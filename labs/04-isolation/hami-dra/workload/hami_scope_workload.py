"""E2B CUDA client: fixed6GiB target, external measurement barrier, no8GiB guard."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

try:
    import base_workload as base
except ModuleNotFoundError:
    import workload as base

GIB, MIB = 1024 ** 3, 1024 ** 2


class PressureState:
    def __init__(self, gpu):
        self.gpu, self.blocks = gpu, []
        self.status, self.last_round, self.last_allocation = 'running', -1, None

    @property
    def tensor_bytes(self):
        return len(self.blocks) * 256 * MIB

    def step(self, round_id):
        if round_id == self.last_round:
            return self.last_allocation
        if round_id != self.last_round + 1:
            raise ValueError('allocation round is out of order')
        row = {'round_id': round_id, 'attempted': self.status == 'running',
               'attempt_bytes': 256 * MIB, 'started_ns': time.time_ns()}
        if row['attempted']:
            try:
                block = self.gpu.allocate(256 * MIB)
                self.blocks.append(block)
                if self.tensor_bytes == 6 * GIB:
                    self.status = 'target_reached'
            except self.gpu.oom_error as exc:
                self.gpu.synchronize()
                self.status = 'cuda_oom'
                row.update(error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
        self.gpu.synchronize()
        row.update(finished_ns=time.time_ns(), tensor_bytes=self.tensor_bytes,
                   status=self.status, **self.gpu.stats())
        self.last_round, self.last_allocation = round_id, row
        return row

    def snapshot(self, round_id):
        if round_id != self.last_round:
            raise ValueError('snapshot round is not the completed allocation round')
        self.gpu.synchronize()
        return {'round_id': round_id, 'phase': 'snapshot', 'status': self.status,
                'tensor_bytes': self.tensor_bytes, 'last_allocation': self.last_allocation,
                **self.gpu.stats()}

    def release(self):
        self.gpu.synchronize()
        self.blocks.clear()
        self.gpu.synchronize()


def atomic_json(path, body):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(body) + '\n')
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control-dir', required=True)
    parser.add_argument('--client-id', required=True)
    args = parser.parse_args()
    directory = Path(args.control_dir)
    owner = json.loads((directory / 'owner.json').read_text())
    if owner['run_id'] != os.environ['EXPERIMENT_RUN'] or owner['client_id'] != args.client_id:
        raise RuntimeError('control directory ownership mismatch')
    raw = base.Emitter('e2b')
    identity = {'client_id': args.client_id, 'pid': os.getpid(), 'uid': os.getuid(),
                'pod_name': os.environ['POD_NAME'], 'pod_uid': os.environ['POD_UID'],
                'container_name': os.environ['CONTAINER_NAME'], 'run_id': owner['run_id']}

    def emit(event, **fields):
        raw(event, **dict(identity, **fields))

    state = None
    try:
        gpu = base.TorchGPU()
        state = PressureState(gpu)
        stats = gpu.stats()  # Initialize CUDA before recording actual cache identity.
        cache_path = Path(os.environ['CUDA_DEVICE_MEMORY_SHARED_CACHE'])
        cache = cache_path.stat()
        library = Path('/usr/local/vgpu/libvgpu.so')
        maps = Path('/proc/self/maps').read_text()
        ready = dict(identity, phase='ready', target_bytes=6 * GIB, block_bytes=256 * MIB,
                     cache_path=str(cache_path), cache_dev=cache.st_dev, cache_inode=cache.st_ino,
                     cache_map_lines=[line for line in maps.splitlines() if '.cache' in line],
                     library_map_lines=[line for line in maps.splitlines() if 'libvgpu.so' in line],
                     library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
                     preload=Path('/etc/ld.so.preload').read_text(),
                     env={k: v for k, v in os.environ.items() if k.startswith(('CUDA_', 'NVIDIA_', 'LD_', 'HAMI_'))},
                     **gpu.info(), **stats)
        atomic_json(directory / 'ready.json', ready)
        emit('READY', **{k: v for k, v in ready.items() if k not in identity})
        sequence, deadline = 0, time.monotonic() + 600
        while time.monotonic() < deadline:
            command_path = directory / 'command.json'
            if not command_path.exists():
                time.sleep(0.01); continue
            command = json.loads(command_path.read_text())
            if command['seq'] <= sequence:
                time.sleep(0.01); continue
            if command['run_id'] != owner['run_id'] or command['seq'] != sequence + 1:
                raise ValueError('control sequence/ownership mismatch')
            sequence = command['seq']
            operation = command['operation']
            started_ns = time.time_ns()
            if operation == 'allocate':
                release_ns = command['not_before_ns']
                while time.time_ns() < release_ns:
                    time.sleep(min(0.01, (release_ns - time.time_ns()) / 1e9))
                result = dict(state.step(command['round_id']), phase='allocation')
                emit('ALLOCATION', **result)
            elif operation == 'snapshot':
                result = state.snapshot(command['round_id'])
                current = cache_path.stat()
                result.update(cache_dev=current.st_dev, cache_inode=current.st_ino,
                              cache_map_lines=[line for line in Path('/proc/self/maps').read_text().splitlines() if '.cache' in line])
                emit('SNAPSHOT', **result)
            elif operation == 'stop':
                state.release()
                result = {'phase': 'released', 'tensor_bytes': 0, **gpu.stats()}
                atomic_json(directory / ('ack-%d.json' % sequence), dict(identity, command_seq=sequence, **result))
                emit('RELEASED', **result)
                return 0
            else:
                raise ValueError('unknown control operation')
            atomic_json(directory / ('ack-%d.json' % sequence), dict(identity, command_seq=sequence,
                        sample_started_ns=started_ns, sample_finished_ns=time.time_ns(), **result))
        raise RuntimeError('client control deadline exceeded')
    except Exception as exc:
        error = dict(identity, phase='error', error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
        atomic_json(directory / 'error.json', error)
        emit('CLIENT_ERROR', **{k: v for k, v in error.items() if k not in identity})
        return 2
    finally:
        if state is not None:
            try:
                state.release()
            except Exception as exc:
                emit('CUDA_SYNC_ERROR', error=str(exc))


if __name__ == '__main__':
    sys.exit(main())
