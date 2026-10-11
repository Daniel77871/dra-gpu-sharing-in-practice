import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest
import yaml

from conftest import ROOT

LAB = ROOT / 'labs/04-isolation'


def control_module():
    path = LAB / 'common/analysis/control.py'
    assert path.is_file(), 'shared runtime controller has not been implemented'
    spec = importlib.util.spec_from_file_location('lab04_control', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mps_module():
    path = LAB / 'common/analysis/mps.py'
    assert path.is_file(), 'MPS effective-limit and client-identity checks missing'
    spec = importlib.util.spec_from_file_location('lab04_mps', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gpu_idle_requires_original_64_mib_bound_and_no_a40_mps_daemon():
    control = control_module()
    gpu = control.GPU_UUID + ', NVIDIA A40, 46068, 64, 45426, 0, Default\n'
    other = {'kind': 'Pod', 'metadata': {'name': 'mps-control-daemon-other'},
             'spec': {'nodeName': 'dra-4090'}}
    assert control.gpu_idle(gpu, '', {'items': [other]})
    assert not control.gpu_idle(gpu.replace(', 64,', ', 65,'), '', {'items': []})
    assert not control.gpu_idle(gpu.replace('Default', 'Exclusive_Process'), '', {'items': []})
    assert not control.gpu_idle(gpu, '999, python, 30\n', {'items': []})
    for daemon in [
        {'kind': 'Pod', 'metadata': {'name': 'mps-control-daemon-claim'},
         'spec': {'nodeName': 'dra-a40'}},
        {'kind': 'Deployment', 'metadata': {'name': 'mps-control-daemon-claim'},
         'spec': {'template': {'spec': {'nodeName': 'dra-a40'}}}},
    ]:
        assert not control.gpu_idle(gpu, '', {'items': [daemon]})
    with pytest.raises(ValueError, match='UUID'):
        control.gpu_idle(gpu.replace(control.GPU_UUID, 'GPU-other'), '', {'items': []})


def test_gpu_cleanup_waits_for_deployment_removal_after_clients_exit(tmp_path):
    control = control_module()
    gpu = tmp_path / 'idle.csv'
    gpu.write_text(control.GPU_UUID + ', NVIDIA A40, 46068, 0, 45490, 0, Default\n')
    before = tmp_path / 'daemons-before.json'
    before.write_text(json.dumps({'items': [{'kind': 'Deployment', 'metadata': {'name': 'mps-control-daemon-claim'},
                                            'spec': {'template': {'spec': {'nodeName': 'dra-a40'}}}}]}))
    script = f'''
source {shlex.quote(str(LAB / 'common/scripts/lib.sh'))}
ROUND_DIR={shlex.quote(str(tmp_path))}
queries=0
gpu_snapshot() {{
    cp {shlex.quote(str(gpu))} "$ROUND_DIR/gpu-$1.txt"
    : > "$ROUND_DIR/processes-$1.txt"
}}
kc() {{
    queries=$((queries + 1))
    if [[ "$queries" == 1 ]]; then cat {shlex.quote(str(before))}
    else printf '{{"items":[]}}\\n'; fi
}}
wait_gpu_clean after
printf '%s\\n' "$queries"
'''
    result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '2'


def test_mps_effective_limits_and_clients_are_checked_independently_of_claim_yaml():
    mps = mps_module()
    raw = {'get_server_list': '321\n', 'get_default_device_pinned_mem_limit ' + mps.GPU_UUID: '8G\n',
           'get_device_pinned_mem_limit 321 ' + mps.GPU_UUID: '8192M\n',
           'get_active_thread_percentage 321': '100.0\n', 'get_default_active_thread_percentage': '100.0\n',
           'get_client_list 321': '401\n402\n403\n'}
    assert mps.readback(raw, 8, 3)['clients'] == [401, 402, 403]
    changed = dict(raw); changed['get_device_pinned_mem_limit 321 ' + mps.GPU_UUID] = '4G\n'
    with pytest.raises(ValueError, match='limit'):
        mps.readback(changed, 8, 3)
    with pytest.raises(ValueError, match='clients'):
        mps.readback(raw, 8, 2)
    changed = dict(raw); changed['get_server_list'] = '321\n999\n'
    with pytest.raises(ValueError, match='server'):
        mps.readback(changed, 8, 3)


def test_mps_host_pid_mapping_requires_pod_uid_container_id_and_cuda_ready_identity():
    mps = mps_module()
    pod = {'metadata': {'name': 'quota-pressure-example', 'uid': 'pod-b'},
           'status': {'containerStatuses': [{'name': 'workload', 'containerID': 'cri-o://container-b'}]}}
    ready = [{'event': 'B_READY', 'pid': 40, 'uid': 0, 'pod_name': 'quota-pressure-example',
              'gpu_uuid': mps.GPU_UUID, 'client_id': '0'}]
    status = 'Uid:\t0\t0\t0\t0\nNSpid:\t402\t40\n'
    record = mps.map_client(402, status, '0::/kubepods-pod-b-container-b\n', 'python\0/opt/lab/isolation_workload.py\0', [pod], ready)
    assert record['pod_uid'] == 'pod-b' and record['namespace_pid'] == 40
    for cgroup in ('0::/kubepods-other-pod-container-b\n', '0::/kubepods-pod-b-other-container\n'):
        with pytest.raises(ValueError, match='exactly one'):
            mps.map_client(402, status, cgroup, 'python /opt/lab/isolation_workload.py', [pod], ready)


def test_real_dual_mps_clients_map_configmap_projection_paths_without_pid_reordering():
    mps = mps_module()
    fixture = ROOT / 'data/e2/fixtures/mps-scope-20261012'
    pods = [json.loads((fixture / (prefix + '-current.json')).read_text()) for prefix in ('a', 'b')]
    ready = [json.loads(line) for prefix in ('a', 'b')
             for line in (fixture / ('task-' + prefix + '.log')).read_text().splitlines()]
    result = mps.readback(json.loads((fixture / 'mps-control.json').read_text()), 8, 3)
    assert result['server_pid'] == 135455
    mappings = [mps.map_client(pid, *((fixture / f'client-{pid}.{field}.txt').read_text()
                                     for field in ('status', 'cgroup', 'cmdline')), pods, ready)
                for pid in result['clients']]
    assert [(row['host_pid'], row['namespace_pid'], row['pod_uid'], row['client_id'])
            for row in mappings] == [
                (135414, 1, pods[0]['metadata']['uid'], 'main'),
                (135565, 41, pods[1]['metadata']['uid'], '1'),
                (135564, 40, pods[1]['metadata']['uid'], '0')]
    # A mismatched read is still rejected; a projected path must not weaken
    # the Pod/container/PID identity checks.
    with pytest.raises(ValueError, match='exactly one'):
        mps.map_client(135565, (fixture / 'client-135565.status.txt').read_text(),
                       (fixture / 'client-135414.cgroup.txt').read_text(),
                       (fixture / 'client-135565.cmdline.txt').read_text(), pods, ready)


def test_mps_host_pid_mapping_rejects_status_from_another_pid_namespace():
    mps = mps_module()
    fixture = ROOT / 'data/e2/fixtures/mps-scope-20261012'
    pod = json.loads((fixture / 'b-current.json').read_text())
    ready = [json.loads(line) for line in (fixture / 'task-b.log').read_text().splitlines()]
    with pytest.raises(ValueError, match='host PID'):
        mps.map_client(135565, (fixture / 'client-135564.status.txt').read_text(),
                       (fixture / 'client-135565.cgroup.txt').read_text(),
                       (fixture / 'client-135565.cmdline.txt').read_text(), [pod], ready)


def test_split_preserves_two_independent_claims_and_job_roles(tmp_path):
    control = control_module()
    items = [{'kind': 'Namespace', 'metadata': {'name': 'gpu-isolation-lab'}},
             {'kind': 'ConfigMap', 'metadata': {'name': 'isolation-code'}},
             *[{'kind': 'ResourceClaim', 'metadata': {'name': name}} for name in ('steady-gpu', 'pressure-gpu')],
             *[{'kind': 'Job', 'metadata': {'name': name}} for name in ('steady-service', 'quota-pressure')]]
    control.split_manifests(items, tmp_path)
    assert len(json.loads((tmp_path / 'claims.json').read_text())['items']) == 2
    assert json.loads((tmp_path / 'steady-service.json').read_text())['metadata']['name'] == 'steady-service'
    assert len(json.loads((tmp_path / 'base.json').read_text())['items']) == 2


def test_event_wait_accepts_terminated_logs_and_matches_ack_nonce(tmp_path):
    control = control_module()
    assert not control.logs_ready({'status': {'containerStatuses': [{'name': 'workload', 'state': {'waiting': {'reason': 'ContainerCreating'}}}]}})
    assert control.logs_ready({'status': {'containerStatuses': [{'name': 'workload', 'state': {'terminated': {'exitCode': 0}}}]}})
    path = tmp_path / 'task-a.log'
    path.write_text('\n'.join(json.dumps(row) for row in [
        {'event': 'CONTROL_ACK', 'command_id': 'old'}, {'event': 'CONTROL_ACK', 'command_id': 'new'}]))
    assert control.find_event(path, 'CONTROL_ACK', 'new')['command_id'] == 'new'
    assert control.find_event(path, 'CONTROL_ACK', 'missing') is None


def test_ownership_refuses_foreign_namespace_and_leftover_workload():
    control = control_module()
    with pytest.raises(ValueError, match='ownership'):
        control.ownership({'metadata': {'labels': {}}}, {'items': []})
    namespace = {'metadata': {'labels': {'app.kubernetes.io/part-of': 'dra-book'}}}
    with pytest.raises(ValueError, match='previous'):
        control.ownership(namespace, {'items': [{'kind': 'Job', 'metadata': {'name': 'quota-pressure', 'labels': {'app.kubernetes.io/part-of': 'dra-book'}}}]})


def test_run_labels_prevent_old_cleanup_from_adopting_new_workloads(tmp_path):
    control = control_module()
    items = [{'kind': 'Namespace', 'metadata': {'name': 'gpu-isolation-lab'}},
             {'kind': 'Job', 'metadata': {'name': 'quota-pressure'}, 'spec': {'template': {'metadata': {}}}},
             {'kind': 'ConfigMap', 'metadata': {'name': 'isolation-code'}}]
    control.stamp_run(items, 'new-run')
    assert 'app.kubernetes.io/instance' not in items[0]['metadata'].get('labels', {})
    assert items[1]['spec']['template']['metadata']['labels']['app.kubernetes.io/instance'] == 'new-run'
    with pytest.raises(ValueError, match='different run'):
        control.cleanup_owner({'items': items[1:]}, 'old-run')
    control.cleanup_owner({'items': items[1:]}, 'new-run')


def test_old_completed_run_cleanup_refuses_new_run_before_any_delete(tmp_path):
    import shutil
    lab = tmp_path / 'labs/04-isolation'
    shutil.copytree(LAB / 'common', lab / 'common', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'labs/common', lab.parent / 'common')
    old = lab / 'out/old-run/boundary-8g-r1'; old.mkdir(parents=True)
    (old / 'trial.json').write_text('{"case":"boundary","backend":"mps"}')
    (old / 'cleaned.json').write_text('{"cleaned":true}')
    binary = tmp_path / 'bin'; binary.mkdir()
    trace = tmp_path / 'unsafe-delete'
    fake = binary / 'kubectl'
    fake.write_text('#!/bin/sh\ncase "$*" in\n' +
                    '*"get namespace"*) printf \'{"metadata":{"labels":{"app.kubernetes.io/part-of":"dra-book"}}}\\n\';;\n' +
                    '*"get jobs,pods"*) printf \'{"items":[{"kind":"Job","metadata":{"name":"quota-pressure","labels":{"app.kubernetes.io/part-of":"dra-book","app.kubernetes.io/instance":"new-run"}}}]}\\n\';;\n' +
                    '*"delete"*) printf unsafe > "$U4_DELETE_TRACE";;\n*) printf \'{"items":[]}\\n\';;\nesac\n')
    fake.chmod(0o755)
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'], 'U4_DELETE_TRACE': str(trace)}
    result = subprocess.run(['bash', str(lab / 'common/scripts/cleanup.sh'), 'old-run'],env=env,
                            text=True,capture_output=True,timeout=5)
    assert result.returncode == 2 and 'different run' in result.stderr
    assert not trace.exists()


def test_mps_claim_and_jobs_preserve_original_topology():
    claim_path = LAB / 'mps/manifests/01-shared-claim.yaml'
    assert claim_path.is_file(), 'MPS manifests have not been implemented'
    claim = yaml.safe_load(claim_path.read_text())
    exactly = claim['spec']['devices']['requests'][0]['exactly']
    assert exactly['count'] == 1 and 'capacity' not in exactly
    sharing = claim['spec']['devices']['config'][0]['opaque']['parameters']['sharing']
    assert sharing == {'strategy': 'MPS', 'mpsConfig': {'defaultActiveThreadPercentage': 100, 'defaultPinnedDeviceMemoryLimit': '8Gi', 'multiUser': False}}
    for filename, role in [('02-steady-service.yaml', 'survivor'), ('03-quota-pressure.yaml', 'pressure'), ('04-dual-pressure.yaml', 'scope')]:
        job = yaml.safe_load((LAB / 'mps/manifests' / filename).read_text())
        assert job['kind'] == 'Job' and job['spec']['backoffLimit'] == 0
        pod = job['spec']['template']['spec']
        assert pod['restartPolicy'] == 'Never' and pod['automountServiceAccountToken'] is False
        assert pod['resourceClaims'][0]['resourceClaimName'] == claim['metadata']['name']
        container = pod['containers'][0]
        assert container['args'] == ['--role', role, '--quota-gib', '8']
        assert container['image'].endswith('@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755')
        assert {e['name']: e.get('value') for e in container['env']}['PYTORCH_NO_CUDA_MEMORY_CACHING'] == '1'
        assert container['resources']['requests'] == {'cpu': '2', 'memory': '4Gi'}


def test_target_rejects_wrong_sharing_mode_and_duplicate_publishers():
    control = control_module()
    device = {'name': 'gpu-0', 'attributes': {'uuid': {'string': control.GPU_UUID}, 'productName': {'string': 'NVIDIA A40'}}, 'allowMultipleAllocations': True,
              'capacity': {'memory': {'value': '46068Mi'}}}
    item = {'spec': {'driver': 'gpu.nvidia.com', 'nodeName': 'dra-a40', 'pool': {'name': 'dra-a40'}, 'devices': [device]}}
    with pytest.raises(ValueError, match='native'):
        control.target({'items': [item]}, 'mps')
    assert control.target({'items': [item]}, 'consumable-shares')['gpu_uuid'] == control.GPU_UUID
    with pytest.raises(ValueError, match='exactly one'):
        control.target({'items': [item, item]}, 'consumable-shares')


def test_existing_provider_spec_refuses_mount_affinity_or_privilege_drift():
    import copy
    control = control_module()
    expected = yaml.safe_load((LAB / 'consumable-shares/manifests/provider/daemonset.yaml').read_text())
    current = copy.deepcopy(expected)
    current['spec']['revisionHistoryLimit'] = 10
    current['spec']['minReadySeconds'] = 0
    current['spec']['template']['metadata']['creationTimestamp'] = None
    current['spec']['template']['spec']['serviceAccount'] = current['spec']['template']['spec']['serviceAccountName']
    expected['spec']['template']['metadata']['creationTimestamp'] = None
    control.verify_provider_spec(current, expected)
    for change in (
        lambda s: s['template']['spec']['containers'][0]['securityContext'].update(privileged=False),
        lambda s: s['template']['spec']['volumes'][0]['hostPath'].update(path='/unexpected'),
        lambda s: s['template']['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'][0]['matchFields'][0].update(values=['dra-4090']),
    ):
        changed = copy.deepcopy(current)
        change(changed['spec'])
        with pytest.raises(ValueError, match='provider spec'):
            control.verify_provider_spec(changed, expected)


def test_job_pod_snapshot_api_failure_propagates_from_command_substitution(tmp_path):
    harness = tmp_path / 'wait.sh'
    harness.write_text('set -euo pipefail\nsource ' + shlex.quote(str(LAB / 'common/scripts/lib.sh')) + '\n' +
                       'ROUND_DIR=' + shlex.quote(str(tmp_path)) + '\n' +
                       'kc() { if [[ "$*" == *"get pods"* ]]; then printf \'{"items":[{"metadata":{"name":"fixture"}}]}\\n\'; else return 17; fi; }\n' +
                       'pod="$(wait_job_pod steady-service a)"\n')
    result = subprocess.run(['bash', str(harness)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 17, result.stdout + result.stderr
    assert not (tmp_path / 'a-pod-name.txt').exists()


def test_partial_collection_preserves_evidence_and_allows_protocol_to_continue(tmp_path):
    lab = tmp_path / 'labs/04-isolation'
    for relative in ('common/scripts', 'common/analysis'):
        target = lab / relative
        target.mkdir(parents=True)
        for path in (LAB / relative).iterdir():
            if path.is_file():
                (target / path.name).write_bytes(path.read_bytes())
    (lab.parent / 'common').symlink_to(ROOT / 'labs/common', target_is_directory=True)
    trial = lab / 'out/partial/consumable-accounting-r1'
    trial.mkdir(parents=True)
    (trial / 'trial.json').write_text('{"backend":"consumable-shares","case":"accounting"}')
    binary = tmp_path / 'bin'; binary.mkdir()
    fake = binary / 'kubectl'
    fake.write_text('#!/bin/sh\nprintf \'{"items":[]}\\n\'\n')
    fake.chmod(0o755)
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH']}
    result = subprocess.run(['bash', str(lab / 'common/scripts/collect.sh'), 'partial'], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert (trial / 'events-final.json').exists()
    assert json.loads((lab / 'out/partial/summary.json').read_text())[0]['complete'] is False
    assert 'complete=' not in result.stdout


def test_finished_aborted_pressure_is_still_safe_to_collect_and_remove():
    control = control_module()
    pod = {'status': {'containerStatuses': [{'name': 'workload', 'state': {'terminated': {'exitCode': 2, 'reason': 'Error'}}}]}}
    assert control.finished(pod)


def test_runner_prints_each_final_round_once_after_cleanup(tmp_path):
    import shutil
    lab = tmp_path / 'labs/04-isolation'
    shutil.copytree(LAB / 'common', lab / 'common', ignore=shutil.ignore_patterns('__pycache__'))
    (lab.parent / 'common').symlink_to(ROOT / 'labs/common', target_is_directory=True)
    # Replace only the cluster protocol in this local orchestration test with
    # complete golden evidence. The actual runner and analyzer remain in use.
    with (lab / 'common/scripts/lib.sh').open('a') as stream:
        stream.write('\npreflight() { :; }\nruntime_round() {\n' +
                     '  cp -R ' + shlex.quote(str(ROOT / 'data/e2/raw')) + '/"$2-${3}g-r$4"/. "$ROUND_DIR/"\n' +
                     '  printf "cleanup-verified:%s\\n" "${ROUND_DIR##*/}"\n}\n')
    result = subprocess.run(['bash', str(lab / 'common/scripts/run.sh'), 'final-verdict'],
                            env={**os.environ, 'SMOKE': '1', 'CASE': 'all', 'BACKEND': 'mps',
                                 'LAB_DIR': str(lab / 'mps')}, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    for name, outcome in [('boundary-8g-r1', 'allocation_oom'), ('scope-8g-r1', 'target_reached')]:
        verdict = f'{name}: {outcome}; complete=True'
        assert lines.count(verdict) == 1, result.stdout
        assert lines.index('cleanup-verified:' + name) < lines.index(verdict)
    assert 'complete=False' not in result.stdout
    assert len(json.loads((lab / 'out/final-verdict/summary.json').read_text())) == 2


@pytest.mark.parametrize('foreign', [False, True])
def test_cleanup_deletes_empty_owned_namespace_and_preserves_foreign_contents(tmp_path, foreign):
    import shutil
    lab = tmp_path / 'labs/04-isolation'
    shutil.copytree(LAB / 'common', lab / 'common', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'labs/common', lab.parent / 'common')
    (lab.parent / 'common/gpu-idle-check.sh').write_text('#!/bin/bash\nexit 0\n')
    with (lab / 'common/scripts/lib.sh').open('a') as stream:
        stream.write('\nwait_gpu_clean() { :; }\n')
    trial = lab / 'out/namespace-cleanup/consumable-accounting-r1'
    shutil.copytree(ROOT / 'data/e2/raw/consumable-accounting-r1', trial)
    (trial / 'cleaned.json').write_text('{"cleaned":true}\n')
    binary = tmp_path / 'bin'; binary.mkdir()
    fake = binary / 'kubectl'
    fake.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['U4_CLEANUP_TRACE'], 'a') as stream: stream.write(json.dumps(args) + '\\n')
if 'api-resources' in args:
    print('serviceaccounts\\nconfigmaps\\nsecrets\\nevents\\nevents.events.k8s.io')
elif 'delete' in args: pass
elif 'namespace' in args:
    print(json.dumps({'metadata': {'labels': {'app.kubernetes.io/part-of': 'dra-book'}}}))
elif 'serviceaccounts' in args:
    print(json.dumps({'items': [{'metadata': {'name': 'default'}}]}))
elif 'configmaps' in args:
    print(json.dumps({'items': [{'metadata': {'name': 'kube-root-ca.crt'}}]}))
elif 'secrets' in args and os.environ['U4_FOREIGN'] == '1':
    print(json.dumps({'items': [{'metadata': {'name': 'foreign-secret'}}]}))
elif 'pods' in args and 'name' in args: pass
else: print('{"items":[]}')
''')
    fake.chmod(0o755)
    trace = tmp_path / 'kubectl.jsonl'
    result = subprocess.run(['bash', str(lab / 'common/scripts/cleanup.sh'), 'namespace-cleanup'],
                            env={**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                                 'U4_CLEANUP_TRACE': str(trace), 'U4_FOREIGN': str(int(foreign))},
                            text=True, capture_output=True, timeout=10)
    commands = [json.loads(line) for line in trace.read_text().splitlines()]
    namespace_deletes = [args for args in commands if 'delete' in args and 'namespace' in args]
    if foreign:
        assert result.returncode != 0 and 'foreign secrets/foreign-secret' in result.stderr
        assert not namespace_deletes
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(namespace_deletes) == 1
        assert '--wait=true' in namespace_deletes[0] and '--timeout=300s' in namespace_deletes[0]
