"""Read named E3 evidence only; retain timings and remove API/network metadata."""
import json
import re

D = 'deliveries/experiment-3-completion-20261007-v3'
R = D + '/results/experiment-3-performance/a40/20261007T151804Z'
GOLDEN = [('exclusive', 1), ('sequential', 2), ('timeslicing', 2), ('mps', 2), ('hami', 2)]


def extract(reader, output):
    for name in ('summary.csv', 'group-statistics.csv', 'task-timing.csv',
                 'paired-mps-vs-timeslicing.csv', 'condition-statistics.json', 'reconciliation.json'):
        raw = reader.read_bytes(R + '/analysis/' + name)
        # The reconciliation's absolute source paths are provenance, not metrics.
        if name == 'reconciliation.json':
            value = json.loads(raw)
            for trial in value['trials']:
                trial.pop('path', None)
            raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
        (output / name).write_bytes(raw)
    for name in ('benchmark-config.json', 'matrix.json'):
        (output / name).write_bytes(reader.read_bytes(R + '/' + name))
    versions = json.loads(reader.read_text(R + '/versions.json'))
    environment = {'kubernetes': versions['kubernetes']['serverVersion']['gitVersion'],
                   'native_image_ids': versions['native_image_ids'], 'driver': versions['driver']}
    (output / 'environment.json').write_text(json.dumps(environment, indent=2) + '\n')
    report = reader.read_text(D + '/EXPERIMENT_3_PERFORMANCE.md')
    tables = [table for table in re.findall(r'(?m)^\| (?:模式|工作) \|.*\n\|---.*(?:\n\|[^\n]*)+', report)
              if '| Speedup |' in table or '| MPS / Time-Slicing |' in table]
    # Three workload tables, then the paired ratio table. Keep original precision.
    (output / 'report-tables.md').write_text('# 原始報告表格\n\n' + '\n\n'.join(tables[:4]) + '\n')
    for mode, n in GOLDEN:
        trial_id = f'b2-continuous-{mode}-n{n}'
        source = R + f'/formal/continuous/{mode}/{trial_id}'
        target = output / 'raw' / trial_id
        target.mkdir(parents=True, exist_ok=True)
        stored = json.loads(reader.read_text(source + '/trial.json'))
        cell = {key: stored[key] for key in ('block', 'repeat', 'mode', 'workload_id', 'concurrency', 'trial_id')}
        (target / 'cell.json').write_text(json.dumps(cell, indent=2) + '\n')
        children = [(source + f'/serial-task-{i}', target / f'serial-task-{i}', [i]) for i in range(n)] if mode == 'sequential' else [(source, target, list(range(n)))]
        for src, dst, ids in children:
            dst.mkdir(parents=True, exist_ok=True)
            for name in ('gpu-metrics.jsonl', 'gpu-raw.csv', 'control-final.json',
                         'observer-ready.json', 'observer-final.json'):
                (dst / name).write_bytes(reader.read_bytes(src + '/' + name))
            for i in ids:
                name = f'task-{i}.log'
                (dst / name).write_bytes(reader.read_bytes(src + '/' + name))
            identity = json.loads(reader.read_text(src + '/identity.json'))
            (dst / 'identity.json').write_text(json.dumps({key: identity[key] for key in ('clock_domain', 'ready')}, indent=2) + '\n')
            trial = json.loads(reader.read_text(src + '/trial.json'))
            (dst / 'trial.json').write_text(json.dumps({key: trial[key] for key in
                ('release', 'makespan_seconds', 'valid', 'cleanup')}, indent=2) + '\n')
            pods = json.loads(reader.read_text(src + '/terminated-pods.json'))
            sanitized = []
            for pod in pods:
                sanitized.append({'metadata': {'name': pod['metadata']['name'], 'uid': pod['metadata']['uid']},
                    'spec': {'nodeName': pod['spec']['nodeName'], 'containers':
                             [{'name': c['name'], 'image': c['image']} for c in pod['spec']['containers']]},
                    'status': {'phase': pod['status']['phase'], 'containerStatuses':
                        [{'name': c['name'], 'restartCount': c['restartCount'],
                          'state': {'terminated': {k: v for k, v in c['state']['terminated'].items() if k != 'containerID'}}}
                         for c in pod['status']['containerStatuses']]}})
            (dst / 'terminated-pods.json').write_text(json.dumps(sanitized, indent=2) + '\n')
