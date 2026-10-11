"""HAMi scope probe. The aggregate guard observes successes; it never caps CUDA."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

try:
    import isolation_workload as fixed
except ModuleNotFoundError:
    from experiments.isolation import workload as fixed

GIB, MIB = fixed.GIB, fixed.MIB


def wait_scope_growth(control, *, clock=time):
    deadline = clock.monotonic() + 120
    while clock.monotonic() < deadline:
        state = control()
        if state.get('stop'):
            return False
        if state.get('scope_grow'):
            return True
        clock.sleep(0.2)
    return False


class SharedQuota:
    def __init__(self, path, quota_bytes):
        self.path, self.quota_bytes = Path(path), quota_bytes

    def record(self, client_id=None, successful_bytes=None):
        with self.path.open('a+') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.seek(0)
            raw = stream.read()
            state = json.loads(raw) if raw else {'clients': {}, 'exceeded': False, 'aggregate_bytes': 0}
            if client_id is not None:
                state['clients'][str(client_id)] = successful_bytes
                state['aggregate_bytes'] = sum(state['clients'].values())
                state['exceeded'] = state['exceeded'] or state['aggregate_bytes'] > self.quota_bytes
                stream.seek(0)
                stream.truncate()
                json.dump(state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            return state


def run_group_pressure(gpu, emit, control, guard, client_id, *, clock=time):
    blocks = []
    block_bytes, target_bytes = 256 * MIB, 5 * GIB
    emit('B_GROW', quota_bytes=guard.quota_bytes, target_bytes=target_bytes, quota_scope='pod_observer')
    def exceeded(state):
        gpu.synchronize()
        emit('B_LIMIT_EXCEEDED', tensor_bytes=len(blocks) * block_bytes,
             aggregate_bytes=state['aggregate_bytes'], quota_bytes=guard.quota_bytes,
             observer_stop=True)
        emit('B_EXIT', outcome='limit_not_enforced', tensor_bytes=len(blocks) * block_bytes,
             aggregate_bytes=state['aggregate_bytes'])
        return 0
    try:
        while True:
            if control().get('stop'):
                gpu.synchronize()
                emit('B_ABORTED')
                return 2
            state = guard.record()
            if state['exceeded']:
                return exceeded(state)
            try:
                blocks.append(gpu.allocate(block_bytes))
            except gpu.oom_error as exc:
                gpu.synchronize()
                emit('B_OOM', tensor_bytes=len(blocks) * block_bytes, blocks=len(blocks),
                     error_type=type(exc).__name__, error=str(exc), **gpu.stats())
                outcome = 'allocation_oom'
                break
            total = len(blocks) * block_bytes
            # Update only after CUDA has actually returned a successful allocation.
            state = guard.record(client_id, total)
            emit('B_ALLOCATE', tensor_bytes=total, blocks=len(blocks),
                 aggregate_bytes=state['aggregate_bytes'], **gpu.stats())
            if state['exceeded']:
                return exceeded(state)
            if total >= target_bytes:
                emit('B_TARGET', tensor_bytes=total)
                outcome = 'target_reached'
                break
            clock.sleep(0.2)
        gpu.synchronize()
        emit('B_HOLD', hold_seconds=30, outcome=outcome,
             tensor_bytes=len(blocks) * block_bytes, **gpu.stats())
        until = clock.monotonic() + 30
        while clock.monotonic() < until:
            if control().get('stop'):
                gpu.synchronize()
                emit('B_ABORTED')
                return 2
            state = guard.record()
            if state['exceeded']:
                return exceeded(state)
            clock.sleep(min(0.2, until - clock.monotonic()))
        gpu.synchronize()
        emit('B_HOLD_END', tensor_bytes=len(blocks) * block_bytes, **gpu.stats())
        emit('B_EXIT', outcome=outcome, tensor_bytes=len(blocks) * block_bytes)
        return 0
    except Exception as exc:
        emit('CUDA_ERROR', error_type=type(exc).__name__, error=str(exc))
        try:
            gpu.synchronize()
        except Exception as sync_error:
            emit('CUDA_SYNC_ERROR', error=str(sync_error))
        return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', required=True, choices=['survivor', 'pressure', 'scope', 'pressure-target'])
    parser.add_argument('--quota-gib', required=True, type=int, choices=[4, 8])
    parser.add_argument('--client-id', default='main')
    parser.add_argument('--shared-quota', default='/tmp/scope-quota.json')
    parser.add_argument('--scope-barrier', action='store_true')
    args = parser.parse_args()
    if args.role in ('survivor', 'pressure'):
        return fixed.main()
    raw = fixed.base.Emitter(args.role)
    def emit(event, **fields):
        raw(event, **dict({'pid': os.getpid(), 'uid': os.getuid(), 'client_id': args.client_id,
                          'pod_name': os.environ.get('POD_NAME')}, **fields))
    gpu = None
    try:
        if args.role == 'scope':
            emit('B_READY', gpu_initialized=False)
            deadline = time.monotonic() + 300
            while not fixed.read_control().get('start'):
                if fixed.read_control().get('stop') or time.monotonic() > deadline:
                    emit('B_START_TIMEOUT')
                    return 2
                time.sleep(0.2)
            children = []
            for index in range(2):
                child = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()),
                                          '--role', 'pressure-target', '--quota-gib', str(args.quota_gib),
                                          '--client-id', str(index), '--shared-quota', args.shared_quota,
                                          '--scope-barrier'])
                children.append(child)
                emit('SCOPE_CHILD_STARTED', child_pid=child.pid, client_id=str(index))
            codes = [child.wait() for child in children]
            emit('B_SCOPE_EXIT', child_exit_codes=codes, gpu_initialized=False)
            return 0 if codes == [0, 0] else 2
        gpu = fixed.base.TorchGPU()
        emit('B_READY', **gpu.info(), **gpu.stats(), shared_cache=os.environ.get('CUDA_DEVICE_MEMORY_SHARED_CACHE'))
        if args.scope_barrier and not wait_scope_growth(fixed.read_control):
            gpu.synchronize()
            emit('B_ABORTED', phase='identity_barrier')
            return 2
        return run_group_pressure(gpu, emit, fixed.read_control,
                                  SharedQuota(args.shared_quota, args.quota_gib * GIB), args.client_id)
    except Exception as exc:
        emit('STARTUP_ERROR', error_type=type(exc).__name__, error=str(exc))
        return 2
    finally:
        if gpu is not None:
            try:
                gpu.synchronize()
            except Exception as exc:
                emit('CUDA_SYNC_ERROR', error=str(exc))


if __name__ == '__main__':
    sys.exit(main())
