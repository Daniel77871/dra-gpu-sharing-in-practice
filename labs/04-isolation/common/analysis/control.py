#!/usr/bin/env python3
"""JSON evidence checks for the shell runners. No cluster or CUDA client."""
import argparse
import copy
import csv
import datetime as dt
import json
from pathlib import Path
import re
import sys
import time
import uuid

GPU_UUID = 'GPU-29ffee3e-bee4-e341-917a-fe7e43e977af'
OWNER = 'app.kubernetes.io/part-of'
COMPONENT = 'app.kubernetes.io/component'
ROLES = ('steady-service', 'quota-pressure', 'capacity-resident', 'capacity-release', 'capacity-waiter')
CLAIMS = ('a40-shared-mps', 'steady-gpu', 'pressure-gpu', 'capacity-resident-gpu', 'capacity-release-gpu', 'capacity-waiter-gpu')


def load(path):
    return json.loads(Path(path).read_text())


def save(path, obj):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def events(path):
    result = []
    for line in Path(path).read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and 'event' in row:
            result.append(row)
    return result


def find_event(path, event, nonce=None):
    found = [row for row in events(path) if row['event'] == event and (nonce is None or row.get('command_id') == nonce)]
    return found[-1] if found else None


def logs_ready(pod):
    state = next((c.get('state', {}) for c in pod.get('status', {}).get('containerStatuses', []) if c['name'] == 'workload'), {})
    return 'running' in state or 'terminated' in state


def termination(pod):
    return next((c.get('state', {}).get('terminated') for c in pod.get('status', {}).get('containerStatuses', []) if c['name'] == 'workload'), None)


def finished(pod):
    return termination(pod) is not None


def ownership(namespace, objects):
    if namespace and namespace['metadata'].get('labels', {}).get(OWNER) != 'dra-book':
        raise ValueError('namespace ownership differs; refusing to adopt gpu-isolation-lab')
    names = set(ROLES + CLAIMS + ('isolation-code',))
    for item in objects.get('items', []):
        meta = item['metadata']
        if meta['name'] in names and meta.get('labels', {}).get(OWNER) != 'dra-book':
            raise ValueError('foreign object ownership: ' + meta['name'])
        if item['kind'] in ('Pod', 'Job', 'ResourceClaim'):
            raise ValueError('previous workload/Claim remains; collect and clean up first: ' + meta['name'])


def stamp_run(items, run_id):
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?', run_id):
        raise ValueError('run-id must fit a Kubernetes instance label (1 to 63 characters, alphanumeric ends)')
    for item in items:
        if item['kind'] == 'Namespace':
            continue
        item['metadata'].setdefault('labels', {})['app.kubernetes.io/instance'] = run_id
        if item['kind'] == 'Job':
            item['spec']['template']['metadata'].setdefault('labels', {})['app.kubernetes.io/instance'] = run_id


def cleanup_owner(objects, run_id):
    for item in objects.get('items', []):
        labels = item['metadata'].get('labels', {})
        if labels.get('app.kubernetes.io/instance') != run_id:
            raise ValueError('workload belongs to a different run; refusing collection/cleanup: ' + item['metadata']['name'])


def target(slices, backend):
    found = []
    for item in slices['items']:
        spec = item['spec']
        for device in spec.get('devices', []):
            attrs = device.get('attributes', {})
            if attrs.get('uuid', {}).get('string') == GPU_UUID:
                found.append((spec, device))
    if len(found) != 1 or found[0][0].get('nodeName') != 'dra-a40':
        raise ValueError('requires exactly one A40 publisher on dra-a40')
    spec, device = found[0]
    if spec['driver'] != 'gpu.nvidia.com' or device['name'] != 'gpu-0':
        raise ValueError('requires the NVIDIA gpu.nvidia.com gpu-0 provider')
    if backend == 'mps' and device.get('allowMultipleAllocations', False):
        raise ValueError('MPS requires the native provider without consumable sharing')
    if backend == 'consumable-shares' and not device.get('allowMultipleAllocations', False):
        raise ValueError('ConsumableShares provider not installed')
    return {'node': spec['nodeName'], 'driver': spec['driver'], 'pool': spec['pool']['name'], 'device': device['name'], 'gpu_uuid': GPU_UUID,
            'published_memory': device.get('capacity', {}).get('memory', {}).get('value')}


def verify_provider_spec(current, expected):
    """Compare the complete provider spec, permitting only known API defaults."""
    actual = copy.deepcopy(current['spec'])
    wanted = copy.deepcopy(expected['spec'])
    for key, default in (('revisionHistoryLimit', 10), ('minReadySeconds', 0)):
        if key not in wanted and actual.get(key) == default:
            actual.pop(key, None)
    for spec in (actual, wanted):
        metadata = spec['template']['metadata']
        for key in ('creationTimestamp', 'managedFields', 'resourceVersion', 'uid', 'generation'):
            metadata.pop(key, None)
    pod = actual['template']['spec']
    wanted_pod = wanted['template']['spec']
    if 'serviceAccount' not in wanted_pod and pod.get('serviceAccount') == pod.get('serviceAccountName'):
        pod.pop('serviceAccount', None)
    if 'enableServiceLinks' not in wanted_pod and pod.get('enableServiceLinks') is True:
        pod.pop('enableServiceLinks')
    if actual != wanted:
        raise ValueError('running alternative provider spec differs from the checked-in manifest')


