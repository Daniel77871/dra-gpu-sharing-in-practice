#!/usr/bin/env python3
"""Recompute E3 fixed-work batches and report tables, using only the stdlib."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

WORKLOADS = ('continuous', 'bursty-aligned', 'bursty-staggered')
MODES = ('exclusive', 'sequential', 'timeslicing', 'mps', 'hami')
FIELDS = ('gpu', 'workload_id', 'mode', 'concurrency', 'repeat', 'block', 'trial_id',
          'makespan_seconds', 'throughput_tasks_per_second', 'mean_task_latency',
          'p50_task_latency', 'p95_task_latency', 'mean_gpu_utilization',
          'p50_gpu_utilization', 'p95_gpu_utilization', 'peak_vram_mib',
          'speedup_vs_sequential', 'parallel_efficiency', 'per_task_slowdown',
          'valid', 'error', 'config_sha256', 'telemetry_samples', 'mean_power_w',
          'mean_sm_clock_mhz', 'mean_memory_clock_mhz', 'continued_after_pause', 'cleanup_recovered')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def describe(values):
    values = sorted(float(v) for v in values)
    if not values:
        raise ValueError('missing metric samples')
    def quantile(q):
        position = (len(values) - 1) * q
        lo, hi = math.floor(position), math.ceil(position)
        return values[lo] + (values[hi] - values[lo]) * (position - lo)
    return {'mean': statistics.mean(values), 'p50': quantile(.5), 'p95': quantile(.95),
            'min': min(values), 'max': max(values),
            'stddev': statistics.stdev(values) if len(values) > 1 else None}


def window_mean(rows, key, windows):
    """Original left-held, duration-weighted NVML integration over window union."""
    rows = sorted(rows, key=lambda row: row['monotonic_ns'])
    total, weight = 0.0, 0
    for i, row in enumerate(rows):
        if row.get(key) is None:
            continue
        end = rows[i + 1]['monotonic_ns'] if i + 1 < len(rows) else max(b for a, b in windows)
        for start, stop in windows:
            overlap = max(0, min(stop, end) - max(start, row['monotonic_ns']))
            total += float(row[key]) * overlap
            weight += overlap
    return total / weight if weight else None


def task_from_log(path, cfg):
    rows = []
    for line in Path(path).read_text().splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue  # CUDA/HAMi diagnostic lines are not JSON events.
        if isinstance(value, dict) and 'event' in value:
            rows.append(value)
    if any(row['event'] == 'WORKER_ERROR' for row in rows):
        raise ValueError('WORKER_ERROR in log')
    events = {}
    for event in ('READY', 'BENCHMARK_START', 'BENCHMARK_END', 'VERIFIED', 'RELEASED'):
        matches = [row for row in rows if row['event'] == event]
        if len(matches) != 1 or matches[0].get('task_id') != cfg['task_id']:
            raise ValueError('missing/duplicate event or wrong task: ' + event)
        events[event] = matches[0]
    ready, start, end = (events[event] for event in ('READY', 'BENCHMARK_START', 'BENCHMARK_END'))
    if ready['config'] != cfg or ready['config_sha256'] != digest(cfg):
        raise ValueError('READY configuration digest mismatch')
    if ready['tf32'] or ready['dtype'] != 'float32':
        raise ValueError('precision changed')
    if (end['completed_gemms'] != cfg['iterations'] or end['expected_gemms'] != cfg['iterations']
            or end['start_ns'] != start['start_ns'] or end['release_ns'] != start['release_ns']
            or end['clock_domain'] != ready['clock_domain'] or end['end_ns'] <= end['start_ns']):
        raise ValueError('work count, clock or END mismatch')
    if not all(events[event].get('correct') is True for event in ('VERIFIED', 'RELEASED')):
        raise ValueError('numeric validation failed')
    return dict(end, ready=ready)


def task_config(base, workload, n, i):
    cfg = dict(base)
    sleep = int(cfg.get('sleep_ns', 0)) if workload != 'continuous' else 0
    offset = i * sleep // n if workload == 'bursty-staggered' else 0
    cfg.update(task_id=str(i), workload_id=workload, concurrency=n, sleep_ns=sleep,
               prefix_sleep_ns=offset, final_sleep_ns=sleep - offset)
    return cfg


def client_raw(path, configs, uuid):
    stored, identity = read_json(path / 'trial.json'), read_json(path / 'identity.json')
    if not stored['valid'] or stored['cleanup'].get('verified') is not True:
        raise ValueError('execution or cleanup not verified')
    release = stored['release']
    domain = identity['clock_domain']
    if release['operation'] != 'start' or release['clock_domain'] != domain:
        raise ValueError('controller clock differs from release')
    controls = read_json(path / 'control-final.json')
    pods = read_json(path / 'terminated-pods.json')
    tasks = []
    for cfg in configs:
        task = task_from_log(path / f"task-{cfg['task_id']}.log", cfg)
        ready = task['ready']
        if ready['gpu_uuid'] != uuid or ready['clock_domain'] != domain:
            raise ValueError('READY GPU/clock mismatch')
        markers = controls[cfg['task_id']]
        if markers['armed.json']['command'] != release or markers['armed.json']['armed_ns'] >= release['release_ns']:
            raise ValueError('worker not armed before release')
        if task['release_ns'] != release['release_ns'] or not 0 <= task['start_ns'] - release['release_ns'] <= 20_000_000:
            raise ValueError('start lateness exceeds 20 ms')
        verified = markers['verified.json']
        for key in ('start_ns', 'end_ns', 'release_ns', 'clock_domain', 'completed_gemms'):
            if verified[key] != task[key]:
                raise ValueError('control/log mismatch: ' + key)
        if not verified['validation_started_after_end'] or verified['validation_end_ns'] < task['end_ns']:
            raise ValueError('validation before END')
        checks = verified['correctness_samples']
        if len(checks) != 8 or any(not row['ok'] or abs(row['actual'] - row['expected_float64']) >
                                  1e-3 + 1e-3 * abs(row['expected_float64']) for row in checks):
            raise ValueError('incorrect numeric samples')
        pod = next((pod for pod in pods if pod['metadata']['uid'] == ready['pod_uid']), None)
        if pod is None or any(c['restartCount'] != 0 or c['state']['terminated']['exitCode'] != 0
                              for c in pod['status']['containerStatuses']):
            raise ValueError('missing successful container exit')
        tasks.append(task)
    if max(t['start_ns'] for t in tasks) - min(t['start_ns'] for t in tasks) > 20_000_000:
        raise ValueError('start skew exceeds 20 ms')
    samples = jsonl(path / 'gpu-metrics.jsonl')
    final = read_json(path / 'observer-final.json')
    if final.get('error') or final['samples'] != len(samples):
        raise ValueError('incomplete observer')
    if any(sample.get('gpu_uuid') != uuid for sample in samples):
        raise ValueError('telemetry GPU mismatch')
    if any(record['clock_domain'] != domain for record in
           (read_json(path / 'observer-ready.json'), final)):
        raise ValueError('observer clock mismatch')
    return tasks, samples


def batch(path, frozen):
    path = Path(path)
    cell = read_json(path / 'cell.json')
    n = cell['concurrency']
    configs = [task_config(frozen['workloads'][cell['workload_id']], cell['workload_id'], n, i) for i in range(n)]
    tasks, samples = [], []
    if cell['mode'] == 'sequential':
        for i, cfg in enumerate(configs):
            clients, raw = client_raw(path / f'serial-task-{i}', [cfg], frozen['gpu_uuid'])
            tasks.extend(clients)
            samples.extend(raw)
        windows = [(t['start_ns'], t['end_ns']) for t in tasks]
        makespan = sum(b - a for a, b in windows) / 1e9
    else:
        tasks, samples = client_raw(path, configs, frozen['gpu_uuid'])
        windows = [(tasks[0]['release_ns'], max(t['end_ns'] for t in tasks))]
        makespan = (windows[0][1] - windows[0][0]) / 1e9
    if len({t['clock_domain'] for t in tasks}) != 1:
        raise ValueError('incompatible task clock domains')
    samples.sort(key=lambda row: row['monotonic_ns'])
    inside = [s for s in samples if any(a <= s['monotonic_ns'] <= b for a, b in windows)]
    if len(inside) < 2 or any(s.get(k) is None for s in inside for k in ('utilization_gpu', 'vram_used_mib', 'vram_free_mib')):
        raise ValueError('missing required telemetry')
    for a, b in windows:
        near = [s['monotonic_ns'] for s in samples if a - 1_500_000_000 <= s['monotonic_ns'] <= b + 1_500_000_000]
        if not near or near[0] > a or near[-1] < b or any(y - x > 1_500_000_000 for x, y in zip(near, near[1:])):
            raise ValueError('telemetry coverage gap')
    latencies = [(t['end_ns'] - t['start_ns']) / 1e9 for t in tasks]
    latency, util = describe(latencies), describe([s['utilization_gpu'] for s in inside])
    row = dict(cell, gpu='A40', makespan_seconds=makespan, throughput_tasks_per_second=n / makespan,
               task_latencies_seconds=latencies, mean_task_latency=latency['mean'], p50_task_latency=latency['p50'],
               p95_task_latency=latency['p95'], mean_gpu_utilization=window_mean(samples, 'utilization_gpu', windows),
               p50_gpu_utilization=util['p50'], p95_gpu_utilization=util['p95'],
               peak_vram_mib=max(s['vram_used_mib'] for s in inside), valid=True, error='',
               config_sha256=digest(frozen), telemetry_samples=len(inside),
               continued_after_pause=False, cleanup_recovered=False)
    for field in ('power_w', 'sm_clock_mhz', 'memory_clock_mhz'):
        row['mean_' + field] = window_mean(samples, field, windows)
    return row


def pair_baselines(rows):
    baseline = {(r['block'], r['workload_id'], r['concurrency']): r for r in rows
                if r['valid'] and r['mode'] in ('exclusive', 'sequential')}
    for r in rows:
        ref = baseline.get((r['block'], r['workload_id'], r['concurrency']))
        r['speedup_vs_sequential'] = ref['makespan_seconds'] / r['makespan_seconds'] if ref else None
        r['parallel_efficiency'] = r['speedup_vs_sequential'] / r['concurrency'] if ref else None
        single = baseline.get((r['block'], r['workload_id'], 1))
        r['per_task_slowdown'] = [x / single['mean_task_latency'] for x in r['task_latencies_seconds']] if single else None
    return rows


def load_summary(path):
    with Path(path).open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    for r in rows:
        for key in ('block', 'repeat', 'concurrency', 'telemetry_samples'):
            r[key] = int(r[key])
        for key in FIELDS[7:18]:
            r[key] = float(r[key]) if r.get(key) else None
        r['valid'] = r['valid'] == 'True'
        r['per_task_slowdown'] = json.loads(r['per_task_slowdown']) if r['per_task_slowdown'] else None
    if len({r['trial_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate trial in summary')
    return rows


def aggregate(rows):
    """Pool Task latencies, never average the per-batch P95s."""
    single = {(r['block'], r['workload_id']): r['mean_task_latency'] for r in rows
              if r['valid'] and r['mode'] == 'exclusive' and r['concurrency'] == 1}
    groups = []
    for workload in WORKLOADS:
        for mode in MODES:
            for n in (1, 2, 4):
                group = [r for r in rows if r['valid'] and (r['workload_id'], r['mode'], r['concurrency']) == (workload, mode, n)]
                if not group:
                    continue
                latencies = []
                for r in group:
                    values = r.get('task_latencies_seconds')
                    if values is None:
                        ref = single.get((r['block'], workload))
                        if ref is None or r['per_task_slowdown'] is None:
                            raise ValueError('exclusive baseline required to reconstruct Task latencies')
                        values = [x * ref for x in r['per_task_slowdown']]
                    if len(values) != n:
                        raise ValueError('missing Task latencies')
                    latencies.extend(values)
                result = dict(workload_id=workload, mode=mode, concurrency=n, repeats=len(group),
                              mean_task_latency=statistics.mean(latencies), p95_task_latency=describe(latencies)['p95'])
                for field in ('makespan_seconds', 'throughput_tasks_per_second', 'mean_gpu_utilization', 'speedup_vs_sequential'):
                    values = [r[field] for r in group if r[field] is not None]
                    result[field] = statistics.mean(values) if values else None
                    if field == 'speedup_vs_sequential':
                        result['speedup_min'] = min(values) if values else None
                        result['speedup_max'] = max(values) if values else None
                groups.append(result)
    return groups


def paired_ratios(rows):
    index = {(r['block'], r['workload_id'], r['mode'], r['concurrency']): r for r in rows if r['valid']}
    result = []
    for workload in WORKLOADS:
        for n in (1, 2, 4):
            pairs = [r['throughput_tasks_per_second'] / index[(block, workload, 'timeslicing', n)]['throughput_tasks_per_second']
                     for (block, w, mode, size), r in index.items() if (w, mode, size) == (workload, 'mps', n)
                     and (block, workload, 'timeslicing', n) in index]
            if pairs:
                result.append(dict(workload_id=workload, concurrency=n, repeats=len(pairs), ratio=statistics.mean(pairs)))
    return result


def table_text(rows):
    groups = aggregate(rows)
    parts = []
    for workload in WORKLOADS:
        parts.append('### ' + workload + '\n\n| 模式 | N | 平均時間（秒） | Task/s | GPU 使用率 | 平均延遲（秒） | P95 延遲（秒） | Speedup |\n|---|---:|---:|---:|---:|---:|---:|---:|')
        for g in groups:
            if g['workload_id'] != workload:
                continue
            speed = f"{g['speedup_vs_sequential']:.3f}" if g['speedup_vs_sequential'] is not None else '—'
            parts.append(f"| {g['mode']} | {g['concurrency']} | {g['makespan_seconds']:.3f} | {g['throughput_tasks_per_second']:.5f} | {g['mean_gpu_utilization']:.2f}% | {g['mean_task_latency']:.3f} | {g['p95_task_latency']:.3f} | {speed} |")
        parts.append('')
    parts.append('### MPS 與 TimeSlicing\n\n| 工作 | N | MPS / Time-Slicing |\n|---|---:|---:|')
    for r in paired_ratios(rows):
        parts.append(f"| {r['workload_id']} | {r['concurrency']} | {r['ratio']:.3f} |")
    return '\n'.join(parts) + '\n'


def write_csv(path, rows):
    with Path(path).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='summary.csv or a run directory containing cell.json batches')
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[3] / 'data/e3/benchmark-config.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        if args.input.is_file():
            rows = load_summary(args.input)
        else:
            frozen = read_json(args.config)
            rows = pair_baselines([batch(p.parent, frozen) for p in sorted(args.input.rglob('cell.json'))])
            if not rows:
                raise ValueError('no batches')
            write_csv(args.output / 'summary.csv', rows)
        (args.output / 'tables.md').write_text(table_text(rows))
        (args.output / 'aggregated.json').write_text(json.dumps(aggregate(rows), indent=2) + '\n')
        (args.output / 'paired-ratios.json').write_text(json.dumps(paired_ratios(rows), indent=2) + '\n')
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Analysis failed: ' + str(error))
        return 1
    print(f'Analyzed {len(rows)} valid batches')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
