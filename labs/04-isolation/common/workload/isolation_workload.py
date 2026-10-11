"""MPS quota workload. The scope parent never constructs a CUDA client."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

try:  # ConfigMap layout on the cluster.
    import base_workload as base
except ModuleNotFoundError:  # Local package layout; E1 stays immutable.
    import workload as base

GIB = 1024 ** 3
MIB = 1024 ** 2
CONTROL = Path('/tmp/isolation-control.json')


def read_control():
    try:
        return json.loads(CONTROL.read_text())
    except FileNotFoundError:
        return {}


def run_pressure(gpu, emit, control, *, quota_bytes, target_bytes=None,
                 block_bytes=256 * MIB, interval=0.2, hold_seconds=30,
                 clock=time):
    blocks = []
    outcome = None
    emit('B_GROW', quota_bytes=quota_bytes, target_bytes=target_bytes)
    try:
        while True:
            if control().get('stop'):
                gpu.synchronize()
                emit('B_ABORTED')
                return 2
            try:
                blocks.append(gpu.allocate(block_bytes))
            except gpu.oom_error as exc:
                gpu.synchronize()
                emit('B_OOM', tensor_bytes=len(blocks) * block_bytes,
                     blocks=len(blocks), error_type=type(exc).__name__,
                     error=str(exc), **gpu.stats())
                outcome = 'allocation_oom'
                break
            total = len(blocks) * block_bytes
            emit('B_ALLOCATE', tensor_bytes=total, blocks=len(blocks),
                 **gpu.stats())
            if total > quota_bytes:
                gpu.synchronize()
                emit('B_LIMIT_EXCEEDED', tensor_bytes=total,
                     quota_bytes=quota_bytes)
                emit('B_EXIT', outcome='limit_not_enforced', tensor_bytes=total)
                return 0
            if target_bytes is not None and total >= target_bytes:
                outcome = 'target_reached'
                emit('B_TARGET', tensor_bytes=total)
                break
            clock.sleep(interval)
        gpu.synchronize()
        emit('B_HOLD', hold_seconds=hold_seconds, outcome=outcome,
             tensor_bytes=len(blocks) * block_bytes, **gpu.stats())
        until = clock.monotonic() + hold_seconds
        while clock.monotonic() < until:
            if control().get('stop'):
                gpu.synchronize()
                emit('B_ABORTED')
                return 2
            clock.sleep(min(0.2, until - clock.monotonic()))
        gpu.synchronize()
        emit('B_HOLD_END', tensor_bytes=len(blocks) * block_bytes,
             **gpu.stats())
        emit('B_EXIT', outcome=outcome, tensor_bytes=len(blocks) * block_bytes)
        return 0
    except Exception as exc:
        emit('CUDA_ERROR', error_type=type(exc).__name__, error=str(exc))
        try:
            gpu.synchronize()
        except Exception as sync_error:
            emit('CUDA_SYNC_ERROR', error=str(sync_error))
        return 2


def run_scope(emit, quota_gib):
    children = []
    for index in range(2):
        command = [sys.executable, '-u', str(Path(__file__).resolve()),
                   '--role', 'pressure-target', '--quota-gib', str(quota_gib),
                   '--client-id', str(index)]
        child = subprocess.Popen(command)
        children.append(child)
        emit('SCOPE_CHILD_STARTED', child_pid=child.pid, client_id=str(index))
    codes = [child.wait() for child in children]
    emit('B_SCOPE_EXIT', child_exit_codes=codes, gpu_initialized=False)
    return 0 if codes == [0, 0] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', required=True,
                        choices=['survivor', 'pressure', 'scope', 'pressure-target'])
    parser.add_argument('--quota-gib', type=int, required=True, choices=[4, 8])
    parser.add_argument('--client-id', default='main')
    args = parser.parse_args()
    raw = base.Emitter(args.role)
    def emit(event, **fields):
        raw(event, **dict({'pid': os.getpid(), 'uid': os.getuid(),
                          'client_id': args.client_id,
                          'pod_name': os.environ.get('POD_NAME')}, **fields))
    gpu = None
    try:
        if args.role == 'scope':
            emit('B_READY', gpu_initialized=False)
        else:
            gpu = base.TorchGPU()
            if args.role == 'survivor':
                return base.run_survivor(
                    gpu, emit, read_control, resident_bytes=2 * GIB,
                    probe_bytes=512 * MIB, interval=1, max_seconds=600)
            emit('B_READY', **gpu.info(), **gpu.stats())
        deadline = time.monotonic() + 300
        while not read_control().get('start'):
            if read_control().get('stop') or time.monotonic() > deadline:
                emit('B_START_TIMEOUT')
                return 2
            time.sleep(0.2)
        if args.role == 'scope':
            return run_scope(emit, args.quota_gib)
        return run_pressure(gpu, emit, read_control,
                            quota_bytes=args.quota_gib * GIB,
                            target_bytes=5 * GIB if args.role == 'pressure-target' else None)
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
