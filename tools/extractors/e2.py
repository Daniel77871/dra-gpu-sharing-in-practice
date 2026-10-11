"""Explicit isolation evidence inputs; append named HAMi cases for later runs."""
import csv
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

import yaml

from . import register

MPS = 'results/experiment-2-isolation/mps-20261006T092100Z'
CAPACITY = 'results/experiment-2-isolation/e2-capacity-20261007T015625Z'
# Each entry names one source directory. No recursive discovery of the lab tree.
SOURCE_CASES = [(MPS, f'{prefix}-r{repeat}', 'mps')
                for prefix in ('boundary-4g', 'boundary-8g', 'scope-8g') for repeat in (1, 2, 3)] + [
    (CAPACITY, f'{prefix}-r{repeat}', 'consumable-shares')
    for prefix in ('consumable-accounting', 'consumable-boundary-8g') for repeat in (1, 2, 3)]


def metadata(value):
    return {key: item for key, item in value.items()
            if key in ('name', 'namespace', 'uid', 'creationTimestamp', 'labels')}


def claim(value):
    return {key: metadata(item) if key == 'metadata' else item
            for key, item in value.items() if key in ('apiVersion', 'kind', 'metadata', 'spec', 'status')}


def pod(value):
    spec = value.get('spec', {})
    status = value.get('status', {})
    result = {'metadata': metadata(value.get('metadata', {})),
              'spec': {key: spec[key] for key in ('nodeName', 'resourceClaims') if key in spec},
              'status': {'phase': status.get('phase'), 'containerStatuses': []}}
    result['spec']['containers'] = [{key: c[key] for key in ('name', 'image', 'command', 'args', 'env') if key in c}
                                  for c in spec.get('containers', [])]
    for container in status.get('containerStatuses', []):
        kept = {key: container[key] for key in ('name', 'image', 'imageID', 'restartCount') if key in container}
        kept['state'] = {name: {key: item for key, item in state.items() if key != 'containerID'}
                         for name, state in container.get('state', {}).items()}
        result['status']['containerStatuses'].append(kept)
    return result


def event_list(value):
    result = []
    for event in value.get('items', []):
        # Scheduling messages establish Pending/allocated transitions; lifecycle
        # messages and reporting identities are not needed for the numeric table.
        if event.get('reason') in ('FailedScheduling', 'Scheduled'):
            kept = {key: event[key] for key in ('reason', 'type', 'message', 'eventTime', 'firstTimestamp', 'lastTimestamp')
                    if key in event}
            kept['involvedObject'] = {key: item for key, item in event.get('involvedObject', {}).items()
                                      if key in ('kind', 'name', 'namespace', 'uid')}
            result.append(kept)
    return {'items': result}


def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


