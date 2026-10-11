#!/usr/bin/env python3
"""Install and remove a namespaced KFP backend using kubectl and UID checks."""
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PART_OF = 'app.kubernetes.io/part-of'
RUN_LABEL = 'dra-book/run-id'
CLUSTER_TYPES = ('customresourcedefinitions', 'clusterroles', 'clusterrolebindings',
                 'mutatingwebhookconfigurations', 'validatingwebhookconfigurations')
PLURALS = {'Namespace': 'namespaces', 'CustomResourceDefinition': 'customresourcedefinitions',
           'DeviceClass': 'deviceclasses', 'ConfigMap': 'configmaps', 'Sec' + 'ret': 'secrets',
           'ResourceClaimTemplate': 'resourceclaimtemplates', 'Pod': 'pods',
           'Workflow': 'workflows', 'ResourceClaim': 'resourceclaims'}


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def kc(*args, obj=None):
    result = subprocess.run(['kubectl', *args], text=True,
                            input=json.dumps(obj) if obj is not None else None,
                            capture_output=True, check=False)
    if result.returncode:
        # An API error must never print the request body (it may contain generated credentials).
        raise RuntimeError('kubectl ' + ' '.join(args) + ' failed: ' + result.stderr.strip())
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout


def get(kind, name=None, namespace=None):
    args = ['get', kind]
    if name:
        args += [name, '--ignore-not-found']
    if namespace:
        args += ['-n', namespace]
    return kc(*args, '-o', 'json')


def identity(obj):
    meta = obj['metadata']
    return (obj['kind'], meta.get('namespace', ''), meta['name'])


def preserved_body(obj):
    """Compare all desired state, without controller status or bookkeeping."""
    body = copy.deepcopy(obj)
    body.pop('status', None)
    meta = body.setdefault('metadata', {})
    for key in ('uid', 'resourceVersion', 'generation', 'creationTimestamp', 'managedFields', 'deletionTimestamp'):
        meta.pop(key, None)
    return body