def provider_ready(daemonset, pods):
    uid = daemonset['metadata']['uid']
    selected = [pod for pod in pods.get('items', [])
                if pod['metadata'].get('namespace') == 'nvidia-dra-driver-gpu'
                and any(ref.get('kind') == 'DaemonSet' and ref.get('uid') == uid and ref.get('controller')
                        for ref in pod['metadata'].get('ownerReferences', []))]
    if not selected:
        return False
    if len(selected) != 1 or selected[0]['spec'].get('nodeName') != 'dra-a40':
        raise ValueError('requires exactly one alternative provider Pod on A40')
    pod = selected[0]
    for kind in ('containers', 'initContainers'):
        expected = {c['name']: (c['image'], c.get('env', [])) for c in daemonset['spec']['template']['spec'].get(kind, [])}
        actual = {c['name']: (c['image'], c.get('env', [])) for c in pod['spec'].get(kind, [])}
        if actual != expected:
            raise ValueError('alternative provider Pod image/env differs from its DaemonSet')
    return any(condition['type'] == 'Ready' and condition['status'] == 'True'
               for condition in pod.get('status', {}).get('conditions', [])) and not pod['metadata'].get('deletionTimestamp')


def gpu_idle(gpu, processes, daemons):
    rows = list(csv.reader(gpu.splitlines()))
    if len(rows) != 1 or len(rows[0]) != 7 or rows[0][0].strip() != GPU_UUID:
        raise ValueError('GPU idle snapshot UUID or query shape differs')
    if not 0 <= float(rows[0][3].strip()) <= 64 or rows[0][-1].strip() != 'Default' or processes.strip():
        return False
    for item in daemons['items']:
        if not item['metadata']['name'].startswith('mps-control-daemon-'):
            continue
        spec = item['spec']['template']['spec'] if item['kind'] == 'Deployment' else item['spec']
        if not spec.get('nodeName'):
            raise ValueError('MPS daemon node identity is missing')
        if spec['nodeName'] == 'dra-a40':
            return False
    return True


def manifest_items(path):
    text = Path(path).read_text()
    decoder = json.JSONDecoder()
    items = []
    while text.strip():
        text = text.lstrip()
        document, end = decoder.raw_decode(text)
        items.extend(document['items'] if document.get('kind') == 'List' else [document])
        text = text[end:]
    if not items:
        raise ValueError('empty manifests')
    return items


def split_manifests(items, directory):
    save(directory / 'base.json', {'apiVersion': 'v1', 'kind': 'List', 'items': [i for i in items if i['kind'] in ('Namespace', 'ConfigMap')]})
    save(directory / 'claims.json', {'apiVersion': 'v1', 'kind': 'List', 'items': [i for i in items if i['kind'] == 'ResourceClaim']})
    for item in items:
        if item['kind'] in ('Job', 'ResourceClaim'):
            save(directory / (item['metadata']['name'] + '.json'), item)


def same_pod(created, current):
    if current['metadata']['uid'] != created['metadata']['uid']:
        raise ValueError('Pod UID changed; preserving earlier evidence')
    if current['metadata'].get('labels', {}).get(OWNER) != 'dra-book':
        raise ValueError('Pod ownership changed')
    if current['metadata'].get('labels', {}).get('app.kubernetes.io/instance') != created['metadata'].get('labels', {}).get('app.kubernetes.io/instance'):
        raise ValueError('Pod belongs to a different run')


def verify_allocations(claims, backend, pod_a, pod_b, ready_a, ready_b):
    if ready_a.get('gpu_uuid') != GPU_UUID:
        raise ValueError('A CUDA GPU UUID differs')
    if ready_b.get('gpu_uuid') not in (None, GPU_UUID):
        raise ValueError('B CUDA GPU UUID differs')
    by_name = {c['metadata']['name']: c for c in claims['items']}
    shares = []
    for pod, quota in ((pod_a, 4), (pod_b, 8)):
        if pod['spec'].get('nodeName') != 'dra-a40':
            raise ValueError('Pod is not on dra-a40')
        name = pod['spec']['resourceClaims'][0]['resourceClaimName']
        claim = by_name[name]
        result = claim.get('status', {}).get('allocation', {}).get('devices', {}).get('results', [])
        if len(result) != 1 or any(result[0].get(k) != v for k, v in (('driver', 'gpu.nvidia.com'), ('pool', 'dra-a40'), ('device', 'gpu-0'))):
            raise ValueError('allocation does not identify the A40')
        if pod['metadata']['uid'] not in {r['uid'] for r in claim.get('status', {}).get('reservedFor', [])}:
            raise ValueError('Claim reservedFor does not include its Job Pod')
        if backend == 'consumable-shares':
            from analyze import quantity_bytes
            if quantity_bytes(result[0]['consumedCapacity']['memory']) != quota * 1024 ** 3 or not result[0].get('shareID'):
                raise ValueError('capacity/shareID mismatch')
            shares.append(result[0]['shareID'])
    if backend == 'mps' and len(by_name) != 1:
        raise ValueError('MPS A/B must share exactly one Claim')
    if backend == 'consumable-shares' and (len(by_name) != 2 or len(set(shares)) != 2):
        raise ValueError('capacity A/B must have independent allocations')


