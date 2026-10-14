#!/usr/bin/env python3
"""Recompute HAMi accounting, process usage and cache-scope evidence (stdlib)."""
import argparse
import csv
import importlib.util
import json
import os
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('hami_common_analysis', Path(__file__).with_name('analyze.py'))
common = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(common)
GIB, MIB = common.GIB, common.MIB
SCOPE_CASES = {'case3', 'case4a', 'case4b'}


def log_events(path):
    """kubectl --timestamps logs and plain JSON logs carry the same events."""
    result = []
    if path.exists():
        for line in path.read_text().splitlines():
            start = line.find('{')
            if start < 0:
                continue
            try:
                value = json.loads(line[start:])
            except ValueError:
                continue
            if isinstance(value, dict) and 'event' in value:
                result.append(value)
    return result


def cache_identity(clients):
    paths = [c.get('cache_path') or c.get('shared_cache') or c.get('env', {}).get('CUDA_DEVICE_MEMORY_SHARED_CACHE')
             for c in clients]
    identities = [(c.get('cache_dev'), c.get('cache_inode')) for c in clients]
    complete = bool(clients) and all(path is not None for path in paths) and all(None not in item for item in identities)
    return {'cache_paths': paths, 'cache_identities': [list(item) for item in identities],
            'cache_path_equal': len(set(paths)) == 1 if paths and all(paths) else None,
            'cache_inode_equal': len(set(identities)) == 1 if complete else None,
            'cache_identity_complete': complete}


def linux_makedev(major, minor):
    """Encode a Linux dev_t as glibc does. The maps lines come from Linux nodes, so os.makedev (platform-specific;
    macOS packs major and minor differently) would make the summary depend on where the analysis runs."""
    return (((major & 0xfffff000) << 32) | ((major & 0x00000fff) << 8)
            | ((minor & 0xffffff00) << 12) | (minor & 0x000000ff))


def mapped_cache(maps, path):
    for line in maps.splitlines():
        fields = line.split(None, 5)
        if len(fields) == 6 and fields[5] == path:
            major, minor = (int(part, 16) for part in fields[3].split(':'))
            return {'cache_dev': linux_makedev(major, minor), 'cache_inode': int(fields[4])}
    return {}


def process_samples(raw):
    samples = {}
    for values in csv.reader(raw.splitlines(), skipinitialspace=True):
        if len(values) >= 3:
            try:
                samples[int(values[0])] = float(values[2].strip().removesuffix(' MiB'))
            except ValueError:
                continue
    return samples


def capacity(claims, slices=None):
    allocated = [result for claim in common.objects(claims)
                 for result in (claim.get('status', {}).get('allocation') or {}).get('devices', {}).get('results', [])]
    result = {'reserved_mib': sum(common.quantity_bytes(r.get('consumedCapacity', {}).get('memory', '0')) / MIB
                                  for r in allocated),
              'reserved_cores': sum(float(r.get('consumedCapacity', {}).get('cores', 0)) for r in allocated)}
    if slices is not None:
        devices = [d for s in common.objects(slices) if s['spec'].get('nodeName') == 'dra-a40'
                   for d in s['spec'].get('devices', []) if 'memory' in d.get('capacity', {})]
        result['published_cores'] = float(devices[0]['capacity']['cores']['value']) if len(devices) == 1 else None
        result['published_mib'] = common.quantity_bytes(devices[0]['capacity']['memory']['value']) / MIB if len(devices) == 1 else None
        for amount in ('mib', 'cores'):
            published = result[f'published_{amount}']
            result[f'schedulable_remaining_{amount}'] = published - result[f'reserved_{amount}'] if published is not None else None
    return result


def finish(row, problems):
    row['complete'] = not problems
    row['incomplete_reason'] = '; '.join(dict.fromkeys(p for p in problems if p))
    return row


