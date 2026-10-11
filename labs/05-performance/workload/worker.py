"""One CUDA client. Fixed work; correctness runs only after every client END."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import traceback

try:
    from . import policy
except ImportError:
    import policy


def atomic(path, value):
    if path.is_symlink() or path.with_suffix('.tmp').is_symlink():
        raise RuntimeError('symlinked worker control file')
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value) + '\n'); os.replace(tmp, path)


def clock_domain():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + '/' + os.readlink('/proc/self/ns/time')


def sleep_until(deadline_ns, clock=time):
    while True:
        remaining = deadline_ns - clock.monotonic_ns()
        if remaining <= 0:
            return
        clock.sleep(min(.01, remaining / 1e9))


def warmup(gpu):
    for _ in range(10):
        gpu.gemm()
    gpu.synchronize()


def execute(gpu, cfg, release_ns, domain, clock=time, emit=lambda *a, **k: None):
    sleep_until(release_ns, clock)
    start = clock.monotonic_ns()
    emit('BENCHMARK_START', start_ns=start, release_ns=release_ns, prefix_sleep_ns=cfg['prefix_sleep_ns'])
    if cfg['prefix_sleep_ns']:
        clock.sleep(cfg['prefix_sleep_ns'] / 1e9)
    completed, bursts = 0, []
    bursty = cfg['workload_id'] != 'continuous'
    for index in range(cfg['iterations']):
        before = clock.monotonic_ns() if bursty or cfg.get('sync_each') else None
        gpu.gemm(); completed += 1
        if bursty or cfg.get('sync_each'):
            gpu.synchronize()
            after = clock.monotonic_ns()
            row = {'index': index, 'start_ns': before, 'end_ns': after}
            bursts.append(row); emit('BURST', **row)
        if bursty:
            delay = cfg['final_sleep_ns'] if index == cfg['iterations'] - 1 else cfg['sleep_ns']
            clock.sleep(delay / 1e9)
    gpu.synchronize()
    end = clock.monotonic_ns()
    result = {'task_id': cfg['task_id'], 'start_ns': start, 'end_ns': end,
              'release_ns': release_ns, 'clock_domain': domain,
              'completed_gemms': completed, 'expected_gemms': cfg['iterations'], 'bursts': bursts}
    emit('BENCHMARK_END', **result)
    return result


class TorchGPU:
    def __init__(self, cfg):
        import torch
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RuntimeError('one CUDA device required')
        if os.environ.get('PYTORCH_NO_CUDA_MEMORY_CACHING') == '1':
            raise RuntimeError('OOM uncached configuration must not leak into performance')
        torch.set_num_threads(1); torch.set_num_interop_threads(1)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.set_float32_matmul_precision('highest')
        torch.manual_seed(cfg.get('seed', policy.SEED)); torch.cuda.manual_seed_all(cfg.get('seed', policy.SEED))
        self.torch = torch; self.shape = cfg['shape']
        self.a = torch.randn((self.shape, self.shape), dtype=torch.float32, device='cuda')
        self.b = torch.randn((self.shape, self.shape), dtype=torch.float32, device='cuda')
        self.c = torch.empty_like(self.a)
        raw = subprocess.check_output(['nvidia-smi', '--query-gpu=uuid,name', '--format=csv,noheader,nounits'], text=True)
        rows = [line.strip() for line in raw.splitlines() if line.startswith('GPU-')]
        if len(rows) != 1 or rows[0].split(',')[0].strip() != policy.UUID:
            raise RuntimeError('worker NVIDIA UUID mismatch: ' + raw)
        self.uuid = rows[0].split(',')[0].strip()

    def gemm(self):
        self.torch.mm(self.a, self.b, out=self.c)

    def synchronize(self):
        self.torch.cuda.synchronize()

    def info(self):
        t = self.torch
        return {'gpu_uuid': self.uuid, 'torch_version': t.__version__, 'cuda_runtime': t.version.cuda,
                'dtype': 'float32', 'tf32': t.backends.cuda.matmul.allow_tf32,
                'num_threads': t.get_num_threads(), 'num_interop_threads': t.get_num_interop_threads(),
                'allocated_bytes': t.cuda.memory_allocated(), 'reserved_bytes': t.cuda.memory_reserved()}

    def check(self):
        # All clients are already at END before the controller permits this.
        points = [(0, 0), (0, self.shape - 1), (self.shape - 1, 0), (self.shape - 1, self.shape - 1),
                  (self.shape // 2, self.shape // 2), (1, 1), (2, 3), (3, 2)]
        checks = []
        for row, col in points:
            expected = float(self.torch.dot(self.a[row, :].cpu().double(), self.b[:, col].cpu().double()))
            actual = float(self.c[row, col].item())
            ok = abs(actual - expected) <= 1e-3 + 1e-3 * abs(expected)
            checks.append({'row': row, 'column': col, 'expected_float64': expected, 'actual': actual, 'ok': ok})
        self.checks = checks
        return all(row['ok'] for row in checks)

    def release(self):
        self.synchronize(); self.a = self.b = self.c = None
        self.torch.cuda.empty_cache(); self.synchronize()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True); parser.add_argument('--control-dir', required=True)
    args = parser.parse_args(); cfg = json.loads(args.config); root = Path(args.control_dir)
    owner = json.loads((root / 'owner.json').read_text())
    if owner['run_id'] != os.environ['EXPERIMENT_RUN'] or owner['task_id'] != cfg['task_id']:
        raise RuntimeError('worker owner mismatch')
    inode = root.stat()
    if (inode.st_dev, inode.st_ino, inode.st_uid) != (owner['dev'], owner['ino'], owner['uid']):
        raise RuntimeError('worker control inode mismatch')
    identity = {'run_id': owner['run_id'], 'task_id': cfg['task_id'], 'pid': os.getpid(), 'uid': os.getuid(),
                'pod_name': os.environ['POD_NAME'], 'pod_uid': os.environ['POD_UID'],
                'config_sha256': policy.digest(cfg), 'clock_domain': clock_domain()}
    def emit(event, **fields):
        print(json.dumps(dict(identity, event=event, observed_ns=time.monotonic_ns(), **fields)), flush=True)
    gpu, sequence, ended, correct = None, 0, None, False
    try:
        gpu = TorchGPU(cfg); warmup(gpu)
        runtime = {k: v for k, v in os.environ.items() if k.startswith(('CUDA_', 'NVIDIA_', 'LD_', 'HAMI_'))}
        extra = {}
        if runtime.get('CUDA_DEVICE_MEMORY_SHARED_CACHE'):
            cache = Path(runtime['CUDA_DEVICE_MEMORY_SHARED_CACHE']); info = cache.stat()
            lib = Path('/usr/local/vgpu/libvgpu.so')
            maps = Path('/proc/self/maps').read_text()
            extra = {'cache_path': str(cache), 'cache_dev': info.st_dev, 'cache_inode': info.st_ino,
                     'library_sha256': hashlib.sha256(lib.read_bytes()).hexdigest(),
                     'library_map_lines': [r for r in maps.splitlines() if 'libvgpu.so' in r],
                     'cache_map_lines': [r for r in maps.splitlines() if '.cache' in r]}
        ready = dict(identity, ready_ns=time.monotonic_ns(), config=cfg, env=runtime, **gpu.info(), **extra)
        atomic(root / 'ready.json', ready); emit('READY', **{k: v for k, v in ready.items() if k not in identity})
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            path = root / 'command.json'
            if not path.exists():
                time.sleep(.01); continue
            if path.is_symlink():
                raise RuntimeError('symlinked command')
            command = json.loads(path.read_text())
            if command['seq'] <= sequence:
                time.sleep(.01); continue
            if command['run_id'] != owner['run_id'] or command['seq'] != sequence + 1:
                raise RuntimeError('worker control sequence mismatch')
            if command.get('clock_domain', identity['clock_domain']) != identity['clock_domain']:
                raise RuntimeError('worker/controller clock-domain mismatch')
            sequence = command['seq']; operation = command['operation']
            if operation == 'start':
                atomic(root / 'armed.json', dict(identity, command=command, armed_ns=time.monotonic_ns()))
                ended = execute(gpu, cfg, command['release_ns'], identity['clock_domain'], emit=emit)
                atomic(root / 'end.json', dict(identity, **{k: v for k, v in ended.items() if k not in identity}))
            elif operation == 'verify':
                if ended is None:
                    raise RuntimeError('verification before END')
                correct = gpu.check()
                result = dict(identity, **{k: v for k, v in ended.items() if k not in identity},
                              correct=correct, correctness_samples=gpu.checks,
                              validation_started_after_end=True, validation_end_ns=time.monotonic_ns(), **gpu.info())
                atomic(root / 'verified.json', result); emit('VERIFIED', correct=correct)
            elif operation == 'stop':
                gpu.release(); gpu = None
                atomic(root / 'exited.json', dict(identity, released_ns=time.monotonic_ns(), correct=correct))
                emit('RELEASED', correct=correct)
                return 0 if correct else (0 if ended is None else 2)
            else:
                raise RuntimeError('unknown worker operation')
        raise RuntimeError('worker command deadline')
    except Exception as exc:
        result = dict(identity, error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
        atomic(root / 'error.json', result); emit('WORKER_ERROR', **{k: v for k, v in result.items() if k not in identity})
        return 2
    finally:
        if gpu is not None:
            gpu.release()


if __name__ == '__main__':
    raise SystemExit(main())
