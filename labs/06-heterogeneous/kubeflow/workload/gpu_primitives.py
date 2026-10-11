"""One real CUDA process for Experiment 4; no GPU OOM pressure."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import time
import traceback
import uuid


SHAPE = 4096
SEED = 20261010
WARMUP = 5
ITERATIONS = 20


def format_uuid(value):
    """Format an observed 16-byte CUDA UUID, never CUDA_VISIBLE_DEVICES."""
    raw = bytes(value)
    if len(raw) != 16:
        raise ValueError('CUDA UUID must have 16 bytes')
    return 'GPU-' + str(uuid.UUID(bytes=raw))


def cuda_driver_uuid():
    driver = ctypes.CDLL('libcuda.so.1')
    init = driver.cuInit
    init.argtypes = [ctypes.c_uint]
    get_device = driver.cuDeviceGet
    get_device.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
    get_uuid = getattr(driver, 'cuDeviceGetUuid_v2', None) or driver.cuDeviceGetUuid
    get_uuid.argtypes = [ctypes.c_void_p, ctypes.c_int]
    device = ctypes.c_int()
    raw = (ctypes.c_ubyte * 16)()
    for function, arguments in ((init, (0,)), (get_device, (ctypes.byref(device), 0)),
                                (get_uuid, (ctypes.byref(raw), device))):
        code = function(*arguments)
        if code != 0:
            raise RuntimeError('CUDA UUID observation failed with code ' + str(code))
    return format_uuid(raw)


def runtime_identity():
    keys = ('CUDA_VISIBLE_DEVICES', 'NVIDIA_VISIBLE_DEVICES', 'CUDA_DEVICE_SM_LIMIT_0',
            'CUDA_DEVICE_MEMORY_LIMIT_0', 'CUDA_DEVICE_MEMORY_SHARED_CACHE',
            'CUDA_DEVICE_MEMORY_CORE_UTILIZATION', 'LD_PRELOAD')
    result = {'env': {key: os.environ[key] for key in keys if key in os.environ}}
    cache_name = result['env'].get('CUDA_DEVICE_MEMORY_SHARED_CACHE')
    if cache_name:
        cache = Path(cache_name)
        stat = cache.stat()
        library = Path('/usr/local/vgpu/libvgpu.so')
        maps = Path('/proc/self/maps').read_text()
        result.update(cache_path=str(cache), cache_dev=stat.st_dev, cache_inode=stat.st_ino,
                      library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
                      library_map_lines=[line for line in maps.splitlines() if 'libvgpu.so' in line],
                      cache_map_lines=[line for line in maps.splitlines() if str(cache) in line])
    return result


class TorchGPU:
    def __init__(self):
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('torch.cuda.is_available() is false')
        if torch.cuda.device_count() != 1:
            raise RuntimeError('exactly one CUDA-visible GPU required')
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.set_float32_matmul_precision('highest')
        torch.manual_seed(SEED)
        torch.cuda.manual_seed_all(SEED)
        self.torch = torch
        self.a = torch.randn((SHAPE, SHAPE), dtype=torch.float32, device='cuda')
        self.b = torch.randn((SHAPE, SHAPE), dtype=torch.float32, device='cuda')
        self.c = torch.empty_like(self.a)
        properties = torch.cuda.get_device_properties(0)
        observed = getattr(properties, 'uuid', None)
        text = str(observed) if observed is not None else ''
        if text.startswith('GPU-'):
            self.uuid = 'GPU-' + str(uuid.UUID(text[4:]))
        elif isinstance(observed, (bytes, bytearray)) and len(observed) == 16:
            self.uuid = format_uuid(observed)
        else:
            self.uuid = cuda_driver_uuid()

    def gemm(self):
        self.torch.mm(self.a, self.b, out=self.c)

    def synchronize(self):
        self.torch.cuda.synchronize()

    def info(self):
        torch = self.torch
        return {'cuda_available': torch.cuda.is_available(), 'gpu_uuid': self.uuid,
                'gpu_name': torch.cuda.get_device_name(0), 'torch_version': str(torch.__version__),
                'cuda_runtime': str(torch.version.cuda), 'shape': SHAPE, 'dtype': 'float32',
                'tf32': torch.backends.cuda.matmul.allow_tf32,
                'seed': SEED, 'warmup_iterations': WARMUP, 'measured_iterations': ITERATIONS,
                'cpu_threads': torch.get_num_threads(),
                'allocated_bytes': torch.cuda.memory_allocated(),
                'reserved_bytes': torch.cuda.memory_reserved()}

    def check(self):
        points = ((0, 0), (0, SHAPE - 1), (SHAPE - 1, 0), (SHAPE - 1, SHAPE - 1),
                  (SHAPE // 2, SHAPE // 2), (1, 1), (2, 3), (3, 2))
        checks = []
        for row, column in points:
            expected = float(self.torch.dot(self.a[row].cpu().double(), self.b[:, column].cpu().double()))
            actual = float(self.c[row, column].item())
            checks.append({'row': row, 'column': column, 'actual': actual,
                           'expected_float64': expected,
                           'ok': abs(actual - expected) <= 1e-3 + 1e-3 * abs(expected)})
        return checks

    def release(self):
        self.synchronize()
        self.a = self.b = self.c = None
        self.torch.cuda.empty_cache()
        self.synchronize()


def await_release(path, run_id, task_id, timeout, clock=time):
    """Reopen the whole-volume ConfigMap path to observe atomic projection swaps."""
    deadline = clock.monotonic_ns() + round(timeout * 1e9)
    last_error = None
    while clock.monotonic_ns() < deadline:
        try:
            control = json.loads(Path(path).read_text())
            if control.get('run_id') == run_id and control.get('release', {}).get(task_id) is True:
                issued = control.get('issued_at_ns')
                observed = clock.time_ns()
                delta = observed - issued if isinstance(issued, int) else None
                return {'release_id': control.get('release_id'),
                        'control_issued_at_ns': issued,
                        'control_observed_at_ns': observed,
                        'control_clock_delta_ns': delta,
                        'control_projection_delay_ns': delta if delta is not None and delta >= 0 else None,
                        'control_delay_includes_polling': True,
                        'control_delay_clock_assumption': 'cross-node UTC clocks must be checked'}
        except (OSError, ValueError, AttributeError) as exc:
            last_error = str(exc)
        clock.sleep(.25)
    raise TimeoutError('control hold deadline exceeded' + (': ' + last_error if last_error else ''))


def run_task(args, gpu_factory=TorchGPU, clock=time):
    identity = {'run_id': args.run_id, 'task_id': str(args.task_id),
                'pod_uid': os.environ.get('E4_POD_UID'), 'gpu_uuid': None,
                'pid': os.getpid()}
    def emit(event, **fields):
        print(json.dumps(dict(identity, event=event, timestamp_ns=clock.time_ns(),
                              monotonic_ns=clock.monotonic_ns(), **fields)), flush=True)
    gpu = None
    exit_code = 0
    try:
        if not identity['pod_uid']:
            raise RuntimeError('E4_POD_UID must identify the Kubernetes Pod')
        gpu = gpu_factory()
        identity['gpu_uuid'] = gpu.uuid
        for _ in range(WARMUP):
            gpu.gemm()
        gpu.synchronize()
        emit('READY', **{key: value for key, value in gpu.info().items() if key not in identity},
             **runtime_identity())
        gpu.synchronize()
        started = clock.monotonic_ns()
        started_at = clock.time_ns()
        emit('CUDA_START', iterations=ITERATIONS)
        for _ in range(ITERATIONS):
            gpu.gemm()
        gpu.synchronize()
        ended = clock.monotonic_ns()
        ended_at = clock.time_ns()
        checks = gpu.check()
        correct = len(checks) == 8 and all(check['ok'] for check in checks)
        emit('CUDA_END', iterations=ITERATIONS, correct=correct,
             start_monotonic_ns=started, end_monotonic_ns=ended, duration_ns=ended - started,
             cuda_started_at_ns=started_at, cuda_finished_at_ns=ended_at)
        emit('CORRECT', checks=len(checks), correct=correct, correctness_samples=checks)
        if not correct:
            raise RuntimeError('CUDA numerical verification failed')
        emit('HOLD', timeout_seconds=args.hold_timeout,
             allocated_bytes=gpu.info()['allocated_bytes'], reserved_bytes=gpu.info()['reserved_bytes'])
        release = await_release(args.control, args.run_id, str(args.task_id), args.hold_timeout, clock)
        gpu.release()
        gpu = None
        emit('RELEASE', correct=True, **release)
    except Exception as exc:
        exit_code = 2
        emit('FAILED', error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
    finally:
        if gpu is not None:
            try:
                gpu.release()
            except Exception as exc:
                exit_code = 2
                emit('FAILED', error_type=type(exc).__name__, error='GPU release failed: ' + str(exc))
    return exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--control', default='/opt/control/control.json')
    parser.add_argument('--hold-timeout', type=float, default=900)
    args = parser.parse_args(argv)
    if args.hold_timeout <= 0:
        parser.error('--hold-timeout must be positive')
    return run_task(args)


if __name__ == '__main__':
    raise SystemExit(main())
