#!/usr/bin/env python3
"""Local evidence checks for HAMi; all cluster operations remain in shell scripts."""
import argparse
import csv
import importlib.util
import io
import json
from pathlib import Path
import sys
import time

ISOLATION = Path(__file__).resolve().parents[2]
COMMON = ISOLATION / 'common/analysis'
spec = importlib.util.spec_from_file_location('isolation_control', COMMON / 'control.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
analysis_spec = importlib.util.spec_from_file_location('isolation_hami_quantity', COMMON / 'analyze.py')
shared_analysis = importlib.util.module_from_spec(analysis_spec)
analysis_spec.loader.exec_module(shared_analysis)
quantity_bytes = shared_analysis.quantity_bytes

DRIVER = 'hami-core-gpu.project-hami.io'
GPU_UUID = c.GPU_UUID
LIBRARY_SHA = '56fc0944896337cc0ee439cbc7364105ccac11e8dc38add6a3dc467cea34b18f'
GIB, MIB = 1024 ** 3, 1024 ** 2
SCOPE_JOBS = {'case3': {'container-scope-pressure': {'worker-0', 'worker-1'}},
              'case4a': {'scope-primary': {'worker-0'}, 'scope-secondary': {'worker-1'}},
              'case4b': {'scope-primary': {'worker-0'}, 'scope-secondary': {'worker-1'}}}
SCOPE_CLAIMS = {'case3': {'container-scope-gpu'}, 'case4a': {'pod-shared-gpu'},
                'case4b': {'worker-primary-gpu', 'worker-secondary-gpu'}}


def ownership(namespace, objects):
    c.ownership(namespace, objects)
    for item in objects.get('items', []):
        meta = item['metadata']
        if meta['name'] == 'hami-scope-code' and meta.get('labels', {}).get(c.OWNER) != 'dra-book':
            raise ValueError('foreign object ownership: hami-scope-code')


def scope_launch(directory, job_name=None, created=None):
    p = Path(directory); meta = c.load(p / 'trial.json')
    path = p / 'scope-launches.json'
    if job_name is None:
        if path.exists():
            raise ValueError('scope launch journal already exists')
        c.save(path, {'run_id': meta['run_id'], 'jobs': {}})
        return
    journal = c.load(path)
    if journal['run_id'] != meta['run_id'] or job_name not in SCOPE_JOBS[meta['case']]:
        raise ValueError('scope launch journal ownership differs')
    if created is None:
        if job_name in journal['jobs']:
            raise ValueError('scope Job creation was already attempted')
        journal['jobs'][job_name] = {'attempted': True}
    else:
        labels = created['metadata'].get('labels', {})
        if (created.get('kind'), created['metadata']['name'], labels.get(c.OWNER),
                labels.get(c.COMPONENT), labels.get('app.kubernetes.io/instance')) != (
                'Job', job_name, 'dra-book', job_name, meta['run_id']):
            raise ValueError('created scope Job identity differs')
        if journal['jobs'].get(job_name) != {'attempted': True}:
            raise ValueError('scope Job creation intent missing or already recorded')
        journal['jobs'][job_name].update(uid=created['metadata']['uid'], spec=created['spec'])
    c.save(path, journal)


def scope_cleanup_clients(directory, inventory=None, recorded=None):
    """Exclude an unlaunched client only with journal and fresh API evidence."""
    p = Path(directory); meta = c.load(p / 'trial.json'); journal = c.load(p / 'scope-launches.json')
    if journal['run_id'] != meta['run_id']:
        raise ValueError('scope launch journal ownership differs')
    if inventory is None:
        inventory = c.load(p / 'scope-objects-current.json')['items']
    jobs = {item['metadata']['name']: item for item in inventory if item['kind'] == 'Job'}
    pods = [item for item in inventory if item['kind'] == 'Pod']
    if recorded is None:
        recorded = c.load(p / 'pods-current.json')['items']
    recorded = {item['metadata']['name']: item for item in recorded}
    expected = set()
    for name, clients in SCOPE_JOBS[meta['case']].items():
        launch = journal['jobs'].get(name)
        related = [pod for pod in pods if pod['metadata'].get('labels', {}).get(c.COMPONENT) == name
                   or any(ref.get('kind') == 'Job' and ref.get('name') == name
                          for ref in pod['metadata'].get('ownerReferences', []))]
        saved_name = p / (name + '-pod-name.txt')
        if name not in jobs:
            if launch or saved_name.exists() or (p / (name + '-created.json')).exists():
                raise ValueError('previously launched scope Job disappeared: ' + name)
            if related:
                raise ValueError('Pod exists for an unlaunched scope Job: ' + name)
            continue
        job = jobs[name]; labels = job['metadata'].get('labels', {})
        if (labels.get(c.OWNER), labels.get(c.COMPONENT), labels.get('app.kubernetes.io/instance')) != (
                'dra-book', name, meta['run_id']):
            raise ValueError('foreign scope Job ownership: ' + name)
        if not launch or launch.get('attempted') is not True or not launch.get('uid'):
            raise ValueError('scope Job launch UID evidence missing: ' + name)
        if job['metadata']['uid'] != launch['uid'] or job['spec'] != launch['spec']:
            raise ValueError('scope Job UID/spec changed: ' + name)
        if {item['name'] for item in job['spec']['template']['spec']['containers']} != clients:
            raise ValueError('scope Job CUDA container identity differs: ' + name)
        expected.update(clients)
        if saved_name.exists():
            pod_name = saved_name.read_text().strip()
            if pod_name not in recorded:
                raise ValueError('previously recorded scope Pod is absent: ' + pod_name)
            c.same_pod(c.load(p / (name + '-created.json')), recorded[pod_name])
            if [pod['metadata']['uid'] for pod in related] != [recorded[pod_name]['metadata']['uid']]:
                raise ValueError('scope Pod disappeared or an unrecorded Pod exists: ' + name)
        elif related:
            raise ValueError('unrecorded scope Pod exists: ' + name)
        for pod in related:
            if not any(ref.get('kind') == 'Job' and ref.get('name') == name and ref.get('uid') == launch['uid']
                       for ref in pod['metadata'].get('ownerReferences', [])):
                raise ValueError('scope Pod Job UID differs: ' + name)
    return expected


def scope_cleanup_phase(directory, current):
    """Journal real exits before deletion; permit only those deletions on retry."""
    p = Path(directory); meta = c.load(p / 'trial.json'); path = p / 'scope-cleanup-started.json'
    roles = SCOPE_JOBS[meta['case']]
    def scope_pod(item):
        return item['kind'] == 'Pod' and (
            item['metadata'].get('labels', {}).get(c.COMPONENT) in roles or any(
                ref.get('kind') == 'Job' and ref.get('name') in roles
                for ref in item['metadata'].get('ownerReferences', [])))
    if path.exists():
        phase = c.load(path)
        if phase['run_id'] != meta['run_id']:
            raise ValueError('scope cleanup phase ownership differs')
        if scope_terminations(phase['terminated_pods'], set(phase['clients'])) != phase['terminations']:
            raise ValueError('scope cleanup phase lacks actual CUDA exits')
    else:
        pods = [item for item in current['items'] if scope_pod(item)]
        expected = scope_cleanup_clients(p, current['items'], pods)
        actual = scope_terminations({'items': pods}, expected)
        saved = scope_terminations(c.load(p / 'terminated-pods.json'), expected)
        if actual is None or actual != saved or actual != c.load(p / 'terminations.json'):
            raise ValueError('fresh scope termination evidence differs before cleanup')
        claims = c.load(p / 'scope-claims-created.json')['items']
        if {claim['metadata']['name'] for claim in claims} != SCOPE_CLAIMS[meta['case']]:
            raise ValueError('scope Claim creation inventory differs')
        current_claims = {item['metadata']['name']: item for item in current['items'] if item['kind'] == 'ResourceClaim'}
        for claim in claims:
            name = claim['metadata']['name']; live = current_claims.get(name)
            labels = live['metadata'].get('labels', {}) if live else {}
            if not live or live['metadata']['uid'] != claim['metadata']['uid'] or live['spec'] != claim['spec'] or (
                    labels.get(c.OWNER), labels.get(c.COMPONENT), labels.get('app.kubernetes.io/instance')) != (
                    'dra-book', name, meta['run_id']):
                raise ValueError('scope Claim UID/spec/ownership changed before cleanup: ' + name)
        owned = [item for item in current['items'] if item['metadata'].get('labels', {}).get(c.OWNER) == 'dra-book'
                 and item['metadata'].get('labels', {}).get('app.kubernetes.io/instance') == meta['run_id']]
        phase = {'run_id': meta['run_id'], 'clients': sorted(expected),
                 'terminated_pods': {'items': pods}, 'terminations': actual, 'objects': {'items': owned}}
    saved_objects = {(item['kind'], item['metadata']['name']): item for item in phase['objects']['items']}
    for item in current['items']:
        key = (item['kind'], item['metadata']['name']); labels = item['metadata'].get('labels', {})
        if key not in saved_objects:
            if scope_pod(item) or item['kind'] == 'Job' and key[1] in roles or (
                    item['kind'] == 'ResourceClaim' and key[1] in SCOPE_CLAIMS[meta['case']]) or (
                    labels.get(c.OWNER) == 'dra-book' and labels.get('app.kubernetes.io/instance') == meta['run_id']):
                raise ValueError('unrecorded object appeared during scope cleanup: ' + key[1])
            continue
        saved = saved_objects[key]; saved_labels = saved['metadata'].get('labels', {})
        if item['metadata']['uid'] != saved['metadata']['uid'] or any(
                labels.get(label) != saved_labels.get(label) for label in
                (c.OWNER, c.COMPONENT, 'app.kubernetes.io/instance')):
            raise ValueError('scope cleanup object UID/ownership changed: ' + key[1])
        if item.get('spec') != saved.get('spec') or item.get('data') != saved.get('data'):
            raise ValueError('scope cleanup object spec/data changed: ' + key[1])
        if scope_pod(item):
            names = {container['name'] for container in item['spec']['containers']}
            if scope_terminations({'items': [item]}, names) != scope_terminations({'items': [saved]}, names):
                raise ValueError('scope cleanup actual container exits changed: ' + key[1])
    if not path.exists():
        c.save(path, phase)
    c.save(p / 'scope-cleanup-objects.json', phase['objects'])
    return phase


def scope_terminations(pods, expected=None):
    if expected is None:
        expected = {'worker-0', 'worker-1'}
    result, clients = [], set()
    for pod in pods.get('items', []):
        names = {container['name'] for container in pod['spec']['containers']}
        if not names <= {'worker-0', 'worker-1'} or clients & names:
            raise ValueError('scope CUDA container identity differs')
        clients.update(names)
        states = {container['name']: container for container in pod.get('status', {}).get('containerStatuses', [])}
        if names != set(states) or not all('terminated' in container.get('state', {}) for container in states.values()):
            return None
        for name, container in states.items():
            result.append({'pod': pod['metadata']['name'], 'container': name, **container['state']['terminated']})
    return result if clients == expected else None


def target(slices, deviceclass=None):
    matches = [(item, device) for item in slices['items'] for device in item['spec'].get('devices', [])
               if device.get('attributes', {}).get('uuid', {}).get('string') == GPU_UUID]
    if len(matches) != 1:
        raise ValueError('requires exactly one A40 publisher')
    item, device = matches[0]
    spec = item['spec']
    if (spec.get('nodeName'), spec['driver'], spec['pool']['name'], device['name']) != ('dra-a40', DRIVER, 'dra-a40', 'hami-gpu-0'):
        raise ValueError('requires the HAMi A40 hami-gpu-0 provider')
    if not device.get('allowMultipleAllocations') or quantity_bytes(device['capacity']['cores']['value']) != 100:
        raise ValueError('HAMi memory/cores capacities are not published')
    if deviceclass is not None:
        wanted = 'device.driver == "' + DRIVER + '" && device.attributes["' + DRIVER + '"].type == "hami-gpu"'
        if deviceclass['spec'].get('config') or deviceclass['spec'].get('selectors') != [{'cel': {'expression': wanted}}]:
            raise ValueError('HAMi DeviceClass selector/config differs')
    return {'node': 'dra-a40', 'driver': DRIVER, 'pool': 'dra-a40', 'device': 'hami-gpu-0',
            'gpu_uuid': GPU_UUID, 'published_memory': device['capacity']['memory']['value'], 'published_cores': 100}


def check_allocation(claim, quota, cores):
    rows = (claim.get('status', {}).get('allocation') or {}).get('devices', {}).get('results', [])
    if len(rows) != 1 or any(rows[0].get(key) != value for key, value in
                            [('driver', DRIVER), ('pool', 'dra-a40'), ('device', 'hami-gpu-0')]):
        raise ValueError('allocation does not identify the HAMi A40')
    result = rows[0]
    if not result.get('shareID') or quantity_bytes(result['consumedCapacity']['memory']) != quota * GIB:
        raise ValueError('allocation memory/shareID differs')
    if quantity_bytes(result['consumedCapacity']['cores']) != cores:
        raise ValueError('allocation cores differs')
    return result


def allocations(claims, pod_a, pod_b, ready_a, ready_b):
    by_name = {claim['metadata']['name']: claim for claim in claims['items']}
    if len(by_name) != 2:
        raise ValueError('runtime A/B require two independent Claims')
    shares = []
    for pod, ready, quota in ((pod_a, ready_a, 4), (pod_b, ready_b, 8)):
        if pod['spec'].get('nodeName') != 'dra-a40' or ready.get('gpu_uuid') not in (None, GPU_UUID):
            raise ValueError('runtime Pod/CUDA device differs')
        claim = by_name[pod['spec']['resourceClaims'][0]['resourceClaimName']]
        shares.append(check_allocation(claim, quota, 50)['shareID'])
        if pod['metadata']['uid'] not in {ref['uid'] for ref in claim['status']['reservedFor']}:
            raise ValueError('Claim reservedFor does not identify the Job Pod')
    if len(set(shares)) != 2:
        raise ValueError('independent runtime Claims must have different shareIDs')


def process_rows(raw):
    result = {}
    for row in csv.reader(io.StringIO(raw)):
        if not row:
            continue
        if len(row) != 3:
            raise ValueError('NVML process query shape differs')
        pid, used = int(row[0].strip()), float(row[2].strip())
        if pid in result or used < 0:
            raise ValueError('NVML duplicate/invalid PID')
        result[pid] = used
    return result


def map_clients(processes, pods, ready, proc_fields):
    mapped = []
    for host_pid in process_rows(processes):
        raw = proc_fields[str(host_pid)]
        fields = dict(line.split(':', 1) for line in raw['status'].splitlines() if ':' in line)
        pid, uid = int(fields['NSpid'].split()[-1]), int(fields['Uid'].split()[0])
        matches = []
        for pod in pods:
            pod_uid = pod['metadata']['uid']
            if pod_uid not in raw['cgroup'] and pod_uid.replace('-', '_') not in raw['cgroup']:
                continue
            for container in pod['status']['containerStatuses']:
                cid = container['containerID'].split('://', 1)[-1]
                if cid not in raw['cgroup']:
                    continue
                candidates = [row for row in ready if row.get('pid') == pid and row.get('uid') == uid
                              and row.get('pod_name') == pod['metadata']['name']
                              and row.get('container_name', container['name']) == container['name']]
                if len(candidates) == 1 and 'workload.py' in raw['cmdline']:
                    matches.append({'host_pid': host_pid, 'namespace_pid': pid, 'uid': uid,
                                    'pod_name': pod['metadata']['name'], 'pod': pod['metadata']['name'],
                                    'pod_uid': pod_uid, 'container_name': container['name'],
                                    'container_id': cid, 'client_id': candidates[0].get('client_id', 'main')})
        if len(matches) != 1:
            raise ValueError('host PID does not identify exactly one CUDA client: ' + str(host_pid))
        mapped.extend(matches)
    return mapped


def verify_identity(ready, mappings, claims, case):
    by_uid = {claim['metadata']['uid']: claim for claim in claims}
    by_client = {row['client_id']: row for row in ready}
    if len(mappings) != 2 or set(by_client) != {'0', '1'}:
        raise ValueError('requires exactly two scope CUDA clients')
    cache_ids = {}
    for mapping in mappings:
        row = by_client[mapping['client_id']]
        claim_uid = mapping['claim_uid']
        check_allocation(by_uid[claim_uid], 8, 50)
        if row['env'].get('CUDA_DEVICE_MEMORY_LIMIT_0') != '8192m' or row['env'].get('CUDA_DEVICE_SM_LIMIT_0') != '50':
            raise ValueError('HAMi memory/cores env differs')
        if claim_uid not in row['cache_path'] or row['library_sha256'] != LIBRARY_SHA:
            raise ValueError('Claim cache/library identity differs')
        if not row['cache_map_lines'] or not row['library_map_lines']:
            raise ValueError('CUDA process has not loaded library/cache')
        if row['gpu_uuid'] != GPU_UUID or row['target_bytes'] != 6 * GIB or row['block_bytes'] != 256 * MIB:
            raise ValueError('scope GPU/workload inputs differ')
        cache_ids[mapping['client_id']] = (row['cache_dev'], row['cache_inode'])
    if (cache_ids['0'] == cache_ids['1']) != (case != 'case4b'):
        raise ValueError('cache inode relationship differs from Claim topology')
    shares = {check_allocation(claim, 8, 50)['shareID'] for claim in claims}
    if len(shares) != (2 if case == 'case4b' else 1):
        raise ValueError('scope Claim/shareID topology differs')
    return cache_ids


def scope_identity(directory):
    p = Path(directory)
    meta = c.load(p / 'trial.json')
    ready = c.load(p / 'ready.json')
    pods = c.load(p / 'pods-current.json')['items']
    claims = c.load(p / 'claims-allocated.json')['items']
    fields = c.load(p / 'proc-fields.json')
    raw = (p / 'processes-mapping.txt').read_text()
    mappings = map_clients(raw, pods, ready, fields)
    for mapping in mappings:
        pod = next(pod for pod in pods if pod['metadata']['uid'] == mapping['pod_uid'])
        name = pod['spec']['resourceClaims'][0]['resourceClaimName']
        mapping['claim_uid'] = next(claim['metadata']['uid'] for claim in claims if claim['metadata']['name'] == name)
    identities = verify_identity(ready, mappings, claims, meta['case'])
    c.save(p / 'identity.json', {'ready': ready, 'clients': mappings, 'claims': claims, 'pods': pods,
                               'proc_fields': fields, 'processes_raw': raw, 'cache_identities': identities})


def runtime_identity(directory):
    p = Path(directory)
    meta = c.load(p / 'trial.json')
    pods = [c.load(p / f'{prefix}-current.json') for prefix in ('a', 'b')]
    ready = [row for prefix in ('a', 'b') for row in c.events(p / f'task-{prefix}.log')
             if row['event'] in ('A_READY', 'B_READY') and row.get('gpu_uuid')]
    hooks = c.load(p / 'runtime-hooks.json')
    fields = c.load(p / 'proc-fields.json')
    mappings = map_clients((p / 'processes-mapping.txt').read_text(), pods, ready, fields)
    count = 3 if meta['case'] == 'scope' else 2
    if len(mappings) != count:
        raise ValueError('runtime CUDA process count differs')
    claims = c.load(p / 'claims-allocated.json')['items']
    for mapping in mappings:
        pod = next(pod for pod in pods if pod['metadata']['uid'] == mapping['pod_uid'])
        hook = hooks['steady-service' if pod['metadata']['uid'] == pods[0]['metadata']['uid'] else 'quota-pressure']
        claim = next(claim for claim in claims if claim['metadata']['name'] == pod['spec']['resourceClaims'][0]['resourceClaimName'])
        quota = 4 if mapping['pod_name'] == pods[0]['metadata']['name'] else 8
        check_allocation(claim, quota, 50)
        if (hook['env'].get('CUDA_DEVICE_MEMORY_LIMIT_0'), hook['env'].get('CUDA_DEVICE_SM_LIMIT_0')) != (str(quota * 1024) + 'm', '50'):
            raise ValueError('runtime memory/cores env differs')
        if hook['library_sha256'] != LIBRARY_SHA or claim['metadata']['uid'] not in hook['cache_path']:
            raise ValueError('runtime library/cache identity differs')
        maps = fields[str(mapping['host_pid'])]['maps']
        if 'libvgpu.so' not in maps or hook['cache_path'] not in maps:
            raise ValueError('actual CUDA process did not map the injected library/cache')
        mapping.update(claim_uid=claim['metadata']['uid'], cache_path=hook['cache_path'],
                       cache_dev=hook['cache_dev'], cache_inode=hook['cache_inode'], library_sha256=LIBRARY_SHA,
                       library_map_lines=[line for line in maps.splitlines() if 'libvgpu.so' in line],
                       cache_map_lines=[line for line in maps.splitlines() if '.cache' in line])
    b = [row for row in mappings if row['pod_name'] == pods[1]['metadata']['name']]
    if len({(row['cache_dev'], row['cache_inode']) for row in b}) != 1:
        raise ValueError('runtime B processes do not share a cache inode')
    a = next(row for row in mappings if row['pod_name'] == pods[0]['metadata']['name'])
    if (a['cache_dev'], a['cache_inode']) == (b[0]['cache_dev'], b[0]['cache_inode']):
        raise ValueError('independent runtime Claims share a cache inode')
    c.save(p / 'client-identity.json', mappings)


def provider_set(current, expected, run_id):
    wanted = {(item['kind'], item['metadata']['name']): item for item in c.manifest_items(expected)}
    for item in current.get('items', []):
        key = item['kind'], item['metadata']['name']
        labels = item['metadata'].get('labels', {})
        if key not in wanted or labels.get(c.OWNER) != 'dra-book' or labels.get('app.kubernetes.io/instance') != run_id:
            raise ValueError('foreign provider object; refusing to replace/delete: ' + str(key))
        if item['kind'] == 'DaemonSet':
            c.verify_provider_spec(item, wanted[key])
        else:
            for field in ('spec', 'rules', 'subjects', 'roleRef'):
                if item.get(field) != wanted[key].get(field):
                    raise ValueError('provider object changed concurrently: ' + str(key))


def native_excluded(directory):
    path = Path(directory)
    spec = importlib.util.spec_from_file_location('provider_control', ISOLATION / 'provider/control.py')
    provider = importlib.util.module_from_spec(spec); spec.loader.exec_module(provider)
    original, state = provider.read_backup(path / 'provider-backup')
    current = c.load(path / 'native-current.json')
    if provider.check_ds(current, original, state) != 'excluded':
        raise ValueError('native provider is not excluded as recorded')
    pods = c.load(path / 'pods-current.json')
    provider.check_protected(pods, original, state)
    if provider.owned_pods(pods, original, 'dra-a40'):
        raise ValueError('native A40 plugin still exists')


def scope_record(directory, round_id):
    p = Path(directory); identity = c.load(p / 'identity.json')
    allocations = c.load(p / 'allocation-acks.json'); snapshots = c.load(p / 'snapshot-acks.json')
    for row in snapshots:
        cid = row['client_id']
        if (row['cache_dev'], row['cache_inode']) != tuple(identity['cache_identities'][cid]) or not row['cache_map_lines']:
            raise ValueError('scope cache identity changed during allocation')
    record = round_record(round_id, snapshots, identity['clients'],
                          (p / 'processes-barrier.txt').read_text(),
                          float((p / 'gpu-barrier.txt').read_text().split(',')[4]))
    record.update(sample_started_utc=c.load(p / 'sample-time.json')['started_utc'], sample_finished_utc=c.utc(),
                  gpu_raw=(p / 'gpu-barrier.txt').read_text(),
                  processes_raw=(p / 'processes-barrier.txt').read_text(), allocation_acks=allocations)
    c.save(p / f'round-{round_id:02d}.json', record)
    print('finished' if all(row['status'] in ('cuda_oom', 'target_reached') for row in record['clients']) else 'growing')


def round_record(round_id, snapshots, mappings, processes, physical_free_mib):
    if len(snapshots) != 2 or {x['client_id'] for x in snapshots} != {'0', '1'}:
        raise ValueError('round snapshot requires both clients')
    if any(x['round_id'] != round_id or x['phase'] != 'snapshot'
           or x['last_allocation']['round_id'] != round_id for x in snapshots):
        raise ValueError('round snapshot barrier incomplete')
    physical = process_rows(processes)
    if set(physical) != {x['host_pid'] for x in mappings}:
        raise ValueError('NVML contains unknown/missing PID')
    by_client = {x['client_id']: x for x in mappings}
    rows = []
    for snapshot in sorted(snapshots, key=lambda x: x['client_id']):
        if snapshot['device_total_bytes'] != 8 * GIB or not 0 <= snapshot['device_free_bytes'] <= 8 * GIB:
            raise ValueError('CUDA quota-visible readings invalid')
        mapping = by_client[snapshot['client_id']]
        rows.append(dict(mapping, tensor_bytes=snapshot['tensor_bytes'],
                         cuda_total_bytes=snapshot['device_total_bytes'], cuda_free_bytes=snapshot['device_free_bytes'],
                         nvml_used_mib=physical[mapping['host_pid']], status=snapshot['status'],
                         allocation=snapshot['last_allocation'], snapshot=snapshot))
    claim_tensor, claim_physical = {}, {}
    for row in rows:
        uid = row['claim_uid']
        claim_tensor[uid] = claim_tensor.get(uid, 0) + row['tensor_bytes']
        claim_physical[uid] = claim_physical.get(uid, 0) + row['nvml_used_mib']
    return {'round_id': round_id, 'clients': rows, 'tensor_bytes': sum(x['tensor_bytes'] for x in rows),
            'physical_mib': sum(physical.values()), 'physical_free_mib': physical_free_mib,
            'claim_tensor_bytes': claim_tensor, 'claim_physical_mib': claim_physical}


def classify(case, rounds):
    if not rounds:
        raise ValueError('no complete allocation rounds')
    escaped = any(v > 8 * GIB for row in rounds for v in row['claim_tensor_bytes'].values())
    physical_exceeded = any(v > 8192 for row in rounds for v in row['claim_physical_mib'].values())
    oom = [client for row in rounds for client in row['clients']
           if client['allocation']['attempted'] and client['allocation']['status'] == 'cuda_oom']
    if any(row['physical_free_mib'] < 1024 and any(x['allocation']['attempted'] and
            x['allocation']['status'] == 'cuda_oom' for x in row['clients']) for row in rounds):
        raise ValueError('OOM cannot be attributed to quota: physical free below1GiB')
    last = rounds[-1]
    targets = all(x['status'] == 'target_reached' for x in last['clients'])
    if escaped:
        conclusion = 'shared_allocation_limit_exceeded' if case != 'case4b' else 'individual_claim_limit_exceeded'
    elif case == 'case4b' and targets:
        conclusion = 'separate_quotas_targets_reached'
    elif oom:
        conclusion = 'shared_allocation_quota_oom' if case != 'case4b' else 'separate_claims_early_oom'
    else:
        raise ValueError('clients neither reached targets nor observed allocation OOM')
    return {'conclusion': conclusion, 'cuda_oom_count': len(oom),
            'peak_tensor_bytes': max(x['tensor_bytes'] for x in rounds),
            'peak_physical_mib': max(x['physical_mib'] for x in rounds),
            'any_claim_tensor_exceeded': escaped, 'any_claim_physical_exceeded': physical_exceeded,
            'final_tensor_bytes_by_client': {x['client_id']: x['tensor_bytes'] for x in last['clients']}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action')
    parser.add_argument('args', nargs='*')
    opts = parser.parse_args(argv)
    action, args = opts.action, opts.args
    try:
        if action == 'ownership':
            namespace = c.load(args[0]) if Path(args[0]).stat().st_size else None
            ownership(namespace, c.load(args[1]))
        elif action == 'target':
            print(json.dumps(target(c.load(args[0]), c.load(args[2]) if len(args) > 2 else None)))
        elif action == 'allocations':
            allocations(c.load(args[0]), *(c.load(path) for path in args[2:]))
        elif action == 'render':
            items = c.manifest_items(args[0]); c.stamp_run(items, args[2])
            items = json.loads(json.dumps(items).replace('__RUN_ID__', args[2]).replace('__REPEAT__', args[3]))
            c.split_manifests(items, Path(args[1]))
        elif action == 'provider-render':
            items = c.manifest_items(args[0]); c.stamp_run(items, args[2])
            for item in items:
                if item['kind'] == 'DaemonSet':
                    item['spec']['template']['metadata']['labels']['app.kubernetes.io/instance'] = args[2]
            c.save(args[1], {'apiVersion': 'v1', 'kind': 'List', 'items': items})
        elif action == 'runtime-identity':
            runtime_identity(args[0])
        elif action == 'scope-identity':
            scope_identity(args[0])
        elif action == 'scope-terminations':
            expected = scope_cleanup_clients(args[1]) if len(args) > 1 else None
            result = scope_terminations(c.load(args[0]), expected)
            if result is None:
                return 1
            print(json.dumps(result))
        elif action == 'scope-launch':
            scope_launch(args[0], args[1] if len(args) > 1 else None,
                         c.load(args[2]) if len(args) > 2 else None)
        elif action == 'scope-cleanup-phase':
            scope_cleanup_phase(args[0], c.load(args[1]))
        elif action == 'scope-record':
            scope_record(args[0], int(args[1]))
        elif action == 'provider-set':
            provider_set(c.load(args[0]), args[1], args[2])
        elif action == 'native-excluded':
            native_excluded(args[0])
        elif action == 'check-summary':
            hami_spec = importlib.util.spec_from_file_location('isolation_hami_analysis', COMMON / 'hami.py')
            hami = importlib.util.module_from_spec(hami_spec)
            hami_spec.loader.exec_module(hami)
            row = hami.analyze_run(Path(args[0]))[0]
            if not row['complete']:
                raise ValueError(row['incomplete_reason'])
        else:
            return c.main([action, *args])
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('HAMi evidence check failed: ' + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