def contains(live, expected):
    """Allow API defaults while comparing every supplied field."""
    if isinstance(expected, dict):
        return isinstance(live, dict) and all(k in live and contains(live[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(live, list) and len(live) == len(expected) and all(contains(a, b) for a, b in zip(live, expected))
    return live == expected


def compatible(existing, desired):
    """Accept additional API defaults; refuse differences in any desired field."""
    relevant = ('spec', 'rules', 'roleRef', 'subjects', 'webhooks', 'aggregationRule')
    return existing['kind'] == desired['kind'] and all(
        key not in desired or contains(existing.get(key), desired[key]) for key in relevant)


def require_owned(live, recorded, run_id):
    if identity(live) != identity(recorded) or live['metadata'].get('uid') != recorded['metadata']['uid']:
        raise RuntimeError('UID changed; refuse deletion: ' + recorded['metadata']['name'])
    labels = live['metadata'].get('labels', {})
    if labels.get(PART_OF) != 'dra-book' or labels.get(RUN_LABEL) != run_id:
        raise RuntimeError('Ownership labels changed; refuse deletion: ' + recorded['metadata']['name'])


def deletion_options(live, recorded, run_id):
    require_owned(live, recorded, run_id)
    return {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'propagationPolicy': 'Foreground',
            'preconditions': {'uid': recorded['metadata']['uid'], 'resourceVersion': live['metadata']['resourceVersion']}}


def delete_owned(recorded, run_id):
    meta = recorded['metadata']
    live = get(recorded['kind'], meta['name'], meta.get('namespace'))
    if live is None:
        return
    options = deletion_options(live, recorded, run_id)
    prefix = '/api/v1' if live['apiVersion'] == 'v1' else '/apis/' + live['apiVersion']
    if meta.get('namespace'):
        prefix += '/namespaces/' + meta['namespace']
    uri = prefix + '/' + PLURALS[live['kind']] + '/' + meta['name']
    try:
        kc('delete', '--raw', uri, '-f', '-', obj=options)
    except RuntimeError:
        current = get(recorded['kind'], meta['name'], meta.get('namespace'))
        if current is None:
            return
        require_owned(current, recorded, run_id)
        if not current['metadata'].get('deletionTimestamp'):
            raise
    kc('wait', '--for=delete', recorded['kind'] + '/' + meta['name'],
       *(['-n', meta['namespace']] if meta.get('namespace') else []), '--timeout=300s')


def cluster_inventory():
    return {kind: get(kind)['items'] for kind in CLUSTER_TYPES}


def verify_preserved(before, after):
    """All original cluster-scoped objects must retain UID and desired state."""
    errors = []
    for kind, originals in before.items():
        now = {identity(o): o for o in after[kind]}
        for old in originals:
            current = now.get(identity(old))
            if (current is None or current['metadata']['uid'] != old['metadata']['uid']
                    or preserved_body(current) != preserved_body(old)):
                errors.append({'kind': kind, 'name': old['metadata']['name']})
    if errors:
        raise RuntimeError('Pre-existing cluster objects changed: ' + json.dumps(errors))


def put_labels(obj, run_id):
    def add(meta):
        meta.setdefault('labels', {}).update({PART_OF: 'dra-book', RUN_LABEL: run_id})
    add(obj['metadata'])
    if obj['kind'] in ('Deployment', 'Job'):
        add(obj['spec']['template'].setdefault('metadata', {}))
    if obj['kind'] == 'ResourceClaimTemplate':
        add(obj['spec'].setdefault('metadata', {}))


def owned_controller_lease(lease, inventory, state):
    """Tie the unlabeled leader Lease to the recorded namespaced controller."""
    if identity(lease) != ('Lease', state['namespace'], 'workflow-controller'):
        return False
    holder = lease.get('spec', {}).get('holderIdentity', '')
    deployment = next((o for o in state['created'] if o['kind'] == 'Deployment'
                       and o['metadata']['name'] == 'workflow-controller'), None)
    if deployment is None:
        return False
    expected = deployment['spec']['template']['spec']
    by_uid = {o['metadata']['uid']: o for o in inventory}
    for pod in inventory:
        if pod['kind'] != 'Pod' or not holder.startswith(pod['metadata']['name']):
            continue
        parents = [by_uid.get(o['uid']) for o in pod['metadata'].get('ownerReferences', []) if o.get('controller')]
        if len(parents) != 1 or not parents[0] or parents[0]['kind'] != 'ReplicaSet':
            continue
        rs = parents[0]
        if not any(o.get('uid') == deployment['metadata']['uid'] and o.get('controller')
                   for o in rs['metadata'].get('ownerReferences', [])):
            continue
        actual = pod['spec']
        if (actual.get('serviceAccountName') == 'argo'
                and actual.get('containers') == expected.get('containers')
                and actual.get('nodeSelector') == expected.get('nodeSelector')):
            return True
        # The API adds defaults to containers, so require all recorded fields instead.
        if (actual.get('serviceAccountName') == 'argo'
                and compatible({'kind': 'Pod', 'spec': {'containers': actual.get('containers'),
                                 'nodeSelector': actual.get('nodeSelector')}},
                               {'kind': 'Pod', 'spec': {'containers': expected.get('containers'),
                                 'nodeSelector': expected.get('nodeSelector')}})):
            return True
    return False


def create_owned(obj, state, state_path):
    put_labels(obj, state['run_id'])
    meta = obj['metadata']
    prior = get(obj['kind'], meta['name'], meta.get('namespace'))
    record = next((o for o in state['created'] if identity(o) == identity(obj)), None)
    desired = copy.deepcopy(obj)
    if obj['kind'] == 'Secret':
        # Credentials are generated once; never persist or rotate them on an install retry.
        desired.pop('data', None)
        desired.pop('stringData', None)
    input_key = json.dumps(list(identity(obj)))
    inputs = state.setdefault('inputs', {})
    if prior is not None:
        if record:
            require_owned(prior, record, state['run_id'])
            if input_key in inputs and inputs[input_key] != desired:
                raise RuntimeError('Recorded input changed; use a new run: ' + meta['name'])
            if (not contains(record, desired) or not contains(prior, desired)
                    or (obj['kind'] == 'ConfigMap' and any(
                        prior.get(key) != desired.get(key) or record.get(key) != desired.get(key)
                        for key in ('data', 'binaryData', 'immutable')))):
                raise RuntimeError('Owned object differs from desired input: ' + meta['name'])
            inputs[input_key] = desired
            save(state_path, state)
            return prior
        raise RuntimeError('Existing unowned object; refuse overwrite: ' + meta['name'])
    if record is not None:
        raise RuntimeError('Recorded UID is absent; refuse replacement: ' + meta['name'])
    live = kc('create', '-f', '-', '-o', 'json', obj=obj)
    # Do not persist Secret values. UID and labels are sufficient to prove ownership.
    safe = copy.deepcopy(live)
    if safe['kind'] == 'Secret':
        safe.pop('data', None)
        safe.pop('stringData', None)
    state['created'].append(safe)
    inputs[input_key] = desired
    save(state_path, state)
    return live


def state_load(folder):
    path = Path(folder).resolve() / 'state.json'
    return path, json.loads(path.read_text())


def require_recorded_namespace_objects(inventory, state):
    recorded = {identity(obj): obj for obj in state['created']
                if obj['metadata'].get('namespace') == state['namespace']}
    for obj in inventory:
        prior = recorded.get(identity(obj))
        if prior is not None:
            require_owned(obj, prior, state['run_id'])


def install(folder, run_id, namespace):
    if os.environ.get('ALLOW_KFP_INSTALL') != '1':
        raise RuntimeError('Set ALLOW_KFP_INSTALL=1 to create KFP cluster-scoped resources')
    if not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,38}[a-z0-9])?', run_id):
        raise RuntimeError('KFP_RUN_ID must be a short DNS label (at most 40 characters)')
    if not re.fullmatch(r'gpu-kubeflow-[a-z0-9-]+-lab', namespace) or len(namespace) > 63:
        raise RuntimeError('Use an isolated gpu-kubeflow-<run-id>-lab Namespace')
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / 'state.json'
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state['run_id'] != run_id or state['namespace'] != namespace or state.get('uninstalled'):
            raise RuntimeError('Installation state mismatch; use a new run directory')
    else:
        if get('namespace', namespace) is not None:
            raise RuntimeError('Namespace already exists; preserve it and use a new KFP_RUN_ID')
        before = cluster_inventory()
        save(folder / 'cluster-before.json', before)
        daemonset = get('daemonset', 'dra-driver-nvidia-gpu-kubelet-plugin', 'nvidia-dra-driver-gpu')
        if daemonset is not None:
            save(folder / 'provider-daemonset-before.json', daemonset)
        state = {'run_id': run_id, 'namespace': namespace, 'created': [], 'reused': [], 'installed_at': utc()}
        save(state_path, state)
    objects = json.loads((ROOT / 'install/objects.json').read_text())['items']
    objects = json.loads(json.dumps(objects).replace('gpu-kubeflow-lab', namespace)
                         .replace('gpu-kubeflow-capacity', 'gpu-kubeflow-capacity-' + run_id))
    # Every pre-existing CRD is read-only. Validate compatibility before creating anything.
    before = json.loads((folder / 'cluster-before.json').read_text())
    for obj in objects:
        if obj['kind'] != 'CustomResourceDefinition':
            continue
        existing = get(obj['kind'], obj['metadata']['name'])
        record = next((o for o in state['created'] if identity(o) == identity(obj)), None)
        if existing and not record:
            if not compatible(existing, obj):
                raise RuntimeError('Existing CRD is incompatible; preserve it: ' + obj['metadata']['name'])
            if identity(existing) not in [identity(o) for o in before['customresourcedefinitions']]:
                raise RuntimeError('CRD appeared concurrently; preserve it and restart with a new run')
            state['reused'].append(list(identity(obj))) if list(identity(obj)) not in state['reused'] else None
    save(state_path, state)
    for obj in objects:
        if obj['kind'] == 'CustomResourceDefinition' and list(identity(obj)) in state['reused']:
            continue
        if obj['kind'] == 'Secret':
            name = obj['metadata']['name']
            if name == 'mysql-secret':
                key = 'password'
                obj['stringData'] = {key: secrets.token_urlsafe(32), 'username': 'root'}
            elif name == 'mlpipeline-minio-artifact':
                obj['stringData'] = {'accesskey': secrets.token_hex(16), 'secretkey': secrets.token_hex(32)}
        created = create_owned(obj, state, state_path)
        if created['kind'] == 'CustomResourceDefinition':
            kc('wait', '--for=condition=Established', 'crd/' + created['metadata']['name'], '--timeout=120s')
    state['device_class'] = 'gpu-kubeflow-capacity-' + run_id
    save(state_path, state)
    for obj in objects:
        if obj['kind'] == 'Deployment':
            kc('rollout', 'status', 'deployment/' + obj['metadata']['name'], '-n', namespace, '--timeout=1200s')
    inventory = get('pods', namespace=namespace)['items'] + get('replicasets', namespace=namespace)['items']
    state['controller_leases'] = []
    for lease in get('leases', namespace=namespace)['items']:
        if not owned_controller_lease(lease, inventory, state):
            raise RuntimeError('Unproven controller Lease in isolated Namespace')
        state['controller_leases'].append(lease['metadata'])
    save(state_path, state)
    verify_preserved(before, cluster_inventory())
    save(folder / 'install-verification.json', {'verified': True, 'namespace': namespace, 'at': utc(),
                                              'backend_pods': get('pods', namespace=namespace)})
    print('KFP Namespace: ' + namespace)
    print('KFP_STATE_DIR=' + str(folder))


def foreign_consumers(obj, namespace):
    if obj['kind'] == 'CustomResourceDefinition':
        spec = obj['spec']
        args = ['get', spec['names']['plural'] + '.' + spec['group'], '-o', 'json']
        if spec['scope'] == 'Namespaced':
            args += ['-A']
        consumers = kc(*args)['items']
        return [o for o in consumers if not (namespace and spec['scope'] == 'Namespaced'
                                            and o['metadata'].get('namespace') == namespace)]
    if obj['kind'] == 'DeviceClass':
        foreign = []
        for kind in ('resourceclaims', 'resourceclaimtemplates'):
            for consumer in kc('get', kind, '-A', '-o', 'json')['items']:
                if namespace and consumer['metadata'].get('namespace') == namespace:
                    continue
                spec = consumer['spec'] if kind == 'resourceclaims' else consumer['spec']['spec']
                for request in spec.get('devices', {}).get('requests', []):
                    choices = [request['exactly']] if 'exactly' in request else request.get('firstAvailable', [])
                    if any(choice.get('deviceClassName') == obj['metadata']['name'] for choice in choices):
                        foreign.append({'kind': consumer['kind'], 'name': consumer['metadata']['name'],
                                        'namespace': consumer['metadata'].get('namespace')})
        return foreign
    return []


def uninstall(folder):
    if os.environ.get('ALLOW_KFP_UNINSTALL') != '1':
        raise RuntimeError('Set ALLOW_KFP_UNINSTALL=1 to remove this installation')
    state_path, state = state_load(folder)
    namespace, run_id = state['namespace'], state['run_id']
    before = json.loads((state_path.parent / 'cluster-before.json').read_text())
    errors = []

    def failed(stage, error, obj=None):
        errors.append({'stage': stage, 'object': list(identity(obj)) if obj else None, 'error': str(error)})

    # Report unrelated changes without abandoning cleanup of our original UIDs.
    try:
        verify_preserved(before, cluster_inventory())
    except (RuntimeError, OSError, ValueError) as error:
        failed('preexisting-objects', error)
    namespace_record = next((o for o in state['created'] if o['kind'] == 'Namespace'), None)
    try:
        live = get('namespace', namespace)
        if live is not None:
            if namespace_record is None:
                raise RuntimeError('Namespace was never recorded; refuse deletion: ' + namespace)
            require_owned(live, namespace_record, run_id)
            resources = subprocess.run(['kubectl', 'api-resources', '--namespaced=true', '--verbs=list', '-o', 'name'],
                                       text=True, capture_output=True, check=True).stdout.splitlines()
            inventory = []
            for kind in sorted(set(resources)):
                if kind in ('events', 'events.events.k8s.io'):
                    continue
                inventory.extend(get(kind, namespace=namespace)['items'])
            require_recorded_namespace_objects(inventory, state)
            # A freshly created, UID-verified Namespace owns its runtime contents too.
            # Controllers need not copy our labels or add ownerReferences to Leases/Secrets.
            save(state_path.parent / 'namespace-ownership.json', {
                'scope': 'recorded-namespace', 'namespace_uid': live['metadata']['uid'], 'foreign': [],
                'objects': [{'kind': o['kind'], 'metadata': o['metadata']} for o in inventory]})
            volumes_path = state_path.parent / 'storage-before.json'
            recorded_volumes = json.loads(volumes_path.read_text()) if volumes_path.exists() else []
            volumes = {v['name']: v for v in recorded_volumes}
            for pvc in inventory:
                if pvc['kind'] != 'PersistentVolumeClaim' or not pvc['spec'].get('volumeName'):
                    continue
                pv = get('persistentvolume', pvc['spec']['volumeName'])
                if (pv is None or pv['spec'].get('claimRef', {}).get('uid') != pvc['metadata']['uid']
                        or pv['spec'].get('persistentVolumeReclaimPolicy') != 'Delete'):
                    raise RuntimeError('Storage UID or reclaim policy prevents verified cleanup')
                name, uid = pv['metadata']['name'], pv['metadata']['uid']
                if name in volumes and volumes[name]['uid'] != uid:
                    raise RuntimeError('Storage PV UID changed; preserve recorded evidence: ' + name)
                volumes[name] = {'name': name, 'uid': uid}
            # A prior attempt may have removed the PVC but left its PV in deletion.
            save(volumes_path, list(volumes.values()))
            delete_owned(namespace_record, run_id)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        failed('namespace', error, namespace_record)
    volumes_path = state_path.parent / 'storage-before.json'
    if volumes_path.exists():
        for volume in json.loads(volumes_path.read_text()):
            try:
                deadline = time.monotonic() + 300
                while get('persistentvolume', volume['name']) is not None:
                    live_pv = get('persistentvolume', volume['name'])
                    if live_pv is not None and live_pv['metadata']['uid'] != volume['uid']:
                        raise RuntimeError('PV was replaced; preserve it and inspect storage cleanup')
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Owned PV did not disappear after Namespace deletion')
                    time.sleep(2)
            except (RuntimeError, OSError, ValueError) as error:
                failed('storage', error)
    for obj in reversed(state['created']):
        if obj['kind'] == 'Namespace' or obj['metadata'].get('namespace'):
            continue
        try:
            live = get(obj['kind'], obj['metadata']['name'])
            if live is None:
                continue
            require_owned(live, obj, run_id)
            # Recheck after namespace DELETE: a UID rejection may mean a new
            # Namespace now uses the same name and its consumers are foreign.
            consumer_namespace = None
            current_namespace = get('namespace', namespace)
            if namespace_record is not None and current_namespace is not None:
                try:
                    require_owned(current_namespace, namespace_record, run_id)
                except RuntimeError:
                    pass
                else:
                    consumer_namespace = namespace
            if foreign_consumers(obj, consumer_namespace):
                raise RuntimeError('Foreign instances require this object; preserve it: ' + obj['metadata']['name'])
            delete_owned(obj, run_id)
        except (RuntimeError, OSError, ValueError) as error:
            failed('cluster-object', error, obj)
    preserved = False
    try:
        after = cluster_inventory()
        save(state_path.parent / 'cluster-after.json', after)
        verify_preserved(before, after)
        preserved = True
    except (RuntimeError, OSError, ValueError) as error:
        failed('preservation-verification', error)
    provider_before = state_path.parent / 'provider-daemonset-before.json'
    if provider_before.exists():
        try:
            original = json.loads(provider_before.read_text())
            current = get('daemonset', original['metadata']['name'], original['metadata']['namespace'])
            if (current is None or current['metadata']['uid'] != original['metadata']['uid']
                    or current['spec'] != original['spec']):
                raise RuntimeError('Original NVIDIA DaemonSet UID or spec changed; stop before provider restore')
        except (RuntimeError, OSError, ValueError) as error:
            failed('provider-verification', error)
    remaining, absent = [], False
    try:
        absent = get('namespace', namespace) is None
    except (RuntimeError, OSError, ValueError) as error:
        failed('namespace-verification', error)
    for obj in state['created']:
        if obj['metadata'].get('namespace'):
            continue
        try:
            if get(obj['kind'], obj['metadata']['name']) is not None:
                remaining.append(identity(obj))
        except (RuntimeError, OSError, ValueError) as error:
            remaining.append(identity(obj))
            failed('removal-verification', error, obj)
    verified = absent and not remaining and not errors and preserved
    save(state_path.parent / 'uninstall-verification.json', {'verified': verified,
                                                           'namespace_absent': absent, 'remaining': remaining,
                                                           'preexisting_objects_unchanged': preserved,
                                                           'errors': errors, 'at': utc()})
    if not verified:
        raise RuntimeError('KFP removal incomplete: ' + '; '.join(e['stage'] + ': ' + e['error'] for e in errors)
                           + '. Retain KFP_STATE_DIR and retry uninstall.')
    state['uninstalled'] = True
    save(state_path, state)
    print('KFP installation removed; original cluster-scoped objects preserved')


def collect_install_failure(folder, original_error):
    """Keep bounded, best-effort diagnostics without querying Secret values."""
    _, state = state_load(folder)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    destination = Path(folder) / 'diagnostics' / stamp
    destination.mkdir(parents=True, mode=0o700)
    report = {'namespace': state['namespace'], 'original_error': str(original_error),
              'at': utc(), 'commands': [], 'errors': [], 'skipped_logs': []}

    def capture(filename, *args):
        command = ['kubectl', '--request-timeout=30s', *args]
        try:
            result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=45)
            (destination / filename).write_text(result.stdout or '')
            (destination / (filename + '.stderr')).write_text(result.stderr or '')
            report['commands'].append({'file': filename, 'command': command, 'returncode': result.returncode})
            if result.returncode:
                report['errors'].append({'file': filename, 'error': result.stderr.strip() or 'kubectl failed'})
                return None
            return result.stdout
        except (OSError, subprocess.SubprocessError) as error:
            for path, value in [(filename, getattr(error, 'stdout', None)),
                                (filename + '.stderr', getattr(error, 'stderr', None) or str(error))]:
                text = value.decode(errors='replace') if isinstance(value, bytes) else value or ''
                (destination / path).write_text(text)
            report['commands'].append({'file': filename, 'command': command, 'returncode': None})
            report['errors'].append({'file': filename, 'error': str(error)})
            return None

    try:
        namespace = state['namespace']
        record = next((o for o in state['created'] if o['kind'] == 'Namespace'), None)
        if record is None:
            raise RuntimeError('Namespace was never recorded; diagnostics limited to local state')
        raw = capture('namespace.json', 'get', 'namespace', namespace, '--ignore-not-found', '-o', 'json')
        if not raw:
            raise RuntimeError('Recorded Namespace is absent or unavailable; no container logs collected')
        require_owned(json.loads(raw), record, state['run_id'])
        for filename, kind in [('deployments.json', 'deployments'), ('services.json', 'services'),
                               ('endpointslices.json', 'endpointslices')]:
            capture(filename, 'get', kind, '-n', namespace, '-o', 'json')
        capture('events.txt', 'get', 'events', '-n', namespace, '--sort-by=.metadata.creationTimestamp')
        capture('pods-describe.txt', 'describe', 'pods', '-n', namespace)
        raw = capture('pods.json', 'get', 'pods', '-n', namespace, '-o', 'json')
        for pod in json.loads(raw or '{"items": []}')['items']:
            name = pod['metadata']['name']
            statuses = {s['name']: s for key in ('containerStatuses', 'initContainerStatuses')
                        for s in pod.get('status', {}).get(key, [])}
            for container in pod['spec'].get('containers', []) + pod['spec'].get('initContainers', []):
                cname = container['name']
                status = statuses.get(cname, {})
                current = status.get('state', {})
                waiting = current.get('waiting', {}).get('reason')
                previous = bool(status.get('restartCount') or status.get('lastState', {}).get('terminated'))
                started = ('running' in current or 'terminated' in current
                           or (previous and waiting not in ('ContainerCreating', 'PodInitializing')))
                for stream, available in [('current', started), ('previous', previous)]:
                    if not available:
                        report['skipped_logs'].append({'pod': name, 'container': cname, 'stream': stream,
                                                      'reason': waiting or 'not yet started or restarted'})
                        continue
                    args = ['logs', name, '-n', namespace, '-c', cname, '--timestamps=true']
                    if stream == 'previous':
                        args.append('--previous')
                    capture(name + '.' + cname + '.' + stream + '.log', *args)
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        report['errors'].append({'stage': 'collection', 'error': str(error)})
    finally:
        save(destination / 'report.json', report)
    return destination


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('install', 'uninstall'))
    p.add_argument('--state-dir', required=True)
    p.add_argument('--run-id')
    p.add_argument('--namespace')
    args = p.parse_args()
    if args.action == 'install':
        try:
            install(args.state_dir, args.run_id, args.namespace)
        except (RuntimeError, OSError, ValueError) as error:
            if os.environ.get('ALLOW_KFP_INSTALL') == '1' and (Path(args.state_dir) / 'state.json').is_file():
                try:
                    destination = collect_install_failure(args.state_dir, error)
                    print('KFP install diagnostics: ' + str(destination), file=sys.stderr)
                except (RuntimeError, OSError, ValueError) as diagnostic_error:
                    print('KFP diagnostics could not be saved: ' + str(diagnostic_error), file=sys.stderr)
            raise
    else:
        uninstall(args.state_dir)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