def analyze_runtime_round(path):
    """Reuse E2 validation; independently add HAMi injected and host evidence."""
    path = Path(path)
    row = common.analyze_round(path)
    problems = [row['incomplete_reason']] if not row['complete'] else []
    slices = common.load(path / 'resourceslices.json', {'items': []})
    if row['case'] == 'accounting':
        for phase, name in [('before', 'claims-before-release.json'), ('after', 'claims-after-release.json')]:
            amounts = capacity(common.load(path / name, []), slices)
            row['published_cores'] = amounts['published_cores']
            for key in ('reserved_cores', 'schedulable_remaining_cores'):
                row[f'{key}_{phase}'] = amounts[key]
        if row['published_cores'] is None:
            problems.append('published cores missing')
        return finish(row, problems)
    row.update(capacity(common.load(path / 'claims-allocated.json', []), slices))
    if row['published_cores'] is None:
        problems.append('published cores missing')
    hooks = common.load(path / 'runtime-hooks.json', {})
    mappings = common.load(path / 'client-identity.json', [])
    process_info = common.load(path / 'client-processes.json', {})
    ready = [r for r in log_events(path / 'task-b.log') if r['event'] == 'B_READY' and r.get('gpu_uuid')]
    pressure = [item for item in mappings if any(
        item.get('pod', item.get('pod_name')) == r.get('pod_name') and
        str(item.get('client_id', 'main')) == str(r.get('client_id', 'main')) for r in ready)]
    raw = (path / 'processes-during.txt').read_text() if (path / 'processes-during.txt').exists() else ''
    physical = process_samples(raw)
    clients, per_client = [], {}
    for item in pressure:
        matches = [r for r in ready if str(r.get('client_id', 'main')) == str(item.get('client_id', 'main'))
                   and r.get('pid') == item.get('namespace_pid') and
                   r.get('pod_name') == item.get('pod', item.get('pod_name'))]
        if len(matches) != 1:
            problems.append('pressure host PID identity missing or inconsistent')
            continue
        observed = dict(matches[0])
        hook = hooks.get('pressure', hooks.get('quota-pressure', {}))
        observed.update({k: v for k, v in hook.items() if k in ('env', 'preload', 'library_sha256')})
        proc = process_info.get(str(item['host_pid']), item)
        maps = proc.get('maps', '\n'.join(observed.get('library_map_lines', []) + observed.get('cache_map_lines', [])))
        cache = observed.get('cache_path') or observed.get('shared_cache') or observed.get('env', {}).get('CUDA_DEVICE_MEMORY_SHARED_CACHE')
        observed['cache_path'] = cache
        observed.update(mapped_cache(maps, cache))
        observed['libvgpu_present'] = '/usr/local/vgpu/libvgpu.so' in maps or bool(observed.get('library_map_lines'))
        clients.append(observed)
        if item['host_pid'] in physical:
            per_client[str(item.get('client_id', 'main'))] = physical[item['host_pid']]
    row.update(cache_identity(clients))
    row['libvgpu_present'] = bool(clients) and all(c.get('libvgpu_present') for c in clients)
    row['library_sha256'] = sorted({c['library_sha256'] for c in clients if c.get('library_sha256')})
    row['injected_env_by_client'] = {str(c.get('client_id', 'main')): c.get('env', {}) for c in clients}
    row['b_nvml_mib_by_client'] = per_client
    row['b_nvml_used_mib'] = sum(per_client.values()) if len(per_client) == len(ready) and ready else None
    if len(clients) != len(ready) or not ready:
        problems.append('HAMi CUDA client identity missing')
    if not row['libvgpu_present']:
        problems.append('libvgpu process map evidence missing')
    if not row['cache_identity_complete']:
        problems.append('cache device/inode evidence missing')
    if row['b_nvml_used_mib'] is None:
        problems.append('B physical process samples missing')
    expected_limit = f'{int(row["quota_gib"] * 1024)}m' if row['quota_gib'] else None
    if any(c.get('env', {}).get('CUDA_DEVICE_MEMORY_LIMIT_0') != expected_limit or
           c.get('env', {}).get('CUDA_DEVICE_SM_LIMIT_0') != '50' or
           c.get('env', {}).get('NVIDIA_VISIBLE_DEVICES') != 'void' for c in clients):
        problems.append('injected quota/SM/device environment missing or inconsistent')
    return finish(row, problems)


def container_exits(value):
    """Accept raw Pod snapshots or explicitly collected container states."""
    result = []
    for item in common.objects(value):
        if 'exitCode' in item:
            result.append(item)
        else:
            for container in item.get('status', {}).get('containerStatuses', []):
                terminated = container.get('state', {}).get('terminated')
                if terminated:
                    result.append({'pod': item.get('metadata', {}).get('name'), 'container': container.get('name'), **terminated})
    return result