def verify_pressure(path, count):
    rows = events(path)
    if any(r['event'] in ('CUDA_ERROR', 'CUDA_SYNC_ERROR', 'STARTUP_ERROR', 'B_START_TIMEOUT', 'B_ABORTED') for r in rows):
        raise ValueError('B failed outside the allocation boundary')
    if any(r['event'] == 'B_LIMIT_EXCEEDED' for r in rows):
        return 'limit_not_enforced'
    if sum(r['event'] == 'B_HOLD' for r in rows) >= count:
        return 'holding'
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action')
    parser.add_argument('args', nargs='*')
    opts = parser.parse_args(argv)
    args, action = opts.args, opts.action
    try:
        if action == 'ownership':
            ownership(load(args[0]) if Path(args[0]).stat().st_size else {}, load(args[1]))
        elif action == 'target':
            if len(args) > 2 and load(args[2]).get('spec', {}).get('config'):
                raise ValueError('gpu.nvidia.com DeviceClass has overriding config')
            print(json.dumps(target(load(args[0]), args[1])))
        elif action == 'split':
            items = manifest_items(args[0])
            if len(args) > 2:
                stamp_run(items, args[2])
            split_manifests(items, Path(args[1]))
        elif action == 'cleanup-owner':
            cleanup_owner(load(args[0]), args[1])
        elif action == 'provider-ready':
            return 0 if provider_ready(load(args[0]), load(args[1])) else 1
        elif action == 'gpu-idle':
            return 0 if gpu_idle(Path(args[0]).read_text(), Path(args[1]).read_text(), load(args[2])) else 1
        elif action == 'pod':
            pods = load(args[0])['items']
            if not pods: return 1
            if len(pods) != 1: raise ValueError('expected exactly one Job Pod')
            print(pods[0]['metadata']['name'])
        elif action == 'logs-ready':
            return 0 if logs_ready(load(args[0])) else 1
        elif action == 'event':
            row = find_event(args[0], args[1], args[2] if len(args) > 2 else None)
            if row is None: return 1
            print(json.dumps(row))
        elif action == 'alive':
            if load(args[0]).get('status', {}).get('phase') in ('Succeeded', 'Failed'):
                raise ValueError('Pod exited before expected evidence')
        elif action == 'terminated':
            state = termination(load(args[0]))
            if state is None: return 1
            if state['exitCode'] != 0 or state.get('reason') == 'OOMKilled':
                raise ValueError('unexpected container termination: ' + str(state))
            print(json.dumps(state))
        elif action == 'finished':
            state = termination(load(args[0]))
            if state is None: return 1
            print(json.dumps(state))
        elif action == 'same-pod':
            same_pod(load(args[0]), load(args[1]))
        elif action == 'allocations':
            verify_allocations(load(args[0]), args[1], *(load(p) for p in args[2:]))
        elif action == 'pressure':
            verdict = verify_pressure(args[0], int(args[1]))
            if verdict is None: return 1
            print(verdict)
        elif action == 'command':
            print(json.dumps({'phase': args[0], 'probe': args[1] == 'true', 'start': args[2] == 'true', 'stop': args[3] == 'true', 'command_id': uuid.uuid4().hex}))
        elif action == 'window':
            path = Path(args[0]) / 'trial.json'; meta = load(path)
            key = args[1] + '_monotonic'
            if args[2] == 'start':
                meta[key] = time.monotonic(); meta[args[1] + '_start_utc'] = utc()
            else:
                meta[args[1] + '_elapsed'] = time.monotonic() - meta[key]
            save(path, meta)
        elif action == 'update':
            path = Path(args[0]) / 'trial.json'
            obj = load(path) if path.exists() else {}
            obj.update(json.loads(args[1])); save(path, obj)
        elif action == 'phase':
            path = Path(args[0]) / 'phases.json'
            rows = load(path) if path.exists() else []
            rows.append({'phase': args[1], 'utc': utc()}); save(path, rows)
        elif action == 'check-summary':
            from analyze import analyze_round
            row = analyze_round(Path(args[0]))
            if not row['complete']: raise ValueError(row.get('incomplete_reason', 'incomplete round'))
        else:
            raise ValueError('unknown action: ' + action)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Evidence check failed: ' + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
