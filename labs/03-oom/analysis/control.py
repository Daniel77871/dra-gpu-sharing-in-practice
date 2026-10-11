#!/usr/bin/env python3
"""Local JSON helpers for the shell runner; no Kubernetes client or CUDA import."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import time
import uuid

from analyze import rows

OWNER = 'app.kubernetes.io/part-of'
COMPONENT = 'app.kubernetes.io/component'


def load(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def target(slices, device_class, node):
    if device_class.get('spec', {}).get('config'):
        raise ValueError('gpu.nvidia.com DeviceClass must have no device configuration')
    targets = []
    for item in slices['items']:
        spec = item['spec']
        if spec.get('driver') != 'gpu.nvidia.com':
            continue
        for device in spec.get('devices', []):
            attributes = device.get('attributes', {})
            if attributes.get('productName', {}).get('string') == 'NVIDIA A40':
                targets.append({'node': spec.get('nodeName'), 'driver': spec['driver'],
                                'pool': spec['pool']['name'], 'device': device['name'],
                                'gpu_uuid': attributes['uuid']['string']})
    targets = list({(t['node'], t['pool'], t['device']): t for t in targets}.values())
    if len(targets) != 1 or targets[0]['node'] != node:
        raise ValueError('the unchanged productName selector requires exactly one published A40 on the target node')
    return targets[0]


def ownership(items):
    names = {('ConfigMap', 'gpu-oom-workload'), ('ResourceClaim', 'a40-shared'),
             ('Job', 'steady-service'), ('Job', 'greedy-batch')}
    for item in items['items']:
        metadata = item['metadata']
        if (item['kind'], metadata['name']) in names and metadata.get('labels', {}).get(OWNER) != 'dra-book':
            raise ValueError('refusing to adopt foreign ' + item['kind'] + '/' + metadata['name'])
        if item['kind'] == 'Job' and metadata['name'] in ('steady-service', 'greedy-batch') or (
                item['kind'] == 'Pod' and metadata.get('labels', {}).get(COMPONENT) in ('steady-service', 'greedy-batch')):
            raise ValueError('previous workloads remain; collect and clean up before starting a new run')


def manifest_items(path):
    # kubectl create prints each object separately; get can print a single List.
    text = Path(path).read_text()
    decoder = json.JSONDecoder()
    items = []
    while text.strip():
        text = text.lstrip()
        document, end = decoder.raw_decode(text)
        items.extend(document['items'] if document.get('kind') == 'List' else [document])
        text = text[end:]
    if not items:
        raise ValueError('rendered manifests are empty')
    return items


def split_manifests(items, directory):
    base = [item for item in items if item['kind'] in ('Namespace', 'ConfigMap')]
    save(directory / 'base.json', {'apiVersion': 'v1', 'kind': 'List', 'items': base})
    for item in items:
        if item['kind'] == 'ResourceClaim':
            save(directory / 'claim-manifest.json', item)
        elif item['kind'] == 'Job':
            save(directory / (item['metadata']['name'] + '-manifest.json'), item)


def verify_shared(claim, ready_a, ready_b, expected, pod_a, pod_b):
    if ready_a.get('gpu_uuid') != expected['gpu_uuid'] or ready_b.get('gpu_uuid') != expected['gpu_uuid']:
        raise ValueError('A and B must see the expected GPU UUID')
    allocation = claim.get('status', {}).get('allocation', {}).get('devices', {}).get('results', [])
    if len(allocation) != 1 or any(allocation[0].get(key) != expected[key] for key in ('driver', 'pool', 'device')):
        raise ValueError('claim allocation differs from the target A40')
    consumers = {entry['uid'] for entry in claim.get('status', {}).get('reservedFor', [])}
    if not {pod_a['metadata']['uid'], pod_b['metadata']['uid']} <= consumers:
        raise ValueError('reservedFor must include both Job Pods before B grows')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('target', 'ownership', 'split', 'pod', 'logs-ready', 'event', 'command', 'init',
                                         'phase', 'window', 'mode', 'shared', 'complete', 'failed', 'collected'))
    parser.add_argument('args', nargs='*')
    parsed = parser.parse_args(argv)
    action, args = parsed.action, parsed.args
    try:
        if action == 'target':
            print(json.dumps(target(load(args[0]), load(args[1]), args[2])))
        elif action == 'ownership':
            ownership(load(args[0]))
        elif action == 'split':
            split_manifests(manifest_items(args[0]), Path(args[1]))
        elif action == 'pod':
            pods = load(args[0])['items']
            if not pods:
                return 1
            if len(pods) != 1:
                raise ValueError('expected exactly one Pod for the Job')
            print(pods[0]['metadata']['name'])
        elif action == 'logs-ready':
            containers = load(args[0]).get('status', {}).get('containerStatuses', [])
            state = next((c.get('state', {}) for c in containers if c.get('name') == 'workload'), {})
            # Exit mode can terminate before polling observes Running. Its logs
            # remain readable and contain the OOM event we must preserve.
            return 0 if 'running' in state or 'terminated' in state else 1
        elif action == 'event':
            events = [row for row in rows(Path(args[0])) if row.get('event') == args[1]
                      and (len(args) == 2 or row.get('command_id') == args[2])]
            if not events:
                return 1
            print(json.dumps(events[-1]))
        elif action == 'command':
            print(json.dumps({'phase': args[0], 'probe': args[1] == 'true', 'start': args[2] == 'true',
                              'stop': args[3] == 'true', 'command_id': uuid.uuid4().hex}))
        elif action == 'mode':
            document = load(args[0])
            for env in document['spec']['template']['spec']['containers'][0]['env']:
                if env['name'] == 'OOM_MODE':
                    env['value'] = args[1]
            save(args[0], document)
        elif action == 'shared':
            verify_shared(*(load(path) for path in args))
        else:
            directory = Path(args[0])
            path = directory / 'trial.json'
            meta = load(path) if path.exists() else {}
            if action == 'init':
                meta = {'mode': args[1], 'repeat': int(args[2]), 'complete': False,
                        'expected_gpu_uuid': load(args[3])['gpu_uuid'], 'started_utc': utc(),
                        'baseline_seconds': 60, 'recovery_seconds': 60}
            elif action == 'phase':
                phases = load(directory / 'phases.json') if (directory / 'phases.json').exists() else []
                phases.append({'phase': args[1], 'utc': utc()})
                save(directory / 'phases.json', phases)
            elif action == 'window':
                key = args[1] + '_started_monotonic'
                if args[2] == 'start':
                    meta[key] = time.monotonic()
                    meta[args[1] + '_started_utc'] = utc()
                else:
                    meta[args[1] + '_observed_seconds'] = time.monotonic() - meta[key]
            elif action == 'complete':
                meta['complete'] = True
                meta['finished_utc'] = utc()
            elif action == 'failed':
                meta['complete'] = False
                meta['error'] = 'runner interrupted or failed; see run-error.txt and commands.log'
            elif action == 'collected':
                for prefix, role in (('a', 'survivor'), ('b', 'pressure')):
                    final = directory / f'{role}-final.json'
                    if not final.exists():
                        continue
                    pod = load(final)
                    containers = pod.get('status', {}).get('containerStatuses', [])
                    container = next((c for c in containers if c.get('name') == 'workload'), {})
                    state = container.get('state', {}).get('terminated', {})
                    meta[prefix + '_exit_code'] = state.get('exitCode')
                    meta[prefix + '_reason'] = state.get('reason')
                    meta[prefix + '_restarts'] = container.get('restartCount')
                    if prefix == 'b':
                        meta['b_finished_utc'] = state.get('finishedAt')
            save(path, meta)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Evidence check failed: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