def scope_snapshot(value, expected_clients):
    clients = value.get('clients', [])
    ids = {str(c.get('client_id')) for c in clients}
    if ids != expected_clients or any(c.get('tensor_bytes') is None for c in clients):
        return None
    physical = process_samples(value.get('processes_raw', ''))
    usage = {str(c['client_id']): physical.get(c.get('host_pid'), c.get('nvml_used_mib')) for c in clients}
    usage = {k: v for k, v in usage.items() if v is not None}
    return {'round_id': value['round_id'], 'tensor_gib_by_client': {str(c['client_id']): c['tensor_bytes'] / GIB for c in clients},
            'tensor_total_gib': sum(c['tensor_bytes'] for c in clients) / GIB,
            'nvml_mib_by_client': usage,
            'nvml_process_total_mib': sum(usage.values()) if set(usage) == expected_clients else None,
            'states': {str(c['client_id']): c.get('status') for c in clients}, 'clients': clients}


def analyze_scope_round(path):
    path = Path(path)
    meta = common.load(path / 'trial.json', {})
    identity = common.load(path / 'identity.json', {})
    ready, mappings = identity.get('ready', []), identity.get('clients', [])
    problems = []
    row = {'trial': path.name, 'backend': 'hami', 'case': meta.get('case'), 'repeat': meta.get('repeat'),
           'quota_gib': meta.get('quota_gib', 8), 'target_gib_per_client': meta.get('target_bytes_per_client', 0) / GIB,
           'tensor_gib_by_client': {}, 'tensor_total_gib': None, 'cuda_oom_count': None,
           'nvml_mib_by_client': {}, 'nvml_process_total_mib': None, 'quota_exceeded': None,
           'outcome': 'incomplete', 'last_complete_round_id': None,
           'last_barrier_tensor_gib_by_client': {}, 'last_barrier_tensor_total_gib': None,
           'last_barrier_nvml_mib_by_client': {}, 'last_barrier_nvml_process_total_mib': None,
           'recovery_exit_codes': [], 'recovery_exit_reasons': []}
    row.update(cache_identity(ready))
    row.update(capacity(identity.get('claims', [])))
    row['claims_shared'] = len({m.get('claim_uid') for m in mappings}) == 1 if len(mappings) == 2 else None
    row['libvgpu_present'] = len(ready) == 2 and all(any('/usr/local/vgpu/libvgpu.so' in line
                                  for line in c.get('library_map_lines', [])) for c in ready)
    row['library_sha256'] = sorted({c['library_sha256'] for c in ready if c.get('library_sha256')})
    row['injected_env_by_client'] = {str(c['client_id']): c.get('env', {}) for c in ready}
    expected = {'0', '1'}
    if {str(c.get('client_id')) for c in ready} != expected or {str(c.get('client_id')) for c in mappings} != expected:
        problems.append('two-client identity evidence missing')
    if not row['cache_identity_complete'] or not row['libvgpu_present']:
        problems.append('cache device/inode or libvgpu map evidence missing')
    shared = meta.get('case') != 'case4b'
    if row['cache_inode_equal'] is not shared or row['cache_path_equal'] is not shared or row['claims_shared'] is not shared:
        problems.append('Claim/cache topology inconsistent with scope case')
    if len({c.get('gpu_uuid') for c in ready}) != 1 or any(not c.get('gpu_uuid') for c in ready):
        problems.append('CUDA GPU identity missing or inconsistent')
    if any(c.get('env', {}).get('CUDA_DEVICE_MEMORY_LIMIT_0') != '8192m' or
           c.get('env', {}).get('CUDA_DEVICE_SM_LIMIT_0') != '50' or
           c.get('env', {}).get('NVIDIA_VISIBLE_DEVICES') != 'void' for c in ready):
        problems.append('injected scope environment missing or inconsistent')
    values = [common.load(p) for p in sorted(path.glob('round-*.json'))]
    snapshots = [scope_snapshot(v, expected) for v in values]
    observed = [s for s in snapshots if s is not None]
    if [s['round_id'] for s in observed] != list(range(len(observed))):
        problems.append('allocation barrier sequence missing or inconsistent')
    if observed:
        last = observed[-1]
        row['last_complete_round_id'] = last['round_id']
        for field in ('tensor_gib_by_client', 'tensor_total_gib', 'nvml_mib_by_client', 'nvml_process_total_mib'):
            row[f'last_barrier_{field}'] = last[field]
    else:
        last = None
        problems.append('complete allocation barrier missing')
    if any(s is None for s in snapshots):
        problems.append('allocation barrier client evidence missing')
    stalled = bool(meta.get('error') or meta.get('stop_error') or (path / 'run-error.txt').exists())
    if stalled:
        problems.append(meta.get('error', meta.get('stop_error', 'runner failed or interrupted')))
        row['outcome'] = 'stalled' if 'timeout' in '; '.join(problems).lower() else 'incomplete'
    elif last and set(last['states'].values()) <= {'cuda_oom', 'target_reached'}:
        for field in ('tensor_gib_by_client', 'tensor_total_gib', 'nvml_mib_by_client', 'nvml_process_total_mib'):
            row[field] = last[field]
        # Count actual OOM attempts once; later barriers retain the stopped state.
        row['cuda_oom_count'] = sum(c.get('allocation', {}).get('attempted') is True and
                                   c.get('allocation', {}).get('error_type') == 'OutOfMemoryError'
                                   for s in observed for c in s['clients'])
        grouped = {}
        for c in last['clients']:
            grouped[c.get('claim_uid')] = grouped.get(c.get('claim_uid'), 0) + c['tensor_bytes']
        row['quota_exceeded'] = any(n > row['quota_gib'] * GIB for n in grouped.values())
        row['outcome'] = 'shared_allocation_limit_exceeded' if row['quota_exceeded'] else 'allocation_oom' if row['cuda_oom_count'] else 'target_reached'
    else:
        problems.append('final quota/target barrier missing')
    if last and last['nvml_process_total_mib'] is None:
        problems.append('physical process samples missing')
    logs = {client: log_events(path / f'client-{client}.log') for client in expected}
    if not stalled and any(not logs[client] or not any(e['event'] == 'RELEASED' for e in logs[client]) for client in expected):
        problems.append('client release log evidence missing')
    exits = common.load(path / 'terminated-pods.json', common.load(path / 'terminations.json', []))
    exited = container_exits(exits)
    if len(exited) != 2 or any(e.get('exitCode') != 0 or e.get('reason') == 'OOMKilled' for e in exited):
        problems.append('normal container termination evidence missing')
    recovery = container_exits(common.load(path / 'recovery/terminated-pods.json', []))
    row['recovery_exit_codes'] = sorted(e['exitCode'] for e in recovery)
    row['recovery_exit_reasons'] = [e['reason'] for e in sorted(recovery, key=lambda e: e['exitCode'])]
    if not meta.get('cleanup', {}).get('verified'):
        problems.append('cleanup not verified')
    if not stalled:
        start, end = meta.get('hold_started_utc'), meta.get('hold_finished_utc')
        if not start or not end or common.timestamp(end) - common.timestamp(start) < meta.get('hold_seconds', 30) - .02:
            problems.append('joint hold interval missing or too short')
    return finish(row, problems)


