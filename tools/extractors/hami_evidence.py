"""Compact explicitly named HAMi evidence without walking the source tree."""
import hashlib
import importlib.util
import json
from pathlib import Path

from .e2 import claim, event_list, pod


class Evidence:
    def __init__(self, reader, output):
        self.reader, self.output = reader, Path(output)
        self.hashes = {}
        reader.line_ranges = {}

    def read(self, path):
        raw = self.reader.read_bytes(path)
        self.hashes[path] = hashlib.sha256(raw).hexdigest()
        self.reader.line_ranges[path] = len(raw.splitlines()) or 1
        return raw

    def json(self, path):
        return json.loads(self.read(path))

    def write(self, path, value):
        path = self.output / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def copy(self, source, destination):
        path = self.output / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.read(source))

    def finish(self):
        self.write('source-sha256.json', self.hashes)
        location = Path(__file__).resolve().parents[2] / 'labs/04-isolation/common/analysis/hami.py'
        spec = importlib.util.spec_from_file_location('extract_hami_analysis', location)
        analysis = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(analysis)
        analysis.write_summaries(analysis.analyze_run(self.output / 'raw'), self.output)


TRIAL_FIELDS = ('backend', 'case', 'quota_gib', 'quota_bytes', 'repeat', 'namespace', 'run_id',
                'started_utc', 'finished_utc', 'expected_gpu_uuid', 'claim_uid', 'baseline_seconds',
                'recovery_seconds', 'baseline_elapsed', 'recovery_elapsed', 'b_actual_exit_utc',
                'recovery_start_utc', 'phase', 'collection_errors', 'target_bytes_per_client',
                'step_bytes', 'hold_seconds', 'hold_started_utc', 'hold_finished_utc',
                'published_memory', 'error', 'stop_error', 'setup_platform_limit')


def trial(value):
    kept = {k: v for k, v in value.items() if k in TRIAL_FIELDS}
    kept['cleanup'] = {k: v for k, v in value.get('cleanup', {}).items()
                       if k in ('verified', 'namespace_absent', 'all_cuda_containers_exited',
                                'claims_cache_cdi_checkpoint_absent', 'identity_4090_unchanged', 'utc')}
    kept['terminations'] = {role: {k: v for k, v in state.items() if k != 'containerID'}
                            for role, state in value.get('terminations', {}).items()}
    return kept


def compact_proc(value):
    return {'status': '\n'.join(line for line in value.get('status', '').splitlines()
                                if line.startswith(('Name:', 'State:', 'Pid:', 'PPid:', 'Uid:', 'NSpid:'))),
            'cgroup': value.get('cgroup', ''), 'cmdline': value.get('cmdline', ''),
            'maps': '\n'.join(line for line in value.get('maps', '').splitlines()
                              if '/usr/local/vgpu/' in line)}


def identity(value):
    return {'ready': value['ready'], 'clients': value['clients'],
            'claims': [claim(c) for c in value.get('claims', [])],
            'pods': [pod(p) for p in value.get('pods', [])],
            'cache_identities': value.get('cache_identities', {}),
            'proc_fields': {k: compact_proc(v) for k, v in value.get('proc_fields', {}).items()},
            'processes_raw': value.get('processes_raw', '')}


def barrier(value):
    # Drop duplicate precomputed aggregates. The analyzer recomputes from each
    # client's independently recorded allocation and physical process sample.
    kept = {k: v for k, v in value.items() if k in ('round_id', 'sample_started_utc',
            'sample_finished_utc', 'gpu_raw', 'processes_raw')}
    kept['clients'] = [{k: v for k, v in c.items() if k in ('host_pid', 'namespace_pid', 'uid',
                       'pod_name', 'pod_uid', 'container_name', 'container_id', 'client_id',
                       'claim_uid', 'tensor_bytes', 'cuda_total_bytes', 'cuda_free_bytes',
                       'nvml_used_mib', 'status', 'allocation', 'snapshot')}
                      for c in value['clients']]
    return kept
