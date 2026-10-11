#!/usr/bin/env python3
"""Summarize GPU placements, non-atomic capacity samples and claim lifecycles."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path

MIB = 1024 ** 2


def read_json(path):
    return json.loads(Path(path).read_text())


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def e4_acceptance(trials):
    groups = {}
    for trial in trials:
        groups.setdefault((trial['stage'], trial['case']), []).append(trial)
    rows = []
    for (stage, case), values in sorted(groups.items()):
        positive = [t for t in values if t['valid'] and t['status'] == 'verified']
        negative = [t for t in values if t['status'] == 'expected-pending'
                    and t['negative_assertion_verified'] and t['claim_lifecycle_verified']]
        retained = [t for t in values if t not in positive + negative]
        repeats = sorted({t['repeat'] for t in positive + negative})
        modes = [t['mode'] for t in values if t.get('mode')]
        rows.append({'stage': stage, 'case': case, 'mode': modes[0] if modes else None,
                     'accepted_repeats': repeats, 'positive_trials': len(positive),
                     'negative_trials': len(negative), 'retained_nonaccepted_trials': len(retained),
                     'retained_statuses': sorted({t['status'] for t in retained}),
                     'status': '已驗收三輪' if repeats == [1, 2, 3] else '尚未驗收三輪'})
    return rows


def e4_capacity_ranges(samples):
    groups = {}
    for row in samples:
        if row['physical'] is not None:
            groups.setdefault((row['node'], row['gpu_uuid'], row['allocation_mode']), []).append(row)
    rows = []
    for (node, uuid, mode), values in sorted(groups.items()):
        remaining = [r['remaining_memory_bytes'] for r in values if r['remaining_memory_bytes'] is not None]
        free = [r['physical']['memory_free_mib'] * MIB for r in values]
        differences = [r['remaining_memory_bytes'] - r['physical']['memory_free_mib'] * MIB
                       for r in values if r['remaining_memory_bytes'] is not None]
        rows.append({'node': node, 'gpu_uuid': uuid, 'allocation_mode': mode,
                     'matched_samples': len(values),
                     'schedulable_remaining_range_bytes': [min(remaining), max(remaining)] if remaining else None,
                     'physical_free_range_bytes': [min(free), max(free)],
                     'difference_range_bytes': [min(differences), max(differences)] if differences else None,
                     'maximum_match_gap_seconds': max(abs(r['physical']['capacity_time_delta_seconds']) for r in values)})
    return rows


def primary_attempts(trial):
    return [a for a in trial['attempts'] if a['kfp_run_id'] == trial['kfp_run_id']]


def placements(trials):
    rows = []
    for trial in trials:
        if trial['status'] != 'verified':
            continue
        for attempt in primary_attempts(trial):
            row = {key: attempt.get(key) for key in ('logical_task_id', 'attempt_index', 'node', 'gpu_uuid',
                                                     'pod_uid', 'claim_uid', 'share_id', 'consumed_capacity')}
            row.update({key: trial[key] for key in ('case', 'repeat', 'path', 'requested_memory')})
            rows.append(row)
    return sorted(rows, key=lambda r: (r['case'], r['repeat'], r['logical_task_id'], r['attempt_index'] or 0))


def pending_windows(trials, lifecycles):
    rows = []
    sources = {row['path']: row for row in lifecycles}
    for trial in trials:
        if trial['case'] != 'h3-h4' or trial['status'] != 'verified':
            continue
        source = sources[trial['path']]
        target = primary_attempts(trial)[0]
        task = next(t for t in source['tasks'] if t['pod_uid'] == target['pod_uid'])
        claim = next(c for c in source['claims'] if c['uid'] == target['claim_uid'])
        start = timestamp(trial['pending_started_at'])
        same_pod = target['pod_uid'] == trial['pending_pod_uid'] and target['pod_uid'] in claim['owner_uids']
        same_claim = target['claim_uid'] == trial['pending_claim_uid']
        allocation_seen = any(event['allocated'] for event in claim['events'])
        rows.append({'case': trial['case'], 'repeat': trial['repeat'], 'path': trial['path'],
                     'pending_seconds': trial['pending_duration_seconds'],
                     'pending_started_at': trial['pending_started_at'], 'release_issued_at': trial['release_issued_at'],
                     'allocated_observed_at': task['allocated_observed_at'],
                     'release_offset_seconds': timestamp(trial['release_issued_at']) - start,
                     'allocated_offset_seconds': timestamp(task['allocated_observed_at']) - start,
                     'pod_uid': target['pod_uid'], 'claim_uid': target['claim_uid'],
                     'same_pod_uid': same_pod, 'same_claim_uid': same_claim,
                     'verified': same_pod and same_claim and allocation_seen and trial['offline_valid']})
    return rows


def lifecycle_rows(trials, lifecycles):
    originals = {row['path']: row for row in trials}
    rows = []
    for source in lifecycles:
        if source['case'] not in ('l1', 'l2', 'l3'):
            continue
        trial = originals[source['path']]
        attempts = primary_attempts(trial)
        task_ids = {a['kfp_task_id'] for a in attempts}
        api_tasks = [t for t in source['api_tasks'] if t['task_id'] in task_ids]
        generated = {uid for a in attempts for uid in a['all_generated_claim_uids']}
        deleted = {claim['uid'] for claim in source['claims'] if claim['deleted_observed_at'] is not None}
        cleanup = source['cleanup']
        rows.append({'case': source['case'], 'repeat': source['repeat'], 'path': source['path'],
                     'run_state': source['run']['state'], 'task_states': sorted({t['state'] for t in api_tasks}),
                     'executor_uids': [pod['uid'] for t in api_tasks for pod in t['pods'] if pod['type'] == 'EXECUTOR'],
                     'attempt_indices': [a['attempt_index'] for a in attempts],
                     'pod_uids': [a['pod_uid'] for a in attempts], 'claim_uids': [a['claim_uid'] for a in attempts],
                     'container_exit_codes': [a['actual_container_exit_code'] for a in attempts],
                     'workload_exit_codes': [a['intended_workload_exit_codes'] for a in attempts],
                     'generated_claim_count': len(generated), 'claims_deleted': bool(generated) and generated <= deleted,
                     'claim_count_after': cleanup['claim_count'],
                     'natural_lifecycle_observed': cleanup['natural_lifecycle_observed'],
                     'manual_gpu_cleanup': cleanup['manual_gpu_cleanup']})
    return rows


def capacity_peaks(path):
    with Path(path).open(newline='') as stream:
        samples = list(csv.DictReader(stream))
    groups = {}
    for row in samples:
        if (row['formal_status'] == 'verified' and row['physical_used_mib']
                and row['allocation_mode'] == 'consumable capacity'):
            groups.setdefault((row['case'], row['node']), []).append(row)
    peaks = []
    for (case, node), values in sorted(groups.items()):
        sample = max(values, key=lambda r: (int(r['reserved_memory_bytes']), float(r['physical_used_mib'])))
        remaining = int(sample['remaining_memory_bytes']) / MIB
        free = float(sample['physical_free_mib'])
        peaks.append({'case': case, 'node': node, 'repeat': int(sample['repeat']),
                      'path': sample['path'], 'snapshot': sample['snapshot'], 'gpu_uuid': sample['gpu_uuid'],
                      'reserved_memory_mib': int(sample['reserved_memory_bytes']) / MIB,
                      'remaining_memory_mib': remaining, 'reserved_cores': int(sample['reserved_cores']),
                      'remaining_cores': int(sample['remaining_cores']),
                      'physical_used_mib': float(sample['physical_used_mib']), 'physical_free_mib': free,
                      'physical_free_minus_remaining_mib': free - remaining,
                      'running_tasks': int(sample['running_gpu_tasks_on_node']),
                      'pending_tasks': int(sample['pending_gpu_tasks_total']),
                      'nvml_offset_seconds': float(sample['nvml_offset_seconds']), 'atomic': sample['atomic'] == 'True'})
    return peaks


def analyze(e4_dir, e4b_dir):
    e4, e4b = Path(e4_dir), Path(e4b_dir)
    trials4 = read_json(e4 / 'trials.json')
    trials4b = read_json(e4b / 'trials.json')
    lifecycles = read_json(e4b / 'lifecycle.json')
    verdicts = [{'case': r['case'], 'repeat': r['repeat'], 'path': r['path'],
                 'formal_status': r['status'], 'offline_status': r['offline_status']}
                for r in trials4b]
    return {'e4_acceptance': e4_acceptance(trials4),
            'e4_capacity_ranges': e4_capacity_ranges(read_json(e4 / 'capacity-samples.json')),
            'e4b_placements': placements(trials4b), 'e4b_verdicts': verdicts,
            'e4b_counts': {'formal_verified': sum(r['formal_status'] == 'verified' for r in verdicts),
                           'formal_invalid': sum(r['formal_status'] == 'invalid' for r in verdicts),
                           'offline_verified': sum(r['offline_status'] == 'verified' for r in verdicts)},
            'pending_windows': pending_windows(trials4b, lifecycles),
            'lifecycle': lifecycle_rows(trials4b, lifecycles),
            'e4b_capacity_peak': capacity_peaks(e4b / 'capacity-samples.csv')}


def analyze_run(out_dir):
    """Read trial summaries produced by the run scripts, preserving failed cases."""
    out = Path(out_dir)
    paths = sorted(set(out.glob('trial.json')) | set(out.glob('cases/*/trial.json')) |
                   set(out.glob('*/trial.json')) | set(out.glob('kubeflow/*/trial.json')))
    trials, placement_rows, priority_phases = [], [], []
    for path in paths:
        row = read_json(path)
        row['errors'] = list(row.get('errors', []))
        if row.get('error'):
            row['errors'].append(row['error'])
        if row['case'] == 'prefer-4090':
            phases = row.get('phases', [])
            uid_keys = ('pod_uid', 'claim_uid')
            idle_workers = [value for phase in phases if phase.get('phase') == 'idle'
                            for value in phase.get('placements', [])]
            worker_uids = {value.get(key) for phase in phases for value in phase.get('placements', [])
                           for key in uid_keys if value.get(key)}
            for name, node, request in [('idle', 'dra-4090', 'gpu/rtx4090'),
                                        ('rtx4090-held', 'dra-a40', 'gpu/a40')]:
                matching = [phase for phase in phases if phase.get('phase') == name]
                if len(matching) != 1:
                    row['errors'].append('priority phase evidence is missing or duplicated: ' + name)
                    continue
                phase = matching[0]
                errors = list(phase.get('errors', []))
                if phase.get('error'):
                    errors.append(phase['error'])
                values = phase.get('placements', [])
                if len(values) != 1 or any(value.get('node') != node or value.get('request') != request for value in values):
                    errors.append('priority phase node/subrequest does not match the expected alternative: ' + name)
                if not values or any(not value.get(key) for value in values for key in uid_keys):
                    errors.append('priority worker Pod/Claim UID evidence is missing: ' + name)
                if name == 'rtx4090-held':
                    if any(value.get(key) and value.get(key) == old.get(key)
                           for value in values for old in idle_workers for key in uid_keys):
                        errors.append('priority worker Pod/Claim UID was reused after idle cleanup')
                    holders = phase.get('holder_placements', [])
                    if len(holders) != 1 or any(value.get('node') != 'dra-4090' or value.get('request') != 'gpu' for value in holders):
                        errors.append('RTX 4090 exclusive holder allocation evidence is missing')
                    if not holders or any(not value.get(key) for value in holders for key in uid_keys):
                        errors.append('RTX 4090 holder Pod/Claim UID evidence is missing')
                    if any(value.get(key) in worker_uids for value in holders for key in uid_keys if value.get(key)):
                        errors.append('RTX 4090 holder Pod/Claim UID overlaps a worker allocation')
                if not phase.get('claims_deleted'):
                    errors.append('priority phase Claim deletion evidence is missing: ' + name)
                if phase.get('verdict') not in ('passed', 'verified', 'PASS'):
                    errors.append('priority phase was not verified: ' + name)
                phase['errors'] = errors
                if errors and phase.get('verdict') in ('passed', 'verified', 'PASS'):
                    phase['verdict'] = 'incomplete'
                row['errors'].extend(errors)
                priority_phases.append({'case': row['case'], 'round': row.get('round'), **phase})
                placement_rows.extend({'case': row['case'], 'round': row.get('round'), 'phase': name, **value}
                                      for value in values)
        pending_case = row['case'] in ('h3h4-pending-release', 'h3-h4')
        if pending_case:
            if not row.get('same_claim_uid'):
                row['errors'].append('same Claim UID was not preserved across Pending and allocation')
            if not row.get('same_pod_uid'):
                row['errors'].append('same Pod UID was not preserved across Pending and allocation')
        if row.get('verdict') in ('passed', 'verified', 'PASS') and not row.get('claims_deleted'):
            row['errors'].append('Claim deletion evidence is missing')
        if row['errors'] and row.get('verdict') in ('passed', 'verified', 'PASS'):
            row['verdict'] = 'incomplete'
        trials.append(row)
        if row['case'] != 'prefer-4090':
            placement_rows.extend({'case': row['case'], 'round': row.get('round'), **value}
                                  for value in row.get('placements', []))
    return {'trials': trials, 'placements': placement_rows, 'priority_phases': priority_phases,
            'pending_windows': [r for r in trials if r.get('pending_seconds') is not None],
            'lifecycle': [{'case': r['case'], 'round': r.get('round'),
                           'claims_deleted': r.get('claims_deleted'), 'verdict': r.get('verdict')}
                          for r in trials]}


def main(argv=None):
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--e4', type=Path, default=root / 'data/e4')
    parser.add_argument('--e4b', type=Path, default=root / 'data/e4b')
    parser.add_argument('--out', type=Path, help='summarize one run output directory')
    parser.add_argument('--output', type=Path, help='write JSON instead of stdout')
    args = parser.parse_args(argv)
    result = analyze_run(args.out) if args.out else analyze(args.e4, args.e4b)
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