@register('e2', help='MPS and consumable-capacity isolation, explicit original rounds')
def extract_e2(reader, output):
    # The talk's transcribed numbers are a separate, credited static input.
    talk = Path(__file__).resolve().parents[2] / 'data/e2/mps-talk.csv'
    (output / 'mps-talk.csv').write_bytes(talk.read_bytes())
    provenance = {}
    def read(relative):
        raw = reader.read_bytes(relative)
        provenance[relative] = hashlib.sha256(raw).hexdigest()
        return raw
    def read_json(relative):
        return json.loads(read(relative))
    def write_json(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    for source, trial, backend in SOURCE_CASES:
        prefix = f'{source}/{trial}'
        destination = output / 'raw' / trial
        destination.mkdir(parents=True, exist_ok=True)
        meta = read_json(f'{prefix}/trial.json')
        meta['backend'] = backend
        # Derived result fields remain in the original sources. The public trial
        # file carries observation times, parameters and independent exit states.
        kept = {key: item for key, item in meta.items() if key in (
            'backend', 'case', 'quota_gib', 'quota_bytes', 'repeat', 'namespace', 'run_id',
            'started_utc', 'finished_utc', 'expected_gpu_uuid', 'claim_uid', 'baseline_seconds',
            'recovery_seconds', 'baseline_elapsed', 'recovery_elapsed', 'b_actual_exit_utc',
            'recovery_start_utc', 'phase', 'collection_errors', 'cleanup')}
        kept['terminations'] = {role: {key: item for key, item in state.items() if key != 'containerID'}
                                for role, state in meta.get('terminations', {}).items()}
        write_json(destination / 'trial.json', kept)
        if meta['case'] == 'accounting':
            for name in ('claims-before-release.json', 'claims-after-release.json'):
                write_json(destination / name, [claim(c) for c in read_json(f'{prefix}/{name}')])
            for name in ('events-before-release.json', 'events-final.json'):
                write_json(destination / name, event_list(read_json(f'{prefix}/{name}')))
            slices = yaml.safe_load(read(f'{prefix}/resourceslices.yaml'))
            write_json(destination / 'resourceslices.json', {'items': [claim(s) for s in slices['items']
                       if s['spec'].get('nodeName') == 'dra-a40']})
            (destination / 'gpu-during.txt').write_bytes(read(f'{prefix}/gpu-during.txt'))
            continue
        for name in ('task-a.log', 'task-b.log', 'gpu-during.txt'):
            (destination / name).write_bytes(read(f'{prefix}/{name}'))
        b = [json.loads(line) for line in (destination / 'task-b.log').read_text().splitlines() if line.startswith('{')]
        start = min(stamp(row['utc']) for row in b if row['event'] == 'B_GROW')
        end = min(stamp(row['utc']) for row in b if row['event'] == 'B_EXIT')
        metrics = [json.loads(line) for line in read(f'{prefix}/gpu-metrics.jsonl').decode().splitlines()]
        selected = [{key: value for key, value in sample.items()
                     if key in ('utc', 'finished_utc', 'uuid', 'memory.total', 'memory.used', 'memory.free', 'error')}
                    for sample in metrics if not sample.get('error') and start <= stamp(sample['utc'])
                    and stamp(sample['finished_utc']) <= end]
        (destination / 'gpu-metrics.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in selected))
        for name in ('survivor-final.json', 'pressure-final.json'):
            write_json(destination / name, pod(read_json(f'{prefix}/{name}')))
        if backend == 'mps':
            claims = yaml.safe_load(read(f'{prefix}/resourceclaims.yaml'))
            write_json(destination / 'claims-allocated.json', [claim(c) for c in claims['items']])
            write_json(destination / 'events.json', event_list(read_json(f'{prefix}/events.json')))
            write_json(destination / 'mps-control.json', read_json(f'{prefix}/mps-control.json'))
        else:
            write_json(destination / 'claims-allocated.json', [claim(c) for c in read_json(f'{prefix}/claims-allocated.json')])
            write_json(destination / 'events-final.json', event_list(read_json(f'{prefix}/events-final.json')))
    for source, backend in ((MPS, 'mps'), (CAPACITY, 'consumable-shares')):
        version = read_json(f'{source}/versions.json')
        compact = {'kubernetes': version['kubernetes']}
        if 'helm_releases' in version:
            compact['helm_releases'] = [item for item in version['helm_releases']
                                       if item['name'] in ('dra-driver-nvidia-gpu', 'gpu-operator')]
            compact['dra_helm_values'] = version['dra_helm_values']
        if 'provider' in version:
            compact['provider'] = pod(version['provider'])
        environment = read_json(f'{source}/environment.json')
        compact['nodes'] = [{'name': node['metadata']['name'], 'capacity': node['status']['capacity'],
                            'nodeInfo': node['status']['nodeInfo']}
                           for node in environment['nodes']['items']]
        write_json(output / f'versions-{backend}.json', compact)
    identity = read_json(f'{MPS}/scope-8g-r1/mps-identity.json')
    identity['clients'] = [{key: value for key, value in client.items() if key != 'container_id'}
                           for client in identity['clients']]
    write_json(output / 'raw/scope-8g-r1/mps-identity.json', identity)
    (output / 'raw/boundary-8g-r1/processes-during.txt').write_bytes(read(f'{MPS}/boundary-8g-r1/processes-during.txt'))
    write_json(output / 'source-sha256.json', provenance)
    analysis_path = Path(__file__).resolve().parents[2] / 'labs/04-isolation/common/analysis/analyze.py'
    spec = importlib.util.spec_from_file_location('e2_analysis', analysis_path)
    analysis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analysis)
    rows = analysis.analyze_run(output / 'raw')
    analysis.write_summaries(rows, output)
    for case, name in ((True, 'accounting.csv'), (False, 'runtime.csv')):
        with (output / name).open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows({key: json.dumps(value, sort_keys=True) if isinstance(value, dict) else value
                              for key, value in row.items()} for row in rows if (row['case'] == 'accounting') == case)