def analyze_round(path):
    path = Path(path)
    meta = common.load(path / 'trial.json', {})
    return analyze_scope_round(path) if meta.get('case') in SCOPE_CASES else analyze_runtime_round(path)


def analyze_run(path):
    path = Path(path)
    if (path / 'trial.json').exists():
        return [analyze_round(path)]
    rounds = [p for p in sorted(path.iterdir()) if p.is_dir() and (p / 'trial.json').exists()]
    if not rounds:
        raise ValueError('no HAMi round directories with trial.json found')
    return [analyze_round(p) for p in rounds]


def write_summaries(rows, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'summary.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    tables = {'summary.csv': rows}
    if any(r['case'] == 'accounting' for r in rows):
        tables['accounting.csv'] = [r for r in rows if r['case'] == 'accounting']
        tables['runtime.csv'] = [r for r in rows if r['case'] != 'accounting']
    for name, selected in tables.items():
        if not selected:
            continue
        fields = list(dict.fromkeys(key for row in selected for key in row))
        with (output / name).open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows({k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (dict, list)) else v
                              for k, v in row.items()} for row in selected)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--output', '--out', type=Path)
    args = parser.parse_args(argv)
    try:
        rows = analyze_run(args.results)
        write_summaries(rows, args.output or args.results)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f'Analysis failed: {error}\n')
    for row in rows:
        print(f"{row['trial']}: {row.get('outcome', row.get('b_outcome')) or 'accounting'}; complete={row['complete']}")
        if not row['complete']:
            print(row['incomplete_reason'])
    return 0 if all(r['complete'] for r in rows) else 2


if __name__ == '__main__':
    raise SystemExit(main())
