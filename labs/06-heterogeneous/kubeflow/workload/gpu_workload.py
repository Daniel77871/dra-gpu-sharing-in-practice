"""Small CUDA work, controlled lifecycle failures, bounded admission quota probe."""
import argparse
import json
import os
from pathlib import Path
import re
import time
import traceback


def await_release(path,run_id,task_id,timeout):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        c=json.loads(Path(path).read_text())
        if c.get('run_id')==run_id and c.get('release',{}).get(str(task_id)) is True:
            return {'release_id':c.get('release_id'),'control_observed_at_ns':time.time_ns()}
        time.sleep(.25)
    raise TimeoutError('Owned control hold deadline exceeded')


def quota_probe(gpu,emit):
    torch=gpu.torch
    gpu.release()
    buffers=[];failure=None;total=0
    try:
        for _ in range(10):
            try:
                b=torch.empty(128*2**20,dtype=torch.uint8,device='cuda');b.fill_(1)
                torch.cuda.synchronize();buffers.append(b);total+=b.numel()*b.element_size()
                free,visible=torch.cuda.mem_get_info()
                emit('QUOTA_ALLOCATION',successful_tensor_bytes=total,
                     cuda_visible_total_bytes=visible,cuda_visible_free_bytes=free,
                     torch_allocated_bytes=torch.cuda.memory_allocated())
            except RuntimeError as e:
                if not (isinstance(e,torch.cuda.OutOfMemoryError) or
                        str(e).startswith('CUDA error: out of memory')):raise
                failure=str(e);emit('QUOTA_REJECTED',successful_tensor_bytes=total,error=str(e));break
    finally:
        buffers.clear();b=None
        torch.cuda.empty_cache();torch.cuda.synchronize()
    oversize_rejected=False
    large=None
    try:
        large=torch.empty(1152*2**20,dtype=torch.uint8,device='cuda');large.fill_(1)
        torch.cuda.synchronize();large=None
    except RuntimeError as e:
        if not (isinstance(e,torch.cuda.OutOfMemoryError) or
                str(e).startswith('CUDA error: out of memory')):raise
        oversize_rejected=True;emit('OVERSIZE_REJECTED',requested_tensor_bytes=1152*2**20,error=str(e))
    finally:large=None;torch.cuda.empty_cache();torch.cuda.synchronize()
    x=torch.tensor([2.,3.],device='cuda');value=float((x*x).sum().item())
    torch.cuda.synchronize();x=None
    if value!=13.:raise RuntimeError('Post-probe CUDA check failed')
    valid=bool(failure and 0<total<=2**30 and oversize_rejected)
    emit('QUOTA_RESULT',quota_bytes=2**30,successful_tensor_bytes=total,
         quota_rejection_observed=failure is not None,oversize_rejected=oversize_rejected,
         post_probe_correct=True,quota_path_verified=valid,
         scope='PyTorch allocation path; not a physical NVML hard limit')
    return valid


def run_task(args,gpu_factory=None,runtime_factory=None,env=None,release_wait=await_release):
    env=os.environ if env is None else env
    if gpu_factory is None:
        from gpu_primitives import TorchGPU
        gpu_factory=TorchGPU
    if runtime_factory is None:
        from gpu_primitives import runtime_identity
        runtime_factory=runtime_identity
    ident={'run_id':args.run_id,'task_id':str(args.task_id),'pod_uid':env.get('E4_POD_UID'),
           'gpu_uuid':None,'pid':os.getpid(),'scenario':args.scenario,'attempt_index':None}
    def emit(event,**fields):
        print(json.dumps(dict(ident,event=event,timestamp_ns=time.time_ns(),monotonic_ns=time.monotonic_ns(),**fields)),flush=True)
    gpu=None;code=0
    try:
        if not ident['pod_uid']:raise RuntimeError('Pod UID missing')
        if args.scenario=='retry':
            text=env.get('E4_EXECUTOR_NODE_NAME')
            match=re.search(r'\.executor\((\d+)\)$',text or '')
            if match:text=match.group(1)
            if text not in ('0','1'):raise RuntimeError('Actual executor annotation index is missing or unsupported')
            ident['attempt_index']=int(text)
            ident['attempt_index_source']='Pod annotation workflows.argoproj.io/node-name'
            ident['executor_node_name']=env.get('E4_EXECUTOR_NODE_NAME')
            ident['framework_retry_index_env']=env.get('KFP_RETRY_INDEX')
        gpu=gpu_factory();ident['gpu_uuid']=gpu.uuid
        for _ in range(5):gpu.gemm()
        gpu.synchronize();emit('READY',**{k:v for k,v in gpu.info().items() if k not in ident},**runtime_factory())
        gpu.synchronize();start=time.monotonic_ns();started=time.time_ns();emit('CUDA_START',iterations=20)
        for _ in range(20):gpu.gemm()
        gpu.synchronize();end=time.monotonic_ns();finished=time.time_ns();checks=gpu.check()
        correct=len(checks)==8 and all(x['ok'] for x in checks)
        emit('CUDA_END',correct=correct,iterations=20,start_monotonic_ns=start,end_monotonic_ns=end,
             duration_ns=end-start,cuda_started_at_ns=started,cuda_finished_at_ns=finished)
        emit('CORRECT',correct=correct,checks=8,correctness_samples=checks)
        if not correct:raise RuntimeError('CUDA numerical verification failed')
        if args.scenario=='controlled_failure' or (args.scenario=='retry' and ident['attempt_index']==0):
            emit('CONTROLLED_FAILURE',intended_workload_exit_code=42,reason='E4B deliberate application failure')
            code=42
        elif args.scenario=='quota':
            if not quota_probe(gpu,emit):code=3
        elif args.scenario=='cancel':
            emit('HOLD',allocated_bytes=gpu.info()['allocated_bytes'],reserved_bytes=gpu.info()['reserved_bytes'])
            until=time.monotonic()+args.hold_timeout
            while time.monotonic()<until:
                gpu.gemm();gpu.synchronize();emit('HEARTBEAT',correct=True);time.sleep(1)
            raise TimeoutError('Cancellation never terminated workload')
        else:
            emit('HOLD',allocated_bytes=gpu.info()['allocated_bytes'],reserved_bytes=gpu.info()['reserved_bytes'])
            release=release_wait(args.control,args.run_id,args.task_id,args.hold_timeout)
            emit('RELEASE',**release)
    except Exception as e:
        code=2;emit('FAILED',error_type=type(e).__name__,error=str(e),traceback=traceback.format_exc())
    finally:
        if gpu is not None:
            try:gpu.release();emit('ALLOCATIONS_RELEASED')
            except Exception as e:code=2;emit('FAILED',error='GPU release failed: '+str(e))
    emit('WORKLOAD_EXIT',intended_workload_exit_code=code)
    return code


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--task-id',required=True);p.add_argument('--run-id',required=True)
    p.add_argument('--scenario',choices=['success','controlled_failure','cancel','retry','quota'],required=True)
    p.add_argument('--control',default='/opt/control/control.json')
    p.add_argument('--hold-timeout',type=float,default=900)
    return run_task(p.parse_args())

if __name__=='__main__':raise SystemExit(main())
