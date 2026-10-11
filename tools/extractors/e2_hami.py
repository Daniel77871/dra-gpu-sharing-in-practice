"""HAMi E2 additions live separately from the earlier E2 summaries."""
import datetime as dt
import json

import yaml

from . import register
from .e2 import claim, event_list, pod
from .hami_evidence import Evidence, compact_proc, trial

SOURCE = 'results/experiment-2-isolation/e2-capacity-20261007T015625Z'
CASES = [f'hami-{case}-r{repeat}' for case in ('accounting', 'boundary-8g', 'scope-8g')
         for repeat in (1, 2, 3)]


def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


@register('e2-hami', help='HAMi isolation additions, nine explicitly named E2 rounds')
def extract_e2_hami(reader, output):
    evidence = Evidence(reader, output)
    # Keep deployment input provenance without publishing whole cluster snapshots.
    for filename in ('hami-provider-input.yaml', 'deviceclass-e2-hami-gpu-20261007-input.yaml'):
        evidence.read(f'{SOURCE}/{filename}')
    for name in CASES:
        source, dest = f'{SOURCE}/{name}', f'raw/{name}'
        meta = trial(evidence.json(f'{source}/trial.json'))
        meta['backend'] = 'hami'
        evidence.write(f'{dest}/trial.json', meta)
        slices = yaml.safe_load(evidence.read(f'{source}/resourceslices.yaml'))
        evidence.write(f'{dest}/resourceslices.json', {'items': [claim(s) for s in slices['items']
                       if s['spec'].get('nodeName') == 'dra-a40']})
        evidence.copy(f'{source}/gpu-during.txt', f'{dest}/gpu-during.txt')
        if meta['case'] == 'accounting':
            for filename in ('claims-before-release.json', 'claims-after-release.json'):
                evidence.write(f'{dest}/{filename}', [claim(c) for c in evidence.json(f'{source}/{filename}')])
            for filename in ('events-before-release.json', 'events-final.json'):
                evidence.write(f'{dest}/{filename}', event_list(evidence.json(f'{source}/{filename}')))
            continue
        for filename in ('task-a.log', 'task-b.log', 'processes-during.txt', 'client-identity.json', 'runtime-hooks.json'):
            evidence.copy(f'{source}/{filename}', f'{dest}/{filename}')
        clients = evidence.json(f'{source}/client-identity.json')
        evidence.write(f'{dest}/client-processes.json', {str(c['host_pid']): compact_proc(
                       evidence.json(f'{source}/client-{c["host_pid"]}.json')) for c in clients})
        for filename in ('survivor-final.json', 'pressure-final.json'):
            evidence.write(f'{dest}/{filename}', pod(evidence.json(f'{source}/{filename}')))
        evidence.write(f'{dest}/claims-allocated.json', [claim(c) for c in evidence.json(f'{source}/claims-allocated.json')])
        evidence.write(f'{dest}/events-final.json', event_list(evidence.json(f'{source}/events-final.json')))
        events = [json.loads(line) for line in (output / dest / 'task-b.log').read_text().splitlines() if line.startswith('{')]
        start = min(stamp(e['utc']) for e in events if e['event'] == 'B_GROW')
        end = min(stamp(e['utc']) for e in events if e['event'] == 'B_EXIT')
        metrics = [json.loads(line) for line in evidence.read(f'{source}/gpu-metrics.jsonl').decode().splitlines()]
        selected = [{k: v for k, v in sample.items() if k in ('utc', 'finished_utc', 'uuid',
                     'memory.total', 'memory.used', 'memory.free', 'error')}
                    for sample in metrics if not sample.get('error') and start <= stamp(sample['utc'])
                    and stamp(sample['finished_utc']) <= end]
        (output / dest / 'gpu-metrics.jsonl').write_text(''.join(json.dumps(s) + '\n' for s in selected))
    versions = evidence.json(f'{SOURCE}/hami-scope-8g-r1/versions.json')
    evidence.write('versions.json', {'kubernetes': versions['kubernetes'], 'provider': pod(versions['provider'])})
    evidence.finish()
