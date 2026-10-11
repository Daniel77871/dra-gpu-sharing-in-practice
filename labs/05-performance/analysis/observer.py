"""Run via kubectl exec in the GPU Operator driver Pod, in the host chroot."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

FIELDS = ['uuid', 'name', 'memory.total', 'memory.used', 'memory.free', 'utilization.gpu',
          'utilization.memory', 'power.draw', 'clocks.sm', 'clocks.mem', 'compute_mode',
          'temperature.gpu', 'pstate', 'clocks_event_reasons.active']
METRICS = ['gpu_uuid', 'gpu_name', 'vram_total_mib', 'vram_used_mib', 'vram_free_mib', 'utilization_gpu',
           'memory_io_utilization', 'power_w', 'sm_clock_mhz', 'memory_clock_mhz', 'compute_mode',
           'temperature_c', 'pstate', 'clock_event_reasons']


def domain():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + '/' + os.readlink('/proc/self/ns/time')


def process_identity(pid, proc=Path('/proc')):
    """Map an NVML host PID to its worker PID and Pod/run identity."""
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError('invalid host PID')
    root = proc / str(pid)
    status = dict(line.split(':', 1) for line in (root / 'status').read_text().splitlines() if ':' in line)
    environment = dict(entry.split('=', 1) for entry in (root / 'environ').read_bytes().decode().split('\0') if '=' in entry)
    return {'host_pid': pid, 'namespace_pids': [int(value) for value in status['NSpid'].split()],
            'pid_namespace': os.readlink(root / 'ns/pid'), 'uid': int(status['Uid'].split()[0]),
            'command': (root / 'cmdline').read_bytes().decode().rstrip('\0').split('\0'),
            'env': {key: environment[key] for key in ('POD_NAME', 'POD_UID', 'EXPERIMENT_RUN') if key in environment}}


def runtime():
    root = Path('/run/nvidia/driver')
    env = dict(os.environ)
    if env.get('LD_PRELOAD') or env.get('CUDA_DEVICE_MEMORY_SHARED_CACHE') or 'vgpu' in env.get('LD_LIBRARY_PATH', ''):
        raise ValueError('observer has CUDA runtime injection')
    binary, library = root / 'usr/bin/nvidia-smi', root / 'usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1'
    if not binary.is_file() or not library.is_file():
        raise ValueError('GPU Operator host nvidia-smi/NVML missing')
    env['LD_LIBRARY_PATH'] = str(library.parent) + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    return str(binary), env, {'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                              'nvml_sha256': hashlib.sha256(library.read_bytes()).hexdigest()}


def parse(raw, uuid, now):
    values = next(csv.reader(io.StringIO(raw)))
    if len(values) != len(FIELDS) or values[0].strip() != uuid:
        raise ValueError('GPU UUID or telemetry columns differ')
    result = {'monotonic_ns': now, 'raw': raw.rstrip('\n')}
    for key, value in zip(METRICS, values):
        value = value.strip()
        if key in ('gpu_uuid', 'gpu_name', 'compute_mode', 'pstate'):
            result[key] = value
        elif value in ('N/A', '[N/A]', 'Not Supported', ''):
            result[key] = None
        elif key == 'clock_event_reasons':
            result[key] = int(value, 0)
        else:
            result[key] = float(value)
    return result


def main():
    action, uuid, nonce = sys.argv[1:4]
    if not re.fullmatch(r'[a-f0-9]{32}', nonce):
        raise ValueError('invalid observer nonce')
    stop = Path('/tmp/dra-book-performance-' + nonce + '.stop')
    if action == 'clock':
        print(json.dumps({'clock_domain': domain(), 'monotonic_ns': time.monotonic_ns()}))
        return
    if action == 'processes':
        print(json.dumps([process_identity(pid) for pid in json.loads(sys.argv[4])]))
        return
    if action == 'stop':
        if stop.is_symlink():
            raise ValueError('symlink observer stop file')
        stop.write_text(nonce)
        return
    if action != 'observe' or stop.exists():
        raise ValueError('observer already stopped or invalid action')
    binary, env, identity = runtime()
    command = [binary, '--id=' + uuid, '--query-gpu=' + ','.join(FIELDS), '--format=csv,noheader,nounits', '-lms', '500']
    count, error, process = 0, None, None
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=sys.stderr, text=True, env=env)
        print(json.dumps({'event': 'OBSERVER_READY', 'clock_domain': domain(), 'command': command, **identity}), flush=True)
        deadline = time.monotonic() + 1200
        for line in process.stdout:
            print(json.dumps(parse(line, uuid, time.monotonic_ns())), flush=True)
            count += 1
            if stop.exists() and stop.read_text() == nonce:
                break
            if time.monotonic() > deadline:
                raise ValueError('observer deadline')
        if process.poll() not in (None, 0):
            raise ValueError('nvidia-smi failed')
    except Exception as exc:
        error = str(exc)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        stop.unlink(missing_ok=True)
        print(json.dumps({'event': 'OBSERVER_FINAL', 'clock_domain': domain(), 'samples': count,
                          'sampling_interval_ms': 500, 'error': error}), flush=True)
    if error:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
