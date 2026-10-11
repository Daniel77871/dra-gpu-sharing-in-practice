#!/usr/bin/env python3
"""Summarize task logs, allocation snapshots and physical GPU samples (stdlib)."""
import argparse
import csv
import datetime as dt
import json
from pathlib import Path
import re

GIB, MIB = 1024 ** 3, 1024 ** 2
ERROR_EVENTS = {'CUDA_ERROR', 'CUDA_SYNC_ERROR', 'A_INTEGRITY_ERROR', 'STARTUP_ERROR'}


def timestamp(value):
    value = value.strip().replace('Z', '+00:00')
    if '/' in value:
        value = value.replace('/', '-').replace(' ', 'T', 1)
    parsed = dt.datetime.fromisoformat(value)
    return parsed.replace(tzinfo=dt.timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()


def quantity_bytes(value):
    match = re.fullmatch(r'([0-9.]+)(Ki|Mi|Gi|Ti)?', str(value))
    if not match:
        raise ValueError(f'unsupported memory quantity: {value}')
    return float(match[1]) * {'Ki': 1024, 'Mi': MIB, 'Gi': GIB, 'Ti': 1024 ** 4, None: 1}[match[2]]


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def events(path):
    result = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue  # CUDA may also print non-JSON diagnostics; preserve the log.
            if isinstance(row, dict) and 'event' in row:
                result.append(row)
    return result


def objects(value):
    return value.get('items', []) if isinstance(value, dict) else value or []


def termination(path, role, meta):
    final = load(path / f'{role}-final.json')
    if final is not None:
        containers = final.get('status', {}).get('containerStatuses', [])
        state = next((c for c in containers if c.get('name') == 'workload'), {})
        return state.get('state', {}).get('terminated', {})
    return meta.get('terminations', {}).get(role, {})


def gpu_samples(path):
    source = path / 'gpu-metrics.jsonl'
    if source.exists():
        return [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    source = path / 'gpu.csv'
    if not source.exists():
        return []
    rows = list(csv.reader(source.read_text().splitlines(), skipinitialspace=True))
    if not rows:
        return []
    header = [name.strip().split(' [')[0] for name in rows[0]]
    if header[0] == 'timestamp':
        rows = rows[1:]
    else:
        header = ['timestamp', 'uuid', 'memory.total', 'memory.used', 'memory.free']
    result = []
    for values in rows:
        row = dict(zip(header, (value.strip() for value in values)))
        row['utc'] = row.pop('timestamp')
        for field in ('memory.total', 'memory.used', 'memory.free'):
            if field in row:
                row[field] = row[field].removesuffix(' MiB').strip()
        result.append(row)
    return result


def base_row(path, meta):
    backend = meta.get('backend', 'mps')
    return {'trial': path.name, 'backend': 'consumable-shares' if backend == 'consumable' else backend,
            'case': meta.get('case'), 'repeat': meta.get('repeat'), 'quota_gib': meta.get('quota_gib'),
            'b_max_tensor_gib': None, 'b_tensor_gib_by_client': {}, 'b_outcome': None,
            'cuda_oom_count': None, 'hold_seconds': None, 'hold_probe_ok': None, 'hold_probe_oom': None,
            'a_probe_ok_pressure': None, 'a_probe_oom_pressure': None, 'a_compute_errors': None,
            'a_exit_code': None, 'b_exit_code': None, 'nvml_peak_used_mib': None, 'nvml_min_free_mib': None,
            'nvml_sample_count': None, 'pod_limit': None,
            'published_mib': None, 'reserved_mib_before': None, 'schedulable_remaining_mib_before': None,
            'physical_free_mib_before': None, 'reserved_mib_after': None,
            'schedulable_remaining_mib_after': None, 'third_claim_pending': None,
            'third_claim_allocated_after_release': None, 'pending_messages': '',
            'complete': False, 'incomplete_reason': ''}


def accounting(path, meta, row, problems):
    before = objects(load(path / 'claims-before-release.json'))
    after = objects(load(path / 'claims-after-release.json'))
    slices = objects(load(path / 'resourceslices.json'))
    capacities = [quantity_bytes(device['capacity']['memory']['value']) / MIB
                  for item in slices if item['spec'].get('nodeName') == 'dra-a40'
                  for device in item['spec'].get('devices', [])
                  if 'memory' in device.get('capacity', {})]
    published = capacities[0] if len(capacities) == 1 else None
    def reserved(claims):
        return sum(quantity_bytes(result.get('consumedCapacity', {}).get('memory', '0')) / MIB
                   for claim in claims
                   for result in (claim.get('status', {}).get('allocation') or {}).get('devices', {}).get('results', []))
    def request(claim):
        requests = claim.get('spec', {}).get('devices', {}).get('requests', [])
        return sum(quantity_bytes(r.get('exactly', {}).get('capacity', {}).get('requests', {}).get('memory', '0'))
                   for r in requests)
    third = next((c for c in before if request(c) == 8 * GIB), None)
    third_after = next((c for c in after if third and c['metadata']['name'] == third['metadata']['name']), None)
    snapshot = path / 'gpu-during.txt'
    if not snapshot.exists():
        snapshot = path / 'gpu-before.txt'
    physical_free = None
    if snapshot.exists():
        physical_free = float(next(csv.reader(snapshot.read_text().splitlines()))[4].strip())
    recorded_events = objects(load(path / 'events-before-release.json'))
    messages = sorted({e.get('message', '') for e in recorded_events if e.get('reason') == 'FailedScheduling'})
    row.update(published_mib=published, reserved_mib_before=reserved(before) if before else None,
               reserved_mib_after=reserved(after) if after else None, physical_free_mib_before=physical_free,
               third_claim_pending=not bool(third.get('status', {}).get('allocation')) if third else None,
               third_claim_allocated_after_release=bool(third_after.get('status', {}).get('allocation')) if third_after else None,
               pending_messages='; '.join(messages))
    for phase in ('before', 'after'):
        amount = row[f'reserved_mib_{phase}']
        row[f'schedulable_remaining_mib_{phase}'] = published - amount if published is not None and amount is not None else None
    if published is None or not before or not after or physical_free is None:
        problems.append('accounting capacity/claim/physical snapshots missing')
    if not row['third_claim_pending'] or not row['third_claim_allocated_after_release']:
        problems.append('third claim did not move from pending to allocated')
    if not any('cannot allocate all claims' in message for message in messages):
        problems.append('scheduler capacity failure event missing')


def runtime(path, meta, row, problems):
    a, b = events(path / 'task-a.log'), events(path / 'task-b.log')
    if not a or not b:
        problems.append('task logs missing')
    quota = meta.get('quota_bytes', (meta.get('quota_gib') or 0) * GIB)
    row['quota_gib'] = quota / GIB if quota else None
    maximum = {}
    for event in b:
        if 'tensor_bytes' in event:
            client = str(event.get('client_id', 'main'))
            maximum[client] = max(maximum.get(client, 0), event['tensor_bytes'])
    row['b_tensor_gib_by_client'] = {client: amount / GIB for client, amount in sorted(maximum.items())}
    row['b_max_tensor_gib'] = sum(maximum.values()) / GIB if maximum else None
    row['pod_limit'] = 'not_enforced' if quota and sum(maximum.values()) > quota else 'not_proven'
    row['cuda_oom_count'] = sum(e['event'] == 'B_OOM' for e in b)
    row['a_compute_errors'] = sum(e['event'] in ERROR_EVENTS for e in a)
    row['a_probe_ok_pressure'] = sum(e['event'] == 'A_PROBE_OK' and e.get('phase') == 'pressure' for e in a)
    row['a_probe_oom_pressure'] = sum(e['event'] == 'A_PROBE_OOM' and e.get('phase') == 'pressure' for e in a)
    exits = [e for e in b if e['event'] == 'B_EXIT']
    outcomes = {e.get('outcome') for e in exits}
    if 'limit_not_enforced' in outcomes or any(e['event'] == 'B_LIMIT_EXCEEDED' for e in b):
        row['b_outcome'] = 'limit_not_enforced'
    elif 'allocation_oom' in outcomes and row['cuda_oom_count']:
        row['b_outcome'] = 'allocation_oom'
    elif outcomes == {'target_reached'}:
        row['b_outcome'] = 'target_reached'
    else:
        row['b_outcome'] = 'incomplete'
        problems.append('B boundary/target/exit evidence missing')
    terms = {role: termination(path, role, meta) for role in ('survivor', 'pressure')}
    for prefix, role in [('a', 'survivor'), ('b', 'pressure')]:
        term = terms[role]
        row[f'{prefix}_exit_code'] = term.get('exitCode')
        if term.get('exitCode') is None:
            problems.append(f'{role} termination evidence missing')
        elif term.get('exitCode') != 0:
            problems.append(f'{role} did not terminate normally')
        if term.get('reason') == 'OOMKilled':
            problems.append(f'{role} Kubernetes OOMKilled')
            if prefix == 'b':
                row['b_outcome'] = 'kubernetes_oomkilled'
    ready_a = next((e for e in a if e['event'] == 'A_READY'), {})
    uuid = meta.get('expected_gpu_uuid') or ready_a.get('gpu_uuid')
    clients = [e for e in b if e['event'] == 'B_READY' and e.get('gpu_uuid')]
    wanted_clients = 2 if meta.get('case') == 'scope' else 1
    if not uuid or ready_a.get('gpu_uuid') != uuid or len(clients) != wanted_clients or any(e['gpu_uuid'] != uuid for e in clients):
        problems.append('CUDA GPU identity evidence missing or inconsistent')
    if row['a_compute_errors'] or any(e['event'] in ERROR_EVENTS for e in b):
        problems.append('CUDA compute/startup error observed')
    if any(e['event'] in ('A_TIMEOUT', 'B_TIMEOUT', 'B_START_TIMEOUT', 'B_ABORTED') for e in a + b):
        problems.append('workload timeout/abort observed')
    for phase in ('baseline', 'recovery'):
        wanted = meta.get(f'{phase}_seconds', 60)
        if meta.get(f'{phase}_elapsed', 0) < wanted:
            problems.append(f'{phase} observation shorter than {wanted} seconds')
    for phase in ('baseline', 'pressure', 'recovery'):
        observed = {e['event'] for e in a if e.get('phase') == phase}
        if 'A_COMPUTE' not in observed or not observed.intersection({'A_PROBE_OK', 'A_PROBE_OOM'}):
            problems.append(f'A compute/probe samples missing in {phase}')
    if not any(e['event'] == 'A_STOPPED' for e in a):
        problems.append('A synchronized stop evidence missing')
    recovery = meta.get('recovery_start_utc')
    actual_exit = terms['pressure'].get('finishedAt') or meta.get('b_actual_exit_utc')
    if not recovery or not actual_exit or timestamp(recovery) < timestamp(actual_exit):
        problems.append('actual B exit/recovery timing missing or out of order')
    if row['b_outcome'] in ('allocation_oom', 'target_reached'):
        starts, ends = [], []
        for client in maximum:
            rows = [e for e in b if str(e.get('client_id', 'main')) == client]
            hold = [e for e in rows if e['event'] == 'B_HOLD']
            end = [e for e in rows if e['event'] == 'B_HOLD_END']
            if len(hold) != 1 or len(end) != 1:
                problems.append(f'hold markers missing for client {client}')
                break
            starts.append(hold[0]['monotonic']); ends.append(end[0]['monotonic'])
            if ends[-1] - starts[-1] < hold[0].get('hold_seconds', 30) - 0.02:
                problems.append(f'hold interval too short for client {client}')
        else:
            if starts and max(starts) < min(ends):
                start, end = max(starts), min(ends)
                held = [e for e in a if start < e.get('monotonic', -1) < end]
                row['hold_seconds'] = end - start
                row['hold_probe_ok'] = sum(e['event'] == 'A_PROBE_OK' for e in held)
                row['hold_probe_oom'] = sum(e['event'] == 'A_PROBE_OOM' for e in held)
                if not row['hold_probe_ok'] and not row['hold_probe_oom']:
                    problems.append('no A probes inside hold interval')
            else:
                problems.append('hold intervals do not overlap')
    grows = [e for e in b if e['event'] == 'B_GROW']
    gpu = gpu_samples(path)
    if grows and exits:
        start = min(timestamp(e['utc']) for e in grows)
        end = min(timestamp(e['utc']) for e in exits)
        samples = [s for s in gpu if not s.get('error') and s.get('uuid') == uuid
                   and start <= timestamp(s['utc']) and timestamp(s.get('finished_utc', s['utc'])) <= end]
        if samples:
            row['nvml_peak_used_mib'] = max(float(s['memory.used']) for s in samples)
            row['nvml_min_free_mib'] = min(float(s['memory.free']) for s in samples)
            row['nvml_sample_count'] = len(samples)
        else:
            problems.append('NVML samples fully inside B interval missing')
    else:
        problems.append('B active interval missing')
    if any(s.get('error') or s.get('uuid') != uuid for s in gpu):
        problems.append('GPU sample error or UUID mismatch')


def analyze_round(path):
    path = Path(path)
    meta = load(path / 'trial.json', {})
    row = base_row(path, meta)
    problems = []
    if meta.get('case') == 'accounting':
        accounting(path, meta, row, problems)
    else:
        runtime(path, meta, row, problems)
    if meta.get('collection_errors'):
        problems.append('evidence collection failed')
    if meta.get('error') or (path / 'run-error.txt').exists():
        problems.append('runner failed or interrupted: ' + meta.get('error', 'see run-error.txt'))
    if not meta.get('cleanup', {}).get('verified'):
        problems.append('cleanup not verified')
    row['complete'] = not problems
    row['incomplete_reason'] = '; '.join(problems)
    return row


def analyze_run(path):
    path = Path(path)
    if (path / 'trial.json').exists():
        return [analyze_round(path)]
    rows = [analyze_round(item) for item in sorted(path.iterdir())
            if item.is_dir() and (item / 'trial.json').exists()]
    if not rows:
        raise ValueError('no round directories with trial.json found')
    return rows


def write_summaries(rows, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'summary.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    with (output / 'summary.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else value
                          for key, value in row.items()} for row in rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--quiet', action='store_true', help='write summaries without printing round verdicts')
    args = parser.parse_args(argv)
    try:
        rows = analyze_run(args.results)
        write_summaries(rows, args.output or args.results)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f'Analysis failed: {error}\n')
    if not args.quiet:
        for row in rows:
            print(f"{row['trial']}: {row['b_outcome'] or 'accounting'}; complete={row['complete']}")
            if not row['complete']:
                print(row['incomplete_reason'])
    return 0 if all(row['complete'] for row in rows) else 2


if __name__ == '__main__':
    raise SystemExit(main())
