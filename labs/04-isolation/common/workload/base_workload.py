#!/usr/bin/env python3
"""Single-process CUDA workloads. No Kubernetes credentials are needed here."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time

MIB = 1024 ** 2
CONTROL = Path('/tmp/gpu-oom-control.json')


class Emitter:
    def __init__(self, role, stream=None, monotonic=time.monotonic):
        self.role = role
        self.stream = stream or sys.stdout
        self.monotonic = monotonic

    def __call__(self, event, **fields):
        row = {'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'monotonic': self.monotonic(), 'role': self.role,
               'event': event, **fields}
        print(json.dumps(row, ensure_ascii=False), file=self.stream, flush=True)


class TorchGPU:
    def __init__(self):
        import torch
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RuntimeError('Exactly one CUDA device must be visible')
        if os.environ.get('PYTORCH_NO_CUDA_MEMORY_CACHING') != '1':
            raise RuntimeError('PYTORCH_NO_CUDA_MEMORY_CACHING must be 1')
        self.torch = torch
        self.oom_error = torch.OutOfMemoryError
        row = subprocess.check_output([
            'nvidia-smi', '--query-gpu=uuid,name',
            '--format=csv,noheader,nounits'], text=True).strip().splitlines()
        if len(row) != 1:
            raise RuntimeError('Exactly one GPU UUID must be visible')
        self.uuid, self.name = [value.strip() for value in row[0].split(',', 1)]

    def info(self):
        return {'gpu_uuid': self.uuid, 'gpu_name': self.name,
                'torch_version': self.torch.__version__,
                'cuda_runtime': self.torch.version.cuda,
                'caching_disabled': True, 'cuda_processes': 1}

    def allocate(self, nbytes):
        try:
            tensor = self.torch.empty(nbytes, dtype=self.torch.uint8, device='cuda')
        except RuntimeError as exc:
            # PyTorch 2.5's uncached cudaMalloc raises generic RuntimeError.
            # Normalize only this exact allocation error, never a kernel error.
            if not isinstance(exc, self.oom_error) and str(exc).split('\n', 1)[0] == 'CUDA error: out of memory':
                raise self.oom_error(str(exc)) from exc
            raise
        tensor.zero_()
        self.synchronize()
        return tensor

    def release(self, tensor):
        # The caller also drops its reference. Caching is disabled globally.
        self.synchronize()

    def synchronize(self):
        self.torch.cuda.synchronize()

    def compute(self, tensor):
        tensor.add_(1)
        tensor.sub_(1)
        self.synchronize()
        return all(tensor[index].item() == 0
                   for index in (0, tensor.numel() // 2, tensor.numel() - 1))

    def stats(self):
        free, total = self.torch.cuda.mem_get_info()
        return {'allocated_bytes': self.torch.cuda.memory_allocated(),
                'reserved_bytes': self.torch.cuda.memory_reserved(),
                'device_free_bytes': free, 'device_total_bytes': total}


def read_control():
    try:
        return json.loads(CONTROL.read_text())
    except FileNotFoundError:
        return {}


def run_survivor(gpu, emit, control, *, resident_bytes, probe_bytes,
                 interval, max_seconds, clock=time):
    start = clock.monotonic()
    try:
        resident = gpu.allocate(resident_bytes)
        for _ in range(3):
            if not gpu.compute(resident):
                emit('A_INTEGRITY_ERROR', phase='warmup')
                return 2
        emit('A_READY', **gpu.info(), **gpu.stats())
        command_id = None
        while clock.monotonic() - start < max_seconds:
            command = control()
            phase = command.get('phase', 'baseline')
            if command.get('command_id') != command_id:
                gpu.synchronize()
                command_id = command.get('command_id')
                emit('CONTROL_ACK', command_id=command_id, phase=phase,
                     probe=command.get('probe', True), **gpu.stats())
            if command.get('stop'):
                gpu.synchronize()
                emit('A_STOPPED', phase=phase)
                return 0
            before = clock.monotonic()
            if not gpu.compute(resident):
                emit('A_INTEGRITY_ERROR', phase=phase)
                return 2
            emit('A_COMPUTE', phase=phase,
                 latency_ms=(clock.monotonic() - before) * 1000,
                 **gpu.stats())
            if command.get('probe', True):
                try:
                    probe = gpu.allocate(probe_bytes)
                except gpu.oom_error as exc:
                    emit('A_PROBE_OOM', phase=phase, error=str(exc), **gpu.stats())
                else:
                    gpu.release(probe)
                    del probe
                    emit('A_PROBE_OK', phase=phase, probe_bytes=probe_bytes,
                         **gpu.stats())
            clock.sleep(max(0, interval - (clock.monotonic() - before)))
        emit('A_TIMEOUT')
        return 2
    except Exception as exc:
        emit('CUDA_ERROR', phase='survivor', error_type=type(exc).__name__, error=str(exc))
        return 2


def run_pressure(gpu, emit, control, *, mode, block_bytes, interval,
                 hold_seconds, growth_seconds, clock=time):
    blocks = []
    start = clock.monotonic()
    emit('B_GROW', mode=mode)
    try:
        while clock.monotonic() - start < growth_seconds:
            if control().get('stop'):
                gpu.synchronize()
                emit('B_ABORTED')
                return 2
            try:
                blocks.append(gpu.allocate(block_bytes))
            except gpu.oom_error as exc:
                gpu.synchronize()
                emit('B_OOM', mode=mode, error=str(exc), blocks=len(blocks),
                     tensor_bytes=len(blocks) * block_bytes, **gpu.stats())
                if mode == 'hold':
                    emit('B_HOLD', hold_seconds=hold_seconds, **gpu.stats())
                    until = clock.monotonic() + hold_seconds
                    while clock.monotonic() < until:
                        if control().get('stop'):
                            gpu.synchronize()
                            emit('B_ABORTED')
                            return 2
                        clock.sleep(min(0.2, until - clock.monotonic()))
                gpu.synchronize()
                emit('B_EXIT', expected_exit_code=1, mode=mode)
                # References stay alive until function/process termination.
                return 1
            emit('B_ALLOCATE', blocks=len(blocks),
                 tensor_bytes=len(blocks) * block_bytes, **gpu.stats())
            clock.sleep(interval)
        gpu.synchronize()
        emit('B_TIMEOUT', growth_seconds=growth_seconds)
        return 2
    except Exception as exc:
        emit('CUDA_ERROR', phase='pressure', error_type=type(exc).__name__, error=str(exc))
        return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', required=True, choices=['survivor', 'pressure'])
    parser.add_argument('--oom-mode', choices=['exit', 'hold'], default=os.environ.get('OOM_MODE', 'exit'))
    parser.add_argument('--max-seconds', type=float, default=600)
    args = parser.parse_args()
    emit = Emitter(args.role)
    try:
        gpu = TorchGPU()
        if args.role == 'survivor':
            return run_survivor(gpu, emit, read_control, resident_bytes=1024 * MIB,
                                probe_bytes=512 * MIB, interval=1,
                                max_seconds=args.max_seconds)
        emit('B_READY', **gpu.info(), **gpu.stats())
        deadline = time.monotonic() + args.max_seconds
        while not read_control().get('start'):
            if read_control().get('stop') or time.monotonic() >= deadline:
                emit('B_START_TIMEOUT')
                return 2
            time.sleep(0.2)
        return run_pressure(gpu, emit, read_control, mode=args.oom_mode,
                            block_bytes=256 * MIB, interval=0.2,
                            hold_seconds=30, growth_seconds=120)
    except Exception as exc:
        emit('STARTUP_ERROR', error_type=type(exc).__name__, error=str(exc))
        return 2


if __name__ == '__main__':
    sys.exit(main())
