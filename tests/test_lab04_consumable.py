"""Provider-switch safety checks; kubectl is replaced by a local state machine."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from conftest import ROOT

PROVIDER = ROOT / "labs/04-isolation/provider"
NAMESPACE = "nvidia-dra-driver-gpu"
DS_NAME = "dra-driver-nvidia-gpu-kubelet-plugin"


def native_ds():
    terms = [{"matchExpressions": [{"key": key, "operator": "In", "values": [value]}]}
             for key, value in [
                 ("feature.node.kubernetes.io/pci-10de.present", "true"),
                 ("feature.node.kubernetes.io/pci-0302_10de.present", "true"),
                 ("feature.node.kubernetes.io/pci-0300_10de.present", "true"),
                 ("feature.node.kubernetes.io/cpu-model.vendor_id", "NVIDIA"),
                 ("nvidia.com/gpu.present", "true"),
             ]]
    return {"apiVersion": "apps/v1", "kind": "DaemonSet", "metadata": {
        "namespace": NAMESPACE, "name": DS_NAME, "uid": "native-ds", "resourceVersion": "1"},
        "spec": {"selector": {"matchLabels": {"dra-driver-nvidia-gpu-component": "kubelet-plugin"}},
                 "updateStrategy": {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": "100%", "maxSurge": 0}},
                 "template": {"metadata": {"labels": {"dra-driver-nvidia-gpu-component": "kubelet-plugin"}},
                              "spec": {"nodeSelector": {"site": "fixture"},
                                       "affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": terms}}},
                                       "containers": [{"name": "gpus", "image": "fixture-native-image", "env": [{"name": "FEATURE_GATES", "value": "MPSSupport=true,"}]}]}}}}


def native_pod(node, uid=None):
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {
        "name": "native-" + node, "namespace": NAMESPACE, "uid": uid or ("pod-" + node), "resourceVersion": "1",
        "labels": {"dra-driver-nvidia-gpu-component": "kubelet-plugin"},
        "ownerReferences": [{"kind": "DaemonSet", "name": DS_NAME, "uid": "native-ds", "controller": True}]},
        "spec": {"nodeName": node, "containers": [{"name": "gpus", "image": "fixture-native-image"}]},
        "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}}


def native_slice(node, uid=None):
    return {"apiVersion": "resource.k8s.io/v1", "kind": "ResourceSlice", "metadata": {
        "name": "slice-" + node, "uid": uid or ("slice-uid-" + node), "resourceVersion": "1",
        "ownerReferences": [{"kind": "Node", "name": node, "uid": "node-" + node}]},
        "spec": {"driver": "gpu.nvidia.com", "nodeName": node,
                 "pool": {"name": node, "generation": 1, "resourceSliceCount": 1},
                 "devices": [{"name": "gpu-0", "attributes": {"uuid": {"string": "GPU-" + node}}, "capacity": {"memory": {"value": "48Gi"}}}]}}


@pytest.fixture
def fake_provider_cluster(tmp_path):
    lab = tmp_path / "labs/04-isolation"
    lab.mkdir(parents=True)
    shutil.copytree(PROVIDER, lab / "provider")
    shutil.copytree(ROOT / "labs/common", lab.parent / "common")
    driver = {"apiVersion": "v1", "kind": "Pod", "metadata": {
        "name": "driver-dra-a40", "namespace": "gpu-operator", "uid": "driver-a40", "labels": {"app": "nvidia-driver-daemonset"}},
        "spec": {"nodeName": "dra-a40", "containers": [{"name": "nvidia-driver-ctr", "image": "fixture-driver"}]},
        "status": {"phase": "Running", "containerStatuses": [{"name": "nvidia-driver-ctr", "ready": True}]}}
    state = {"ds": native_ds(), "pods": [native_pod("dra-a40"), native_pod("dra-4090"), driver],
             "slices": [native_slice("dra-a40"), native_slice("dra-4090")], "claims": [], "other_ds": [],
             "gpu_used_mib": 0, "compute_mode": "Default", "compute_apps": ""}
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state))
    actions = tmp_path / "actions.jsonl"
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "kubectl"
    fake.write_text(f"#!{sys.executable}\n" + r'''import copy, json, os, sys
from urllib.parse import unquote
path = os.environ['LAB04_STATE']
state = json.load(open(path))
args = [a for a in sys.argv[1:] if not a.startswith('--request-timeout=')]
entry = {'args': args}
def save():
    with open(path, 'w') as stream: json.dump(state, stream)
def field(obj, pointer):
    for key in pointer.strip('/').split('/'): obj = obj[key]
    return obj
if 'patch' in args:
    patch = json.load(open(args[args.index('--patch-file') + 1]))
    entry['body'] = patch
elif '--raw' in args:
    body = json.load(open(args[args.index('-f') + 1]))
    entry['body'] = body
with open(os.environ['LAB04_ACTIONS'], 'a') as stream:
    stream.write(json.dumps(entry) + '\n')
if 'get' in args:
    resource = args[args.index('get') + 1]
    if resource in ('daemonset', 'ds'):
        obj = state['ds']
    elif resource in ('daemonsets', 'daemonsets.apps'):
        obj = {'items': [state['ds']] + state['other_ds']}
    elif resource == 'pods':
        if '-A' in args and ('native_pod_gone_after_reads' in state or 'native_slices_gone_after_reads' in state):
            state['native_cleanup_reads'] = state.get('native_cleanup_reads', 0) + 1
            if state['native_cleanup_reads'] >= state.get('native_pod_gone_after_reads', 10**9):
                state['pods'] = [p for p in state['pods'] if p['metadata']['uid'] != 'pod-dra-a40']
            if state['native_cleanup_reads'] >= state.get('native_slices_gone_after_reads', 10**9):
                state['slices'] = [s for s in state['slices'] if s['metadata']['uid'] != 'slice-uid-dra-a40']
            save()
        pods = state['pods']
        if '-n' in args:
            pods = [p for p in pods if p['metadata']['namespace'] == args[args.index('-n') + 1]]
        if '-l' in args:
            labels = dict(item.split('=', 1) for item in args[args.index('-l') + 1].split(','))
            pods = [p for p in pods if all(p['metadata'].get('labels', {}).get(k) == v for k, v in labels.items())]
        if '-o' in args and args[args.index('-o') + 1] == 'name':
            for pod in pods: print('pod/' + pod['metadata']['name'])
            raise SystemExit(0)
        obj = {'items': pods}
    elif resource == 'pod':
        name = args[args.index('get') + 2]
        obj = next((p for p in state['pods'] if p['metadata']['name'] == name), None)
        if obj is None:
            if '--ignore-not-found' in args: raise SystemExit(0)
            raise SystemExit(1)
    elif resource.startswith('resourceslices'):
        obj = {'items': state['slices']}
    elif resource.startswith('resourceclaims'):
        obj = {'items': state['claims']}
    elif resource == 'node':
        name = args[args.index('get') + 2]
        obj = {'metadata': {'name': name, 'uid': 'node-' + name}}
    else:
        raise SystemExit('unexpected fake GET ' + resource)
    print(json.dumps(obj))
elif 'patch' in args:
    if state.pop('patch_race', False):
        state['ds']['spec']['template']['spec']['nodeSelector']['race'] = 'true'
        save()
    for operation in patch:
        if operation['op'] == 'test' and field(state['ds'], operation['path']) != operation['value']:
            raise SystemExit('JSON patch test failed')
    for operation in patch:
        if operation['op'] == 'replace':
            assert operation['path'] == '/spec'
            state['ds']['spec'] = operation['value']
    spec = state['ds']['spec']
    terms = spec['template']['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
    excluded = all(any(e.get('key') == 'metadata.name' and e.get('operator') == 'NotIn' and 'dra-a40' in e.get('values', [])
                       for e in term.get('matchFields', [])) for term in terms)
    if excluded and state.get('controller_deletes_pod'):
        state['pods'] = [p for p in state['pods'] if p['metadata']['namespace'] != 'nvidia-dra-driver-gpu' or p['spec'].get('nodeName') != 'dra-a40']
    if not excluded and not any(p['metadata']['namespace'] == 'nvidia-dra-driver-gpu' and p['spec'].get('nodeName') == 'dra-a40' for p in state['pods']):
        p = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {
            'name': 'native-dra-a40-restored', 'namespace': 'nvidia-dra-driver-gpu', 'uid': 'restored-a40', 'resourceVersion': '4',
            'labels': {'dra-driver-nvidia-gpu-component': 'kubelet-plugin'},
            'ownerReferences': [{'kind': 'DaemonSet', 'uid': 'native-ds', 'name': 'dra-driver-nvidia-gpu-kubelet-plugin', 'controller': True}]},
            'spec': {'nodeName': 'dra-a40', 'containers': [{'name': 'gpus', 'image': 'fixture-native-image'}]},
            'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        state['pods'].append(p)
        target = copy.deepcopy(state['baseline_target_slice'])
        target['metadata']['uid'] = 'restored-slice'
        target['spec']['pool']['generation'] = 5
        state['slices'].append(target)
    save()
    print(json.dumps(state['ds']))
elif 'exec' in args:
    assert args[args.index('-n') + 1] == 'gpu-operator'
    assert args[args.index('-c') + 1] == 'nvidia-driver-ctr'
    if '--query-compute-apps=pid,process_name,used_memory' in args:
        print(state['compute_apps'], end='')
    else:
        assert '--query-gpu=uuid,name,memory.total,memory.used,memory.free,utilization.gpu,compute_mode' in args
        print('GPU-dra-a40, NVIDIA A40, 49152, ' + str(state['gpu_used_mib']) + ', 49152, 0, ' + state['compute_mode'])
elif 'delete' in args and '--raw' in args:
    uri = args[args.index('--raw') + 1]
    name = unquote(uri.rsplit('/', 1)[1])
    kind = 'pods' if '/pods/' in uri else 'slices'
    obj = next((o for o in state[kind] if o['metadata']['name'] == name), None)
    race = state.pop('pod_delete_failure', None) if kind == 'pods' else None
    if race:
        reason, outcome = race.split('-', 1)
        obj['metadata']['resourceVersion'] = '2'
        if outcome == 'terminating':
            obj['metadata']['deletionTimestamp'] = '2026-10-11T17:10:15Z'
            state['native_pod_gone_after_reads'] = state.get('native_cleanup_reads', 0) + 2
        elif outcome == 'gone':
            state['pods'] = [p for p in state['pods'] if p['metadata']['name'] != name]
        elif outcome == 'replaced':
            obj['metadata']['uid'] = 'replacement-pod'
        else:
            assert outcome == 'rv-drift'
        save()
        raise SystemExit('Error from server (' + reason + '): concurrent Pod update')
    if state.pop('delete_race', False) and kind == 'pods':
        obj['metadata']['uid'] = 'replacement-pod'
        save()
    if obj is None: raise SystemExit('not found')
    if any(obj['metadata'][key] != value for key, value in body['preconditions'].items()):
        raise SystemExit('delete precondition failed')
    state[kind] = [o for o in state[kind] if o['metadata']['name'] != name]
    save()
    print(json.dumps({'status': 'Success'}))
elif 'delete' in args and args[args.index('delete') + 1] == 'daemonsets':
    selector = args[args.index('-l') + 1]
    assert selector == 'app.kubernetes.io/part-of=dra-book,app.kubernetes.io/component=consumable-provider'
    labels = dict(item.split('=', 1) for item in selector.split(','))
    state['other_ds'] = [d for d in state['other_ds'] if not all(d['metadata'].get('labels', {}).get(k) == v for k, v in labels.items())]
    save()
    print('No resources found')
else:
    raise SystemExit('unexpected fake command')
''')
    fake.chmod(0o755)
    state["baseline_target_slice"] = copy.deepcopy(state["slices"][0])
    state_path.write_text(json.dumps(state))
    env = os.environ.copy()
    for name in ("OUT_DIR", "RUN_ID", "I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER", "GPU_NODE", "PROTECTED_NODE"):
        env.pop(name, None)
    env.update(PATH=str(binary) + os.pathsep + env['PATH'], LAB04_STATE=str(state_path),
               LAB04_ACTIONS=str(actions), OUT_DIR=str(lab / "out/fixture"),
               I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER="1", WAIT_INTERVAL="0.01", PROVIDER_TIMEOUT="1")
    return lab, state_path, actions, env


def run_provider(cluster, script, *args):
    lab, _, _, env = cluster
    return subprocess.run(["bash", str(lab / "provider" / script), *args], env=env,
                          text=True, capture_output=True, timeout=8)


def read_actions(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def mutate(cluster, action):
    state_path = cluster[1]
    state = json.loads(state_path.read_text())
    action(state)
    state_path.write_text(json.dumps(state))


def backup(cluster):
    result = run_provider(cluster, "backup.sh")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('script,args', [('backup.sh', []), ('exclude-node.sh', ['dra-a40']), ('restore.sh', []), ('remove-provider-slices.sh', [])])
def test_provider_guard_precedes_every_kubectl_call(fake_provider_cluster, script, args):
    cluster = fake_provider_cluster
    cluster[3].pop('I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER')
    result = run_provider(cluster, script, *args)
    assert result.returncode != 0
    assert 'I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1' in result.stderr

    assert read_actions(cluster[2]) == []


@pytest.mark.parametrize('script,args', [('exclude-node.sh', ['dra-a40']), ('restore.sh', []), ('remove-provider-slices.sh', [])])
def test_provider_requires_explicit_backup_selection(fake_provider_cluster, script, args):
    cluster = fake_provider_cluster
    cluster[3].pop('OUT_DIR')
    result = run_provider(cluster, script, *args)
    assert result.returncode != 0 and 'OUT_DIR or RUN_ID' in result.stderr
    assert read_actions(cluster[2]) == []


@pytest.mark.parametrize('controller_deletes', [False, True])
def test_provider_cycle_preserves_all_affinity_terms_and_protected_plugin(fake_provider_cluster, controller_deletes):
    cluster = fake_provider_cluster
    mutate(cluster, lambda s: s.update(controller_deletes_pod=controller_deletes))
    original = json.loads(cluster[1].read_text())['ds']
    backup(cluster)
    files = Path(cluster[3]['OUT_DIR']) / 'provider-backup'
    assert json.loads((files / 'original-daemonset.json').read_text()) == original
    assert (files / 'original-daemonset.yaml').is_file()
    assert (files / 'uid.txt').read_text().strip() == original['metadata']['uid']
    assert json.loads((files / 'update-strategy.json').read_text()) == original['spec']['updateStrategy']
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode == 0, result.stderr
    current = json.loads(cluster[1].read_text())
    spec = current['ds']['spec']
    assert spec['updateStrategy'] == {'type': 'OnDelete'}
    old_terms = original['spec']['template']['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
    terms = spec['template']['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
    assert len(terms) == len(old_terms) == 5
    for old, new in zip(old_terms, terms):
        assert new['matchExpressions'] == old['matchExpressions']
        assert new['matchFields'] == [{'key': 'metadata.name', 'operator': 'NotIn', 'values': ['dra-a40']}]
    assert spec['template']['spec']['nodeSelector'] == {'site': 'fixture'}
    assert [p['metadata']['uid'] for p in current['pods'] if p['metadata']['namespace'] == NAMESPACE] == ['pod-dra-4090']
    assert [s['spec']['nodeName'] for s in current['slices']] == ['dra-4090']
    calls = read_actions(cluster[2])
    patches = [c for c in calls if 'patch' in c['args']]
    assert patches[0]['body'][:2] == [{'op': 'test', 'path': '/metadata/uid', 'value': 'native-ds'},
                                    {'op': 'test', 'path': '/spec', 'value': original['spec']}]
    deletes = [c for c in calls if 'delete' in c['args']]
    assert all(c['body']['preconditions'].get('uid') and c['body']['preconditions'].get('resourceVersion') for c in deletes)
    assert not any('dra-4090' in c['args'][c['args'].index('--raw') + 1] for c in deletes)
    result = run_provider(cluster, 'restore.sh')
    assert result.returncode == 0, result.stderr
    current = json.loads(cluster[1].read_text())
    assert current['ds']['metadata']['uid'] == 'native-ds'
    assert current['ds']['spec'] == original['spec']
    assert next(p for p in current['pods'] if p['spec']['nodeName'] == 'dra-4090')['metadata']['uid'] == 'pod-dra-4090'
    assert {s['spec']['nodeName'] for s in current['slices']} == {'dra-a40', 'dra-4090'}
    patches = [c for c in read_actions(cluster[2]) if 'patch' in c['args']]
    assert patches[-2]['body'][-1]['value']['updateStrategy'] == {'type': 'OnDelete'}
    assert patches[-1]['body'][-1]['value']['updateStrategy'] == original['spec']['updateStrategy']
    assert json.loads((files / 'restored.json').read_text())['uid'] == 'native-ds'
    count = len(read_actions(cluster[2]))
    result = run_provider(cluster, 'restore.sh')
    assert result.returncode == 0, result.stderr
    assert not any('patch' in c['args'] or 'delete' in c['args'] for c in read_actions(cluster[2])[count:])


def test_existing_backup_is_not_overwritten_after_exclusion(fake_provider_cluster):
    cluster = fake_provider_cluster
    backup(cluster)
    saved = Path(cluster[3]['OUT_DIR']) / 'provider-backup/original-daemonset.json'
    before = saved.read_bytes()
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode == 0, result.stderr
    result = run_provider(cluster, 'backup.sh')
    assert result.returncode == 0, result.stderr
    assert saved.read_bytes() == before


@pytest.mark.parametrize('change,message', [('uid', 'DaemonSet UID changed'), ('spec', 'DaemonSet spec changed')])
def test_provider_exclusion_refuses_concurrent_identity_or_spec_change(fake_provider_cluster, change, message):
    cluster = fake_provider_cluster
    backup(cluster)
    if change == 'uid':
        mutate(cluster, lambda s: s['ds']['metadata'].update(uid='replacement'))
    else:
        mutate(cluster, lambda s: s['ds']['spec']['template']['spec']['nodeSelector'].update(foreign='true'))
    start = len(read_actions(cluster[2]))
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode != 0 and message in result.stderr
    assert not any('patch' in c['args'] or 'delete' in c['args'] for c in read_actions(cluster[2])[start:])


def test_provider_exclusion_refuses_changed_protected_pod(fake_provider_cluster):
    cluster = fake_provider_cluster
    backup(cluster)
    mutate(cluster, lambda s: s['pods'][1]['metadata'].update(uid='restarted-4090'))
    start = len(read_actions(cluster[2]))
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode != 0 and 'protected plugin identity changed' in result.stderr
    assert not any('patch' in c['args'] or 'delete' in c['args'] for c in read_actions(cluster[2])[start:])


@pytest.mark.parametrize('race', ['patch_race', 'delete_race'])
def test_provider_atomic_preconditions_reject_races(fake_provider_cluster, race):
    cluster = fake_provider_cluster
    backup(cluster)
    mutate(cluster, lambda s: s.update({race: True}))
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode != 0
    state = json.loads(cluster[1].read_text())
    assert next(p for p in state['pods'] if p['spec']['nodeName'] == 'dra-4090')['metadata']['uid'] == 'pod-dra-4090'
    if race == 'patch_race':
        assert state['ds']['spec']['updateStrategy']['type'] == 'RollingUpdate'
    else:
        assert next(p for p in state['pods'] if p['spec']['nodeName'] == 'dra-a40')['metadata']['uid'] == 'replacement-pod'
        assert len(state['slices']) == 2


@pytest.mark.parametrize('race', ['Conflict-terminating', 'Conflict-gone', 'NotFound-terminating', 'NotFound-gone'])
def test_provider_exclusion_waits_after_controller_delete_race(fake_provider_cluster, race):
    cluster = fake_provider_cluster
    backup(cluster)
    mutate(cluster, lambda s: s.update(pod_delete_failure=race))
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode == 0, result.stderr
    state = json.loads(cluster[1].read_text())
    assert [p['metadata']['uid'] for p in state['pods'] if p['metadata']['namespace'] == NAMESPACE] == ['pod-dra-4090']
    assert [s['spec']['nodeName'] for s in state['slices']] == ['dra-4090']
    deletes = [row for row in read_actions(cluster[2]) if '--raw' in row['args']]
    assert len([row for row in deletes if '/pods/' in row['args'][row['args'].index('--raw') + 1]]) == 1


@pytest.mark.parametrize('race,message', [('Conflict-rv-drift', 'without termination'),
                                         ('Conflict-replaced', 'identity changed'),
                                         ('NotFound-replaced', 'identity changed')])
def test_provider_delete_race_recheck_refuses_unrelated_change(fake_provider_cluster, race, message):
    cluster = fake_provider_cluster
    backup(cluster)
    mutate(cluster, lambda s: s.update(pod_delete_failure=race))
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode != 0 and message in result.stderr
    state = json.loads(cluster[1].read_text())
    assert len(state['slices']) == 2
    deletes = [row for row in read_actions(cluster[2]) if '--raw' in row['args']]
    assert len(deletes) == 1


def test_provider_delete_error_other_than_conflict_or_notfound_is_not_recovered(fake_provider_cluster):
    cluster = fake_provider_cluster
    backup(cluster)
    mutate(cluster, lambda s: s.update(pod_delete_failure='Forbidden-terminating'))
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode != 0 and '(Forbidden)' in result.stderr
    assert len(json.loads(cluster[1].read_text())['slices']) == 2
    assert len([row for row in read_actions(cluster[2]) if '--raw' in row['args']]) == 1


@pytest.mark.parametrize('allocated', [False, True])
def test_provider_restore_refuses_residual_claims_without_mutation(fake_provider_cluster, allocated):
    cluster = fake_provider_cluster
    backup(cluster)
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode == 0, result.stderr
    claim = {'metadata': {'name': 'memory-reservation', 'namespace': 'gpu-isolation-lab'}}
    if allocated:
        claim['metadata']['namespace'] = 'another-workload'
        claim['status'] = {'allocation': {'devices': {'results': [{'driver': 'gpu.nvidia.com', 'pool': 'dra-a40', 'device': 'gpu-0'}]}}}
    mutate(cluster, lambda s: s['claims'].append(claim))
    start = len(read_actions(cluster[2]))
    result = run_provider(cluster, 'restore.sh')
    assert result.returncode != 0 and 'residual ResourceClaim' in result.stderr
    assert not any('patch' in c['args'] or 'delete' in c['args'] for c in read_actions(cluster[2])[start:])


def test_provider_restore_refuses_alternative_daemonset_even_with_no_ready_pod(fake_provider_cluster):
    cluster = fake_provider_cluster
    backup(cluster)
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode == 0, result.stderr
    alternate = native_ds()
    alternate['metadata'].update(name='a40-consumable-provider', uid='alternate-ds', labels={
        'app.kubernetes.io/part-of': 'dra-book', 'app.kubernetes.io/component': 'consumable-provider'})
    alternate['spec']['template']['spec']['nodeSelector'] = {'kubernetes.io/hostname': 'dra-a40'}
    mutate(cluster, lambda s: s['other_ds'].append(alternate))
    start = len(read_actions(cluster[2]))
    result = run_provider(cluster, 'restore.sh')
    assert result.returncode != 0 and 'alternative provider' in result.stderr
    assert not any('patch' in c['args'] or 'delete' in c['args'] for c in read_actions(cluster[2])[start:])


def test_provider_stale_slice_deletion_refuses_changed_uid(fake_provider_cluster):
    cluster = fake_provider_cluster
    backup(cluster)
    mutate(cluster, lambda s: s['slices'][0]['metadata'].update(uid='foreign-slice'))
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode != 0 and 'ResourceSlice identity or spec changed' in result.stderr
    assert len(json.loads(cluster[1].read_text())['slices']) == 2


def test_provider_stale_slice_deletion_refuses_changed_node_owner(fake_provider_cluster):
    cluster = fake_provider_cluster
    backup(cluster)
    mutate(cluster, lambda s: s['slices'][0]['metadata']['ownerReferences'][0].update(uid='foreign-node'))
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode != 0 and 'ResourceSlice identity or spec changed' in result.stderr
    assert len(json.loads(cluster[1].read_text())['slices']) == 2


def test_provider_exclusion_is_idempotent_without_a_new_backup(fake_provider_cluster):
    cluster = fake_provider_cluster
    backup(cluster)
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode == 0, result.stderr
    start = len(read_actions(cluster[2]))
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode == 0, result.stderr
    assert not any('patch' in c['args'] or 'delete' in c['args'] for c in read_actions(cluster[2])[start:])


def test_provider_affinity_append_preserves_existing_fields_and_rejects_empty_or_terms():
    spec = importlib.util.spec_from_file_location('lab04_provider_control', PROVIDER / 'control.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    original = native_ds()['spec']
    terms = original['template']['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
    terms[0]['matchFields'] = [{'key': 'metadata.name', 'operator': 'In', 'values': ['dra-a40', 'dra-4090']}]
    changed = helper.excluded_spec(original, 'dra-a40')
    assert terms[0]['matchFields'] == [{'key': 'metadata.name', 'operator': 'In', 'values': ['dra-a40', 'dra-4090']}]
    changed_terms = changed['template']['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
    assert changed_terms[0]['matchFields'][:1] == terms[0]['matchFields']
    terms.clear()
    with pytest.raises(ValueError, match='nodeSelectorTerms'):
        helper.excluded_spec(original, 'dra-a40')


def test_provider_restore_allows_unscheduled_original_daemonset_pod():
    spec = importlib.util.spec_from_file_location('lab04_provider_pending', PROVIDER / 'control.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    original = native_ds()
    pending = native_pod('dra-a40')
    pending['spec'].pop('nodeName')
    pending['spec']['containers'][0]['image'] = 'nvcr.io/nvidia/dra-driver-nvidia-gpu:v0.5.0'
    pending['status'] = {'phase': 'Pending'}
    helper.check_alternatives({'items': [pending]}, {'items': [original]}, original, {'target_node': 'dra-a40'})


@pytest.mark.parametrize('field,value', [('gpu_used_mib', 65), ('compute_mode', 'Exclusive_Process'), ('compute_apps', '123, python, 32\n')])
@pytest.mark.parametrize('script', ['exclude-node.sh', 'restore.sh'])
def test_provider_requires_physical_idle_before_any_spec_mutation(fake_provider_cluster, field, value, script):
    cluster = fake_provider_cluster
    backup(cluster)
    if script == 'restore.sh':
        result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
        assert result.returncode == 0, result.stderr
    mutate(cluster, lambda s: s.update({field: value}))
    start = len(read_actions(cluster[2]))
    result = run_provider(cluster, script, *(['dra-a40'] if script == 'exclude-node.sh' else []))
    assert result.returncode != 0 and 'GPU must be idle in Default mode' in result.stderr
    assert not any('patch' in c['args'] or 'delete' in c['args'] for c in read_actions(cluster[2])[start:])


def test_provider_original_idle_boundary_includes_64_mib(fake_provider_cluster):
    cluster = fake_provider_cluster
    backup(cluster)
    mutate(cluster, lambda s: s.update(gpu_used_mib=64))
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('driver', ['gpu.nvidia.com', 'hami-core-gpu.project-hami.io'])
def test_provider_removes_only_snapshotted_alternative_slices_after_provider_is_absent(fake_provider_cluster, driver):
    cluster = fake_provider_cluster
    backup(cluster)
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode == 0, result.stderr
    alternate = native_slice('dra-a40', 'alternate-slice')
    alternate['metadata']['name'] = 'alternative-publication'
    alternate['spec']['driver'] = driver
    alternate['spec']['devices'][0]['allowMultipleAllocations'] = True
    mutate(cluster, lambda s: s['slices'].append(alternate))
    cluster[3]['DRIVER'] = driver
    start = len(read_actions(cluster[2]))
    result = run_provider(cluster, 'remove-provider-slices.sh')
    assert result.returncode == 0, result.stderr
    state = json.loads(cluster[1].read_text())
    assert [s['spec']['nodeName'] for s in state['slices']] == ['dra-4090']
    backup_dir = Path(cluster[3]['OUT_DIR']) / 'provider-backup'
    assert json.loads((backup_dir / 'alternative-slices.json').read_text()) == {'items': [alternate]}
    deletes = [c for c in read_actions(cluster[2])[start:] if 'delete' in c['args']]
    assert len(deletes) == 1
    assert deletes[0]['body']['preconditions'] == {'uid': 'alternate-slice', 'resourceVersion': '1'}
    start = len(read_actions(cluster[2]))
    result = run_provider(cluster, 'remove-provider-slices.sh')
    assert result.returncode == 0, result.stderr
    assert not any('delete' in c['args'] for c in read_actions(cluster[2])[start:])
    result = run_provider(cluster, 'restore.sh')
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('change', ['pool', 'uuid', 'owner'])
def test_provider_alternative_slice_cleanup_refuses_unknown_device_or_node_identity(fake_provider_cluster, change):
    cluster = fake_provider_cluster
    backup(cluster)
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode == 0, result.stderr
    alternate = native_slice('dra-a40', 'alternate-slice')
    alternate['metadata']['name'] = 'alternative-publication'
    if change == 'pool':
        alternate['spec']['pool']['name'] = 'other-pool'
    elif change == 'uuid':
        alternate['spec']['devices'][0]['attributes']['uuid']['string'] = 'GPU-other'
    else:
        alternate['metadata']['ownerReferences'][0]['uid'] = 'other-node'
    mutate(cluster, lambda s: s['slices'].append(alternate))
    start = len(read_actions(cluster[2]))
    result = run_provider(cluster, 'remove-provider-slices.sh')
    assert result.returncode != 0 and 'known target GPU publication' in result.stderr
    assert not any('delete' in c['args'] for c in read_actions(cluster[2])[start:])


def test_provider_alternative_slice_cleanup_refuses_original_provider_template(fake_provider_cluster):
    cluster = fake_provider_cluster
    backup(cluster)
    start = len(read_actions(cluster[2]))
    result = run_provider(cluster, 'remove-provider-slices.sh')
    assert result.returncode != 0 and 'native provider must remain excluded' in result.stderr
    assert not any('delete' in c['args'] for c in read_actions(cluster[2])[start:])
@pytest.mark.parametrize('script', ['install-provider.sh', 'uninstall-provider.sh'])
def test_alternative_provider_mutations_require_explicit_cluster_guard(tmp_path, script):
    path = ROOT / 'labs/04-isolation/consumable-shares/scripts' / script
    assert path.is_file(), 'alternative provider scripts have not been implemented'
    binary = tmp_path / 'bin'
    binary.mkdir()
    trace = tmp_path / 'kubectl-calls'
    fake = binary / 'kubectl'
    fake.write_text('#!/bin/sh\nprintf called >> "$U4_KUBECTL_TRACE"\nexit 99\n')
    fake.chmod(0o755)
    env = os.environ.copy()
    env.pop('I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER', None)
    env.update(PATH=str(binary) + os.pathsep + env['PATH'], U4_KUBECTL_TRACE=str(trace))
    result = subprocess.run(['bash', str(path), 'guard-test'], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1' in result.stderr
    assert not trace.exists()


@pytest.mark.parametrize('unsafe', ['protected-uid', 'active-gpu'])
def test_uninstall_checks_protected_uid_and_idle_before_provider_deletion(fake_provider_cluster, unsafe):
    cluster = fake_provider_cluster
    backup(cluster)
    result = run_provider(cluster, 'exclude-node.sh', 'dra-a40')
    assert result.returncode == 0, result.stderr
    lab = cluster[0]
    shutil.copytree(ROOT / 'labs/04-isolation/consumable-shares', lab / 'consumable-shares')
    if unsafe == 'protected-uid':
        mutate(cluster, lambda s: s['pods'][0]['metadata'].update(uid='replaced-4090'))
    else:
        mutate(cluster, lambda s: s.update(gpu_used_mib=65))
    before = len(read_actions(cluster[2]))
    result = subprocess.run(['bash', str(lab / 'consumable-shares/scripts/uninstall-provider.sh'), 'fixture'],
                            env=cluster[3], text=True, capture_output=True, timeout=8)
    assert result.returncode != 0
    assert not any('delete' in row['args'] for row in read_actions(cluster[2])[before:]), result.stderr
    assert ('protected' in result.stderr if unsafe == 'protected-uid' else 'idle' in result.stderr)


def test_uninstall_resumes_interrupted_two_stage_native_restore(fake_provider_cluster):
    cluster = fake_provider_cluster
    backup(cluster)
    assert run_provider(cluster, 'exclude-node.sh', 'dra-a40').returncode == 0
    def resumed(state):
        state['ds']['spec'] = copy.deepcopy(native_ds()['spec'])
        state['ds']['spec']['updateStrategy'] = {'type': 'OnDelete'}
        state['pods'].append(native_pod('dra-a40', 'restored-a40-pod'))
        state['slices'].append(native_slice('dra-a40', 'restored-a40-slice'))
    mutate(cluster, resumed)
    lab = cluster[0]
    shutil.copytree(ROOT / 'labs/04-isolation/consumable-shares', lab / 'consumable-shares')
    before = len(read_actions(cluster[2]))
    result = subprocess.run(['bash', str(lab / 'consumable-shares/scripts/uninstall-provider.sh'), 'fixture'],
                            env=cluster[3], text=True, capture_output=True, timeout=8)
    assert result.returncode == 0, result.stderr
    assert json.loads(cluster[1].read_text())['ds']['spec'] == native_ds()['spec']
    assert not any('delete' in row['args'] for row in read_actions(cluster[2])[before:])


@pytest.mark.parametrize('native_pod_present', [False, True])
def test_uninstall_waits_for_native_pod_and_slices_after_aborted_install(fake_provider_cluster, native_pod_present):
    cluster = fake_provider_cluster
    backup(cluster)
    spec = importlib.util.spec_from_file_location('lab04_aborted_install', PROVIDER / 'control.py')
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    def abort_exclusion(state):
        state['ds']['spec'] = helper.excluded_spec(state['ds']['spec'], 'dra-a40')
        state['pods'][0]['metadata']['deletionTimestamp'] = '2026-10-11T17:10:15Z'
        if not native_pod_present:
            state['pods'].pop(0)
        state.update(native_pod_gone_after_reads=3, native_slices_gone_after_reads=5)
    mutate(cluster, abort_exclusion)
    cluster[3]['PROVIDER_TIMEOUT'] = '5'
    lab = cluster[0]
    shutil.copytree(ROOT / 'labs/04-isolation/consumable-shares', lab / 'consumable-shares')
    before = len(read_actions(cluster[2]))
    result = subprocess.run(['bash', str(lab / 'consumable-shares/scripts/uninstall-provider.sh'), 'fixture'],
                            env=cluster[3], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    state = json.loads(cluster[1].read_text())
    assert state['ds']['metadata']['uid'] == 'native-ds'
    assert state['ds']['spec'] == native_ds()['spec']
    assert state['native_cleanup_reads'] >= 5
    assert next(p for p in state['pods'] if p['spec']['nodeName'] == 'dra-4090')['metadata']['uid'] == 'pod-dra-4090'
    assert not any('--raw' in row['args'] for row in read_actions(cluster[2])[before:])


@pytest.mark.parametrize('native_pod_present', [False, True])
def test_uninstall_native_cleanup_timeout_explains_safe_retry(fake_provider_cluster, native_pod_present):
    cluster = fake_provider_cluster
    backup(cluster)
    spec = importlib.util.spec_from_file_location('lab04_aborted_timeout', PROVIDER / 'control.py')
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    mutate(cluster, lambda s: s['ds'].update(spec=helper.excluded_spec(s['ds']['spec'], 'dra-a40')))
    mutate(cluster, lambda s: s['pods'][0]['metadata'].update(deletionTimestamp='2026-10-11T17:10:15Z'))
    if not native_pod_present:
        mutate(cluster, lambda s: s['pods'].pop(0))
    lab = cluster[0]
    shutil.copytree(ROOT / 'labs/04-isolation/consumable-shares', lab / 'consumable-shares')
    before = len(read_actions(cluster[2]))
    result = subprocess.run(['bash', str(lab / 'consumable-shares/scripts/uninstall-provider.sh'), 'fixture'],
                            env=cluster[3], text=True, capture_output=True, timeout=8)
    assert result.returncode != 0
    assert 'did not disappear within 1s' in result.stderr
    assert 'exclude-node.sh dra-a40' in result.stderr and 'OUT_DIR' in result.stderr
    calls = read_actions(cluster[2])[before:]
    assert not any('--raw' in row['args'] or 'patch' in row['args'] for row in calls)


def test_alternative_provider_readiness_requires_daemonset_owner_uid_and_a40():
    import yaml
    path = ROOT / 'labs/04-isolation/common/analysis/control.py'
    spec = importlib.util.spec_from_file_location('lab04_alternative', path)
    control = importlib.util.module_from_spec(spec); spec.loader.exec_module(control)
    ds = yaml.safe_load((ROOT / 'labs/04-isolation/consumable-shares/manifests/provider/daemonset.yaml').read_text())
    ds['metadata']['uid'] = 'alternative-ds'
    pod = {'metadata': {'name': 'a40-alternative', 'namespace': NAMESPACE,
                        'ownerReferences': [{'kind': 'DaemonSet', 'uid': 'alternative-ds', 'controller': True}]},
           'spec': copy.deepcopy(ds['spec']['template']['spec']),
           'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}
    pod['spec']['nodeName'] = 'dra-a40'
    assert control.provider_ready(ds, {'items': [pod]})
    pod['metadata']['ownerReferences'][0]['uid'] = 'older-alternative-ds'
    assert not control.provider_ready(ds, {'items': [pod]})
    pod['metadata']['ownerReferences'][0]['uid'] = 'alternative-ds'
    pod['status']['conditions'][0]['status'] = 'False'
    assert not control.provider_ready(ds, {'items': [pod]})
    pod['spec']['nodeName'] = 'dra-4090'
    with pytest.raises(ValueError, match='A40'):
        control.provider_ready(ds, {'items': [pod]})
