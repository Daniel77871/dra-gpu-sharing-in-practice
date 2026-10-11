"""HAMi evidence must preserve accounting scope and unfinished observations."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest
import yaml

from conftest import ROOT
from test_lab04_consumable import backup, fake_provider_cluster, native_ds, run_provider

ANALYSIS = ROOT / 'labs/04-isolation/common/analysis/hami.py'
E2 = ROOT / 'data/e2/hami/raw'
E2B = ROOT / 'data/e2b/raw'


def module():
    assert ANALYSIS.is_file(), 'HAMi analysis interface has not been implemented'
    spec = importlib.util.spec_from_file_location('hami_analysis_test', ANALYSIS)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_e2_runtime_recomputes_tensor_oom_physical_and_survivor_golden_values():
    hami = module()
    rows = [r for r in hami.analyze_run(E2) if r['case'] != 'accounting']
    assert len(rows) == 6
    single = [r for r in rows if r['case'] == 'boundary']
    dual = [r for r in rows if r['case'] == 'scope']
    assert [r['b_max_tensor_gib'] for r in single] == [7.5, 7.5, 7.5]
    assert [r['b_max_tensor_gib'] for r in dual] == [7.5, 7.5, 7.25]
    assert [r['cuda_oom_count'] for r in single] == [1, 1, 1]
    assert [r['cuda_oom_count'] for r in dual] == [2, 2, 2]
    assert [r['b_nvml_used_mib'] for r in single] == [7986, 7986, 7986]
    assert [r['b_nvml_used_mib'] for r in dual] == [8292, 8292, 8036]
    assert [r['b_nvml_mib_by_client'] for r in dual] == [
        {'0': 4146, '1': 4146}, {'0': 4146, '1': 4146}, {'0': 3890, '1': 4146}]
    assert [r['hold_probe_ok'] for r in single + dual] == [30, 30, 30, 30, 30, 29]
    assert sum(r['hold_probe_ok'] for r in rows) == 179
    assert all(r['hold_probe_oom'] == r['a_compute_errors'] == 0 for r in rows)
    assert all(r['complete'] and r['b_outcome'] == 'allocation_oom' for r in rows)
    assert all(r['reserved_cores'] == 100 and r['schedulable_remaining_cores'] == 0 for r in rows)
    assert all(r['schedulable_remaining_mib'] == 33780 for r in rows)
    assert all(r['libvgpu_present'] and r['cache_path_equal'] for r in dual)


def test_e2_accounting_keeps_memory_and_cores_balances_separate():
    rows = [r for r in module().analyze_run(E2) if r['case'] == 'accounting']
    assert len(rows) == 3
    for row in rows:
        assert row['published_mib'] == 46068
        assert row['published_cores'] == 100
        assert row['reserved_mib_before'] == 40960
        assert row['schedulable_remaining_mib_before'] == 5108
        assert row['reserved_mib_after'] == 24576
        assert row['schedulable_remaining_mib_after'] == 21492
        assert row['reserved_cores_before'] == row['reserved_cores_after'] == 20
        assert row['schedulable_remaining_cores_before'] == row['schedulable_remaining_cores_after'] == 80
        assert row['physical_free_mib_before'] == 45490
        assert row['third_claim_pending'] and row['third_claim_allocated_after_release']
        assert row['complete']


@pytest.mark.parametrize('case,repeat,tensors,oom,physical', [
    ('case3', 1, {'0': 3.5, '1': 3.75}, 2, {'0': 3890, '1': 4146}),
    ('case3', 2, {'0': 3.75, '1': 3.5}, 2, {'0': 4146, '1': 3890}),
    ('case3', 3, {'0': 3.5, '1': 3.75}, 2, {'0': 3890, '1': 4146}),
    ('case4a', 1, {'0': 6, '1': 3.75}, 1, {'0': 6450, '1': 4146}),
    ('case4a', 2, {'0': 3.75, '1': 6}, 1, {'0': 4146, '1': 6450}),
    ('case4b', 1, {'0': 6, '1': 6}, 0, {'0': 6450, '1': 6450}),
    ('case4b', 2, {'0': 6, '1': 6}, 0, {'0': 6450, '1': 6450}),
    ('case4b', 3, {'0': 6, '1': 6}, 0, {'0': 6450, '1': 6450}),
])
def test_e2b_completed_barriers_match_exact_golden_rows(case, repeat, tensors, oom, physical):
    row = module().analyze_scope_round(E2B / f'{case}-r{repeat}')
    assert row['tensor_gib_by_client'] == tensors
    assert row['tensor_total_gib'] == sum(tensors.values())
    assert row['cuda_oom_count'] == oom
    assert row['nvml_mib_by_client'] == physical
    assert row['nvml_process_total_mib'] == sum(physical.values())
    assert row['complete']
    assert row['libvgpu_present']
    assert row['cache_path_equal'] == row['cache_inode_equal'] == (case != 'case4b')
    assert row['claims_shared'] == (case != 'case4b')
    assert row['quota_exceeded'] == (case == 'case4a')
    assert row['reserved_cores'] == (100 if case == 'case4b' else 50)


def test_stalled_scope_round_never_becomes_a_successful_quota_result():
    row = module().analyze_scope_round(E2B / 'case4a-r3')
    assert not row['complete'] and row['outcome'] == 'stalled'
    assert row['tensor_total_gib'] is None
    assert row['nvml_process_total_mib'] is None
    assert row['cuda_oom_count'] is None
    assert row['quota_exceeded'] is None
    assert row['last_barrier_tensor_gib_by_client'] == {'0': 3.75, '1': 3.75}
    assert row['last_barrier_tensor_total_gib'] == 7.5
    assert row['last_barrier_nvml_process_total_mib'] == 8292
    assert row['last_complete_round_id'] == 14
    assert 'timeout' in row['incomplete_reason']
    assert row['recovery_exit_codes'] == [0, 137]
    assert row['recovery_exit_reasons'] == ['Completed', 'Error']


def test_matching_cache_path_alone_cannot_establish_shared_region():
    hami = module()
    clients = [{'cache_path': '/shared.cache', 'cache_dev': 1, 'cache_inode': 10},
               {'cache_path': '/shared.cache', 'cache_dev': 1, 'cache_inode': 20}]
    observed = hami.cache_identity(clients)
    assert observed['cache_path_equal'] is True
    assert observed['cache_inode_equal'] is False
    assert observed['cache_identity_complete'] is True
    del clients[1]['cache_dev']
    observed = hami.cache_identity(clients)
    assert observed['cache_inode_equal'] is None
    assert observed['cache_identity_complete'] is False


def test_scope_without_physical_or_exit_evidence_remains_incomplete(tmp_path):
    hami = module()
    target = tmp_path / 'case3-r1'
    shutil.copytree(E2B / 'case3-r1', target)
    for path in target.glob('round-*.json'):
        value = json.loads(path.read_text())
        value.pop('processes_raw', None)
        for client in value['clients']:
            client.pop('nvml_used_mib', None)
        path.write_text(json.dumps(value))
    (target / 'terminated-pods.json').unlink()
    row = hami.analyze_scope_round(target)
    assert not row['complete']
    assert row['nvml_process_total_mib'] is None
    assert 'physical' in row['incomplete_reason']
    assert 'termination' in row['incomplete_reason']


def test_cli_recomputes_summaries_and_exits_nonzero_for_stalled_round(tmp_path):
    module()
    result = subprocess.run([sys.executable, str(ANALYSIS), str(E2B), '--output', str(tmp_path)],
                            text=True, capture_output=True)
    assert result.returncode == 2, result.stdout + result.stderr
    rows = json.loads((tmp_path / 'summary.json').read_text())
    assert len(rows) == 9 and sum(r['complete'] for r in rows) == 8
    with (tmp_path / 'summary.csv').open() as stream:
        assert len(list(csv.DictReader(stream))) == 9
    assert 'case4a-r3' in result.stdout and 'stalled' in result.stdout


def test_checked_summaries_are_recomputed_from_public_raw_and_fit_size_limit():
    hami = module()
    for directory in (ROOT / 'data/e2/hami', ROOT / 'data/e2b'):
        assert json.loads((directory / 'summary.json').read_text()) == hami.analyze_run(directory / 'raw')
        sources = (directory / 'SOURCES.md').read_text()
        assert 'results/experiment-2' in sources
        assert ':1' in sources or '` L1' in sources
    total = sum(p.stat().st_size for directory in (ROOT / 'data/e2', ROOT / 'data/e2b')
                for p in directory.rglob('*') if p.is_file())
    assert total < 8 * 1024 * 1024


def lab_module(name, directory='analysis'):
    path = ROOT / 'labs/04-isolation/hami-dra' / directory / f'{name}.py'
    assert path.is_file(), f'HAMi {name} safety interface has not been implemented'
    spec = importlib.util.spec_from_file_location(f'hami_lab_{name}', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize('labels,returncode', [({}, 2), ({'app.kubernetes.io/part-of': 'dra-book'}, 0)])
def test_scope_configmap_ownership_is_checked_before_apply(tmp_path, labels, returncode):
    namespace = tmp_path / 'namespace.json'
    objects = tmp_path / 'objects.json'
    namespace.write_text(json.dumps({'metadata': {'labels': {'app.kubernetes.io/part-of': 'dra-book'}}}))
    objects.write_text(json.dumps({'items': [{'kind': 'ConfigMap', 'metadata': {
        'name': 'hami-scope-code', 'labels': labels}}]}))
    command = [sys.executable, str(ROOT / 'labs/04-isolation/hami-dra/analysis/control.py'),
               'ownership', str(namespace), str(objects)]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == returncode, result.stdout + result.stderr
    if returncode:
        assert 'foreign' in result.stderr and 'hami-scope-code' in result.stderr


def test_scope_manifest_topology_preserves_the_cache_scope_question():
    base = ROOT / 'labs/04-isolation/hami-dra/manifests/scope'
    for case, claim_count, job_count, container_count in [('case3', 1, 1, 2), ('case4a', 1, 2, 2), ('case4b', 2, 2, 2)]:
        objects = [obj for p in sorted((base / case).glob('*.yaml'))
                   if p.name != 'kustomization.yaml' for obj in yaml.safe_load_all(p.read_text())]
        claims = [o for o in objects if o.get('kind') == 'ResourceClaim']
        jobs = [o for o in objects if o.get('kind') == 'Job']
        assert len(claims) == claim_count and len(jobs) == job_count
        assert sum(len(j['spec']['template']['spec']['containers']) for j in jobs) == container_count
        names = {c['metadata']['name'] for c in claims}
        referenced = set()
        for claim in claims:
            exactly = claim['spec']['devices']['requests'][0]['exactly']
            requests = exactly['capacity']['requests']
            assert requests['memory'] == '8Gi' and int(requests['cores']) == 50
            assert exactly['count'] == 1
        for job in jobs:
            assert job['spec']['backoffLimit'] == 0
            spec = job['spec']['template']['spec']
            assert spec['restartPolicy'] == 'Never'
            assert 'nodeName' not in spec
            referenced.update(c['resourceClaimName'] for c in spec['resourceClaims'])
            for container in spec['containers']:
                assert container['image'].endswith('@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755')
                assert {e['name']: e.get('value') for e in container['env']}['PYTORCH_NO_CUDA_MEMORY_CACHING'] == '1'
            assert job['metadata']['labels']['app.kubernetes.io/part-of'] == 'dra-book'
        assert referenced == names


def test_host_control_delete_requires_cuda_exit_and_preserves_unknown_files(tmp_path):
    host = lab_module('host', 'scripts')
    host.HOST_ROOT = tmp_path
    root = tmp_path / 'dra-hami-example'
    owner = host.create_control(root, 'example')
    with pytest.raises((ValueError, RuntimeError), match='exit|CUDA'):
        host.remove_control(root, owner, False)
    unexpected = root / '1/private-state'
    unexpected.write_text('preserve')
    with pytest.raises((ValueError, RuntimeError), match='unexpected|preserve'):
        host.remove_control(root, owner, True)
    assert unexpected.read_text() == 'preserve'
    assert (root / '0/owner.json').exists()
    unexpected.unlink()
    assert host.remove_control(root, owner, True)['removed']
    assert not root.exists()


def test_host_control_refuses_changed_inode_and_symlink_before_writing(tmp_path):
    host = lab_module('host', 'scripts')
    host.HOST_ROOT = tmp_path
    root = tmp_path / 'dra-hami-example'
    owner = host.create_control(root, 'example')
    other = tmp_path / 'other'; other.mkdir()
    (root / '0/owner.json').unlink()
    (root / '0/owner.json').symlink_to(other / 'owner.json')
    with pytest.raises((ValueError, RuntimeError), match='identity|symlink|owner'):
        host.check_control(root, owner)
    assert not (other / 'owner.json').exists()


def test_scope_partial_command_replay_finishes_delivery_before_stop(tmp_path, monkeypatch):
    host = lab_module('host', 'scripts')
    host.HOST_ROOT = tmp_path
    root = tmp_path / 'dra-hami-example'
    owner = host.create_control(root, 'example')
    command = {'run_id': 'example', 'seq': 1, 'operation': 'allocate', 'round_id': 0, 'not_before_ns': 1}
    atomic = host.atomic
    def interrupted(path, body):
        atomic(path, body)
        if path == root / '0/command.json':
            raise OSError('injected interruption after client 0 atomic write')
    monkeypatch.setattr(host, 'atomic', interrupted)
    with pytest.raises(OSError, match='injected interruption'):
        host.send_command(root, owner, command)
    assert json.loads((root / '0/command.json').read_text()) == command
    assert not (root / '1/command.json').exists()
    monkeypatch.setattr(host, 'atomic', atomic)
    host.atomic(root / '0/ack-1.json', {'run_id': 'example', 'client_id': '0',
                                     'command_seq': 1, 'phase': 'allocation'})
    replay = host.replay_command(root, owner, command)
    assert not replay['acknowledged']
    assert json.loads((root / '1/command.json').read_text()) == command
    assert host.replay_command(root, owner, command) == replay
    stop = {'run_id': 'example', 'seq': 2, 'operation': 'stop'}
    with pytest.raises(RuntimeError, match='barrier'):
        host.send_command(root, owner, stop, abort=True, previous=command)
    for client in ('0', '1'):
        host.atomic(root / client / 'ack-1.json', {'run_id': 'example', 'client_id': client,
                                                'command_seq': 1, 'phase': 'allocation'})
    assert host.replay_command(root, owner, command)['acknowledged']
    monkeypatch.setattr(host, 'atomic', interrupted)
    with pytest.raises(OSError, match='injected interruption'):
        host.send_command(root, owner, stop, abort=True, previous=command)
    monkeypatch.setattr(host, 'atomic', atomic)
    host.replay_command(root, owner, stop, previous=command)
    host.replay_command(root, owner, stop, previous=command)
    assert all(json.loads((root / client / 'command.json').read_text()) == stop for client in ('0', '1'))


def test_scope_replay_rejects_foreign_command_and_owner_before_writing(tmp_path):
    host = lab_module('host', 'scripts')
    host.HOST_ROOT = tmp_path
    root = tmp_path / 'dra-hami-example'
    owner = host.create_control(root, 'example')
    command = {'run_id': 'example', 'seq': 1, 'operation': 'allocate', 'round_id': 0, 'not_before_ns': 1}
    foreign = dict(command, operation='stop')
    host.atomic(root / '0/command.json', foreign)
    with pytest.raises(RuntimeError, match='command|foreign'):
        host.replay_command(root, owner, command)
    assert json.loads((root / '0/command.json').read_text()) == foreign
    assert not (root / '1/command.json').exists()
    with pytest.raises(RuntimeError, match='owner|identity'):
        host.replay_command(root, dict(owner, run_id='foreign'), command)
    assert not (root / '1/command.json').exists()


def test_scope_replay_allows_an_interrupted_journal_promotion(tmp_path):
    host = lab_module('host', 'scripts')
    host.HOST_ROOT = tmp_path
    root = tmp_path / 'dra-hami-example'
    owner = host.create_control(root, 'example')
    command = {'run_id': 'example', 'seq': 1, 'operation': 'allocate', 'round_id': 0, 'not_before_ns': 1}
    host.send_command(root, owner, command)
    # Promotion can finish before the new latest command is atomically stored.
    assert host.replay_command(root, owner, command, previous=command)['command'] == command


def test_scope_cleanup_skips_control_sends_after_all_actual_containers_exit(tmp_path):
    import shlex
    owner = {'app.kubernetes.io/part-of': 'dra-book', 'app.kubernetes.io/instance': 'example',
             'app.kubernetes.io/component': 'container-scope-pressure'}
    job = {'kind': 'Job', 'metadata': {'name': 'container-scope-pressure', 'uid': 'scope-job', 'labels': owner},
           'spec': {'backoffLimit': 0, 'template': {'spec': {'containers': [
               {'name': 'worker-0'}, {'name': 'worker-1'}], 'restartPolicy': 'Never'}}}}
    pods = {'items': [{'kind': 'Pod', 'metadata': {'name': 'container-scope-pressure-generated',
        'uid': 'scope-pod', 'labels': owner,
        'ownerReferences': [{'kind': 'Job', 'name': 'container-scope-pressure', 'uid': 'scope-job'}]}, 'spec': {'containers': [
        {'name': 'worker-0'}, {'name': 'worker-1'}]}, 'status': {'containerStatuses': [
        {'name': 'worker-0', 'state': {'terminated': {'exitCode': 0, 'reason': 'Completed',
                                                 'finishedAt': '2026-10-07T00:00:00Z'}}},
        {'name': 'worker-1', 'state': {'terminated': {'exitCode': 137, 'reason': 'Error',
                                                 'finishedAt': '2026-10-07T00:00:01Z'}}}]}}]}
    (tmp_path / 'pods-current.json').write_text(json.dumps(pods))
    (tmp_path / 'trial.json').write_text(json.dumps({'case': 'case3', 'run_id': 'example'}))
    (tmp_path / 'scope-launches.json').write_text(json.dumps({'run_id': 'example', 'jobs': {
        'container-scope-pressure': {'attempted': True, 'uid': 'scope-job', 'spec': job['spec']}}}))
    (tmp_path / 'container-scope-pressure-pod-name.txt').write_text(pods['items'][0]['metadata']['name'])
    (tmp_path / 'container-scope-pressure-created.json').write_text(json.dumps(pods['items'][0]))
    (tmp_path / 'scope-objects-current.json').write_text(json.dumps({'items': [job, *pods['items']]}))
    script = '\n'.join([
        'source ' + shlex.quote(str(ROOT / 'labs/04-isolation/hami-dra/scripts/lib.sh')),
        'ROUND_DIR=' + shlex.quote(str(tmp_path)),
        'scope_refresh() { :; }',
        'scope_inventory() { :; }',
        'scope_send() { touch "$ROUND_DIR/unexpected-send"; return 1; }',
        'scope_read() { printf "{}\\n" > "$ROUND_DIR/control-latest.json"; }',
        'gpu_snapshot() { : > "$ROUND_DIR/processes-exited.txt"; }',
        'scope_stop',
    ])
    result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / 'unexpected-send').exists()
    assert json.loads((tmp_path / 'terminated-pods.json').read_text()) == pods
    assert len(json.loads((tmp_path / 'terminations.json').read_text())) == 2


def test_scope_send_replays_the_saved_partial_delivery_and_reuses_stop_sequence(tmp_path):
    import shlex
    host = lab_module('host', 'scripts')
    node = tmp_path / 'node'; node.mkdir()
    host.HOST_ROOT = node
    root = '/var/tmp/dra-hami-example'
    host.host_path('/var/tmp').mkdir(parents=True)
    owner = host.create_control(root, 'example')
    directory = tmp_path / 'round'; directory.mkdir()
    command = {'run_id': 'example', 'seq': 1, 'operation': 'allocate', 'round_id': 0, 'not_before_ns': 1}
    (directory / 'trial.json').write_text(json.dumps({'run_id': 'example', 'control_root': root}))
    (directory / 'control-owner.json').write_text(json.dumps(owner))
    (directory / 'scope-command.json').write_text(json.dumps(command))
    host.atomic(host.host_path(root) / '0/command.json', command)
    host.atomic(host.host_path(root) / '0/ack-1.json', {'run_id': 'example', 'client_id': '0',
                                                   'command_seq': 1, 'phase': 'allocation'})
    bridge = tmp_path / 'host-bridge.py'
    bridge.write_text(f'''import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('offline_hami_host',{str(ROOT / 'labs/04-isolation/hami-dra/scripts/host.py')!r})
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h);h.HOST_ROOT=Path({str(node)!r})
args=json.loads(sys.argv[1]);operation=args.pop('operation')
if operation=='ack':
    command=json.load(open(Path({str(directory)!r})/'scope-command.json'))
    rows=[]
    for client in ('0','1'):
        row={{'run_id':'example','client_id':client,'command_seq':command['seq'],'phase':'allocation'}}
        h.atomic(h.host_path({root!r})/client/f"ack-{{command['seq']}}.json",row);rows.append(row)
    value=rows
else:
    value={{'read':h.read_control,'send':h.send_command,'replay':h.replay_command}}[operation](**args)
print(json.dumps(value))
''')
    call = shlex.quote(sys.executable) + ' ' + shlex.quote(str(bridge))
    script = '\n'.join([
        'source ' + shlex.quote(str(ROOT / 'labs/04-isolation/hami-dra/scripts/lib.sh')),
        'ROUND_DIR=' + shlex.quote(str(directory)),
        'host_call() { ' + call + ' "$1"; }',
        'scope_receive() { ' + call + ' \'{"operation":"ack"}\' > "$3"; }',
        'scope_send stop -1 true',
        'scope_send stop -1 true',
    ])
    result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    stop = {'run_id': 'example', 'seq': 2, 'operation': 'stop'}
    assert json.loads((directory / 'scope-command.json').read_text()) == stop
    assert json.loads((directory / 'scope-previous-command.json').read_text()) == command
    assert all(json.loads((host.host_path(root) / client / 'command.json').read_text()) == stop for client in ('0', '1'))


def scope_startup_fixture(tmp_path):
    owner = {'app.kubernetes.io/part-of': 'dra-book', 'app.kubernetes.io/instance': 'example',
             'app.kubernetes.io/component': 'scope-primary'}
    job = {'kind': 'Job', 'metadata': {'name': 'scope-primary', 'uid': 'primary-job', 'labels': owner},
           'spec': {'backoffLimit': 0, 'template': {'spec': {'containers': [{'name': 'worker-0'}],
                                                         'restartPolicy': 'Never'}}}}
    pod = {'kind': 'Pod', 'metadata': {'name': 'scope-primary-generated', 'uid': 'primary-pod',
           'labels': owner, 'ownerReferences': [{'kind': 'Job', 'name': 'scope-primary', 'uid': 'primary-job'}]},
           'spec': {'containers': [{'name': 'worker-0'}]}, 'status': {'containerStatuses': [
               {'name': 'worker-0', 'state': {'terminated': {'exitCode': 0, 'reason': 'Completed'}}}]}}
    (tmp_path / 'trial.json').write_text(json.dumps({'case': 'case4a', 'run_id': 'example'}))
    (tmp_path / 'scope-launches.json').write_text(json.dumps({'run_id': 'example', 'jobs': {
        'scope-primary': {'attempted': True, 'uid': job['metadata']['uid'], 'spec': job['spec']}}}))
    (tmp_path / 'scope-primary-pod-name.txt').write_text(pod['metadata']['name'] + '\n')
    (tmp_path / 'scope-primary-created.json').write_text(json.dumps(pod))
    (tmp_path / 'pods-current.json').write_text(json.dumps({'items': [pod]}))
    (tmp_path / 'scope-objects-current.json').write_text(json.dumps({'items': [job, pod]}))
    return job, pod


def test_scope_startup_cleanup_allows_only_a_proven_never_launched_secondary(tmp_path):
    control = lab_module('control')
    job, pod = scope_startup_fixture(tmp_path)
    expected = control.scope_cleanup_clients(tmp_path)
    assert expected == {'worker-0'}
    assert control.scope_terminations({'items': [pod]}, expected)[0]['container'] == 'worker-0'


def test_scope_startup_cleanup_uses_the_primary_actual_exit_without_sending_control(tmp_path):
    import shlex
    job, pod = scope_startup_fixture(tmp_path)
    script = '\n'.join([
        'source ' + shlex.quote(str(ROOT / 'labs/04-isolation/hami-dra/scripts/lib.sh')),
        'ROUND_DIR=' + shlex.quote(str(tmp_path)),
        'scope_refresh() { :; }',
        'scope_inventory() { :; }',
        'scope_send() { touch "$ROUND_DIR/unexpected-send"; return 1; }',
        'scope_read() { printf "{}\\n" > "$ROUND_DIR/control-latest.json"; }',
        'gpu_snapshot() { : > "$ROUND_DIR/processes-exited.txt"; }',
        'scope_stop',
    ])
    result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / 'unexpected-send').exists()
    assert json.loads((tmp_path / 'terminated-pods.json').read_text()) == {'items': [pod]}
    assert len(json.loads((tmp_path / 'terminations.json').read_text())) == 1


def test_scope_runner_journals_claim_and_job_identity_before_secondary_startup(tmp_path):
    import shlex
    job, pod = scope_startup_fixture(tmp_path)
    (tmp_path / 'scope-launches.json').unlink()
    (tmp_path / 'primary-api.json').write_text(json.dumps(job))
    claim = {'kind': 'ResourceClaim', 'metadata': {'name': 'pod-shared-gpu', 'uid': 'claim-uid'}}
    (tmp_path / 'claims-api.json').write_text(json.dumps({'items': [claim]}))
    bridge = tmp_path / 'mock-startup.py'
    bridge.write_text('''import json,sys
from pathlib import Path
p=Path(sys.argv[1]);args=sys.argv[2:]
if 'apply' in args:
    if Path(args[-1]).name=='scope-primary.json':
        journal=json.load(open(p/'scope-launches.json'))
        assert journal['jobs']['scope-primary']=={'attempted':True}
        assert json.load(open(p/'scope-claims-created.json'))['items'][0]['metadata']['uid']=='claim-uid'
elif 'resourceclaims' in args: print((p/'claims-api.json').read_text())
elif 'job' in args and 'scope-primary' in args: print((p/'primary-api.json').read_text())
else: raise SystemExit('unexpected kubectl call')
''')
    call = shlex.quote(sys.executable) + ' ' + shlex.quote(str(bridge)) + ' ' + shlex.quote(str(tmp_path))
    script = '\n'.join([
        'source ' + shlex.quote(str(ROOT / 'labs/04-isolation/hami-dra/scripts/lib.sh')),
        'ROUND_DIR=' + shlex.quote(str(tmp_path)), 'RUN_ID=example', 'REPEAT=1',
        'wait_gpu_clean() { :; }', 'bash() { :; }', 'render() { :; }',
        'kc() { ' + call + ' "$@"; }',
        'host_call() { printf \'{"run_id":"example"}\\n\'; }',
        'wait_job_pod() { printf "scope-primary-generated\\n"; }',
        'scope_receive() { return 42; }',
        'scope_round case4a',
    ])
    result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
    assert result.returncode == 42, result.stdout + result.stderr
    journal = json.loads((tmp_path / 'scope-launches.json').read_text())
    assert journal['jobs'] == {'scope-primary': {'attempted': True, 'uid': 'primary-job', 'spec': job['spec']}}
    assert json.loads((tmp_path / 'scope-claims-created.json').read_text()) == {'items': [claim]}


def test_scope_startup_existing_secondary_job_without_a_pod_is_still_expected(tmp_path):
    control = lab_module('control')
    job, pod = scope_startup_fixture(tmp_path)
    secondary = json.loads(json.dumps(job))
    secondary['metadata'].update(name='scope-secondary', uid='secondary-job')
    secondary['metadata']['labels']['app.kubernetes.io/component'] = 'scope-secondary'
    secondary['spec']['template']['spec']['containers'] = [{'name': 'worker-1'}]
    journal = json.loads((tmp_path / 'scope-launches.json').read_text())
    journal['jobs']['scope-secondary'] = {'attempted': True, 'uid': 'secondary-job', 'spec': secondary['spec']}
    (tmp_path / 'scope-launches.json').write_text(json.dumps(journal))
    (tmp_path / 'scope-objects-current.json').write_text(json.dumps({'items': [job, pod, secondary]}))
    expected = control.scope_cleanup_clients(tmp_path)
    assert expected == {'worker-0', 'worker-1'}
    assert control.scope_terminations({'items': [pod]}, expected) is None


def test_scope_cleanup_phase_retains_claim_uids_and_allows_only_its_own_deletions(tmp_path):
    control = lab_module('control')
    job, pod = scope_startup_fixture(tmp_path)
    claim = {'kind': 'ResourceClaim', 'metadata': {'name': 'pod-shared-gpu', 'uid': 'claim-uid',
        'labels': {'app.kubernetes.io/part-of': 'dra-book', 'app.kubernetes.io/instance': 'example',
                   'app.kubernetes.io/component': 'pod-shared-gpu'}}, 'spec': {'devices': {}}}
    (tmp_path / 'scope-claims-created.json').write_text(json.dumps({'items': [claim]}))
    (tmp_path / 'terminated-pods.json').write_text(json.dumps({'items': [pod]}))
    (tmp_path / 'terminations.json').write_text(json.dumps(control.scope_terminations({'items': [pod]}, {'worker-0'})))
    first = control.scope_cleanup_phase(tmp_path, {'items': [job, pod, claim]})
    assert first['run_id'] == 'example'
    # Jobs and Pods may be absent only after the durable, verified cleanup phase.
    resumed = control.scope_cleanup_phase(tmp_path, {'items': [claim]})
    assert resumed == first
    assert [item['metadata']['uid'] for item in resumed['objects']['items'] if item['kind'] == 'ResourceClaim'] == ['claim-uid']
    replaced = json.loads(json.dumps(claim)); replaced['metadata']['uid'] = 'replacement-claim'
    with pytest.raises(ValueError, match='UID|identity|changed'):
        control.scope_cleanup_phase(tmp_path, {'items': [replaced]})
    foreign = json.loads(json.dumps(claim)); foreign['metadata']['labels']['app.kubernetes.io/part-of'] = 'foreign'
    with pytest.raises(ValueError, match='ownership|identity|changed'):
        control.scope_cleanup_phase(tmp_path, {'items': [foreign]})


def test_scope_cleanup_can_resume_after_jobs_and_claims_were_deleted(tmp_path):
    import shlex
    control = lab_module('control'); job, pod = scope_startup_fixture(tmp_path)
    claim = {'kind': 'ResourceClaim', 'metadata': {'name': 'pod-shared-gpu', 'uid': 'claim-uid',
        'labels': {'app.kubernetes.io/part-of': 'dra-book', 'app.kubernetes.io/instance': 'example',
                   'app.kubernetes.io/component': 'pod-shared-gpu'}}, 'spec': {'devices': {}}}
    (tmp_path / 'scope-claims-created.json').write_text(json.dumps({'items': [claim]}))
    (tmp_path / 'terminated-pods.json').write_text(json.dumps({'items': [pod]}))
    (tmp_path / 'terminations.json').write_text(json.dumps(control.scope_terminations({'items': [pod]}, {'worker-0'})))
    (tmp_path / 'cluster-current.json').write_text(json.dumps({'items': [job, pod, claim]}))
    trial = json.loads((tmp_path / 'trial.json').read_text()); trial['control_root'] = '/var/tmp/dra-hami-example'
    (tmp_path / 'trial.json').write_text(json.dumps(trial)); (tmp_path / 'control-owner.json').write_text('{}')
    bridge = tmp_path / 'mock-kubectl.py'
    bridge.write_text('''import json,sys
from pathlib import Path
p=Path(sys.argv[1]);args=sys.argv[2:];state=json.load(open(p/'cluster-current.json'))
if 'get' in args: print(json.dumps(state))
elif 'delete' in args:
    assert (p/'scope-cleanup-started.json').exists(), 'deletion preceded verified cleanup phase'
    kinds={'Job','Pod'} if 'jobs' in args else {'ResourceClaim'}
    state['items']=[item for item in state['items'] if item['kind'] not in kinds]
    (p/'cluster-current.json').write_text(json.dumps(state))
else: raise SystemExit('unexpected kubectl operation')
''')
    call = shlex.quote(sys.executable) + ' ' + shlex.quote(str(bridge)) + ' ' + shlex.quote(str(tmp_path))
    artifact_check = '''import json,sys
from pathlib import Path
p=Path(sys.argv[1]);items=json.load(open(sys.argv[2]))['items']
assert {item['metadata']['uid'] for item in items if item['kind']=='ResourceClaim'}=={'claim-uid'}
path=p/'artifact-calls';count=int(path.read_text())+1 if path.exists() else 1;path.write_text(str(count))
raise SystemExit(23 if count==1 else 0)
'''
    shared = '\n'.join([
        'source ' + shlex.quote(str(ROOT / 'labs/04-isolation/hami-dra/scripts/lib.sh')),
        'ROUND_DIR=' + shlex.quote(str(tmp_path)), 'RUN_ID=example',
        'kc() { ' + call + ' "$@"; }',
        'scope_refresh() { touch "$ROUND_DIR/unexpected-refresh"; return 1; }',
        'scope_send() { touch "$ROUND_DIR/unexpected-send"; return 1; }',
        'gpu_snapshot() { : > "$ROUND_DIR/processes-exited.txt"; }',
        'wait_pods_absent() { :; }', 'wait_gpu_clean() { :; }',
        'host_call() { printf \'{"removed":true}\\n\'; }', 'bash() { :; }',
        'wait_claim_artifacts_absent() { python3 -c ' + shlex.quote(artifact_check) + ' "$ROUND_DIR" "$1"; }',
    ])
    failed = subprocess.run(['bash', '-c', shared + '\nscope_cleanup_round'], text=True, capture_output=True)
    assert failed.returncode == 23, failed.stdout + failed.stderr
    assert (tmp_path / 'scope-cleanup-started.json').exists()
    assert not (tmp_path / 'cleaned.json').exists()
    assert json.loads((tmp_path / 'cluster-current.json').read_text()) == {'items': []}
    resumed = subprocess.run(['bash', '-c', shared + '\nscope_stop\nscope_cleanup_round'], text=True, capture_output=True)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert not (tmp_path / 'unexpected-refresh').exists()
    assert not (tmp_path / 'unexpected-send').exists()
    assert json.loads((tmp_path / 'cleanup.json').read_text())['verified']
    assert (tmp_path / 'cleaned.json').exists()


@pytest.mark.parametrize('change', ['missing', 'replacement'])
def test_scope_cleanup_cannot_forget_a_claim_uid_before_the_deletion_phase(tmp_path, change):
    control = lab_module('control'); job, pod = scope_startup_fixture(tmp_path)
    claim = {'kind': 'ResourceClaim', 'metadata': {'name': 'pod-shared-gpu', 'uid': 'claim-uid',
        'labels': {'app.kubernetes.io/part-of': 'dra-book', 'app.kubernetes.io/instance': 'example',
                   'app.kubernetes.io/component': 'pod-shared-gpu'}}, 'spec': {'devices': {}}}
    (tmp_path / 'scope-claims-created.json').write_text(json.dumps({'items': [claim]}))
    (tmp_path / 'terminated-pods.json').write_text(json.dumps({'items': [pod]}))
    (tmp_path / 'terminations.json').write_text(json.dumps(control.scope_terminations({'items': [pod]}, {'worker-0'})))
    current = [job, pod]
    if change == 'replacement':
        claim['metadata']['uid'] = 'replacement-claim'; current.append(claim)
    with pytest.raises(ValueError, match='Claim|UID|claim'):
        control.scope_cleanup_phase(tmp_path, {'items': current})
    assert not (tmp_path / 'scope-cleanup-started.json').exists()


def test_scope_host_control_removal_can_resume_after_a_verified_partial_removal(tmp_path, monkeypatch):
    host = lab_module('host', 'scripts'); host.HOST_ROOT = tmp_path
    root = tmp_path / 'dra-hami-example'; owner = host.create_control(root, 'example')
    host.atomic(root / '0/ready.json', {'run_id': 'example'})
    unlink = Path.unlink
    def interrupted(path, *args, **kwargs):
        unlink(path, *args, **kwargs)
        if path == root / '0/ready.json':
            raise OSError('injected cleanup interruption')
    monkeypatch.setattr(Path, 'unlink', interrupted)
    with pytest.raises(OSError, match='injected cleanup interruption'):
        host.remove_control(root, owner, True)
    monkeypatch.setattr(Path, 'unlink', unlink)
    assert host.remove_control(root, owner, True, resume=True)['removed']


@pytest.mark.parametrize('interruption', ['client_rmdir', 'root_owner_unlink'])
def test_scope_host_removal_resumes_missing_owned_children_but_rejects_replacements(tmp_path, monkeypatch, interruption):
    host = lab_module('host', 'scripts'); host.HOST_ROOT = tmp_path
    root = tmp_path / 'dra-hami-example'; owner = host.create_control(root, 'example')
    unlink, rmdir = Path.unlink, Path.rmdir
    def interrupted_unlink(path, *args, **kwargs):
        unlink(path, *args, **kwargs)
        if interruption == 'root_owner_unlink' and path == root / 'owner.json':
            raise OSError('injected root owner interruption')
    def interrupted_rmdir(path, *args, **kwargs):
        rmdir(path, *args, **kwargs)
        if interruption == 'client_rmdir' and path == root / '0':
            raise OSError('injected child directory interruption')
    monkeypatch.setattr(Path, 'unlink', interrupted_unlink); monkeypatch.setattr(Path, 'rmdir', interrupted_rmdir)
    with pytest.raises(OSError, match='injected'):
        host.remove_control(root, owner, True)
    monkeypatch.setattr(Path, 'unlink', unlink); monkeypatch.setattr(Path, 'rmdir', rmdir)
    if interruption == 'client_rmdir':
        # A replacement surviving child must not inherit authorization from the old inode.
        surviving = root / '1'; surviving.rename(tmp_path / 'saved-survivor')
        surviving.mkdir()
        with pytest.raises(RuntimeError, match='unexpected|identity'):
            host.remove_control(root, owner, True, resume=True)
        surviving.rmdir(); (tmp_path / 'saved-survivor').rename(surviving)
    assert host.remove_control(root, owner, True, resume=True)['removed']
    assert not root.exists()
    assert host.remove_control(root, owner, True, resume=True)['removed']


@pytest.mark.parametrize('change', ['missing_job', 'replaced_job', 'missing_pod', 'attempted_secondary', 'orphan_secondary'])
def test_scope_startup_cleanup_preserves_attempted_or_changed_objects(tmp_path, change):
    control = lab_module('control')
    job, pod = scope_startup_fixture(tmp_path)
    inventory = {'items': [job, pod]}
    if change == 'missing_job':
        inventory['items'] = [pod]
    elif change == 'replaced_job':
        job['metadata']['uid'] = 'replacement-job'
    elif change == 'missing_pod':
        (tmp_path / 'pods-current.json').write_text('{"items":[]}')
        inventory['items'] = [job]
    elif change == 'attempted_secondary':
        journal = json.loads((tmp_path / 'scope-launches.json').read_text())
        journal['jobs']['scope-secondary'] = {'attempted': True}
        (tmp_path / 'scope-launches.json').write_text(json.dumps(journal))
    else:
        orphan = json.loads(json.dumps(pod))
        orphan['metadata'].update(name='scope-secondary-generated', uid='secondary-pod',
                                 ownerReferences=[{'kind': 'Job', 'name': 'scope-secondary', 'uid': 'secondary-job'}])
        inventory['items'].append(orphan)
    (tmp_path / 'scope-objects-current.json').write_text(json.dumps(inventory))
    with pytest.raises(ValueError, match='absent|disappeared|UID|launch|Pod'):
        control.scope_cleanup_clients(tmp_path)


def test_hami_target_rejects_duplicate_publishers_and_changed_core_capacity():
    control = lab_module('control')
    slices = json.loads((E2 / 'hami-boundary-8g-r1/resourceslices.json').read_text())
    assert control.target(slices)
    duplicated = {'items': slices['items'] * 2}
    with pytest.raises((ValueError, RuntimeError), match='one|duplicate|publisher'):
        control.target(duplicated)
    changed = json.loads(json.dumps(slices))
    changed['items'][0]['spec']['devices'][0]['capacity']['cores']['value'] = '99'
    with pytest.raises((ValueError, RuntimeError), match='cores|capacity'):
        control.target(changed)


def test_hami_allocation_rejects_memory_or_core_accounting_drift():
    control = lab_module('control')
    claims = json.loads((E2 / 'hami-boundary-8g-r1/claims-allocated.json').read_text())
    pressure = next(c for c in claims if c['metadata']['name'] == 'pressure-gpu')
    assert control.check_allocation(pressure, 8, 50)
    changed = json.loads(json.dumps(pressure))
    changed['status']['allocation']['devices']['results'][0]['consumedCapacity']['cores'] = '49'
    with pytest.raises((ValueError, RuntimeError), match='cores|capacity|allocation'):
        control.check_allocation(changed, 8, 50)


def test_e2_csv_splits_keep_accounting_and_runtime_fields_in_their_own_tables():
    root = ROOT / 'data/e2/hami'
    assert (root / 'runtime.csv').is_file(), 'HAMi runtime table has not been exported'
    assert (root / 'accounting.csv').is_file(), 'HAMi accounting table has not been exported'
    with (root / 'runtime.csv').open() as stream:
        runtime = list(csv.DictReader(stream))
    with (root / 'accounting.csv').open() as stream:
        accounting = list(csv.DictReader(stream))
    assert len(runtime) == 6 and {r['case'] for r in runtime} == {'boundary', 'scope'}
    assert len(accounting) == 3 and {r['case'] for r in accounting} == {'accounting'}
    assert 'b_nvml_used_mib' in runtime[0] and 'reserved_cores_before' in accounting[0]


def test_hami_physical_figure_uses_per_b_usage_and_the_exact_8192_mib_quota():
    from tools.figures import ch04
    assert hasattr(ch04, 'hami_runtime_physical'), 'HAMi physical figure has not been implemented'
    figure = ch04.hami_runtime_physical(ROOT / 'data')
    axis = figure.axes[0]
    assert [p.get_height() for p in axis.patches] == [7986, 7986, 7986, 8292, 8292, 8036]
    assert any(list(line.get_ydata()) == [8192, 8192] for line in axis.lines)
    assert 'B' in axis.get_ylabel() and 'MiB' in axis.get_ylabel()


def test_runtime_joins_generated_job_pod_names_to_cuda_ready_identity(tmp_path):
    hami = module()
    target = tmp_path / 'hami-scope-8g-r1'
    shutil.copytree(E2 / 'hami-scope-8g-r1', target)
    mappings = json.loads((target / 'client-identity.json').read_text())
    for mapping in mappings:
        if mapping['pod'] == 'pressure':
            mapping['pod'] = 'quota-pressure-generated'
    (target / 'client-identity.json').write_text(json.dumps(mappings))
    lines = []
    for line in (target / 'task-b.log').read_text().splitlines():
        if line.startswith('{'):
            value = json.loads(line)
            value['pod_name'] = 'quota-pressure-generated'
            line = json.dumps(value)
        lines.append(line)
    (target / 'task-b.log').write_text('\n'.join(lines) + '\n')
    hooks = json.loads((target / 'runtime-hooks.json').read_text())
    hooks['quota-pressure'] = hooks.pop('pressure')
    hooks['steady-service'] = hooks.pop('survivor')
    (target / 'runtime-hooks.json').write_text(json.dumps(hooks))
    row = hami.analyze_runtime_round(target)
    assert row['complete'], row['incomplete_reason']
    assert row['b_nvml_used_mib'] == 8292


def test_scope_rejects_a_missing_middle_allocation_barrier(tmp_path):
    hami = module()
    target = tmp_path / 'case3-r1'
    shutil.copytree(E2B / 'case3-r1', target)
    (target / 'round-07.json').unlink()
    row = hami.analyze_scope_round(target)
    assert not row['complete']
    assert 'barrier' in row['incomplete_reason']


def test_live_runtime_identity_handles_generated_job_pods_and_distinct_claim_caches(tmp_path):
    control = lab_module('control')
    hami = module()
    source = E2 / 'hami-scope-8g-r1'
    fields = json.loads((source / 'client-processes.json').read_text())
    mapping = json.loads((source / 'client-identity.json').read_text())
    names = {'survivor': 'steady-service-generated', 'pressure': 'quota-pressure-generated'}
    for prefix, role in [('a', 'survivor'), ('b', 'pressure')]:
        pod = json.loads((source / f'{role}-final.json').read_text())
        pod['metadata']['name'] = names[role]
        client = next(c for c in mapping if c['pod'] == role)
        pod['status']['containerStatuses'][0]['containerID'] = 'cri-o://' + client['container_id']
        (tmp_path / f'{prefix}-current.json').write_text(json.dumps(pod))
        lines = []
        for line in (source / f'task-{prefix}.log').read_text().splitlines():
            if line.startswith('{'):
                value = json.loads(line)
                value['pod_name'] = names[role]
                line = json.dumps(value)
            lines.append(line)
        (tmp_path / f'task-{prefix}.log').write_text('\n'.join(lines) + '\n')
    hooks = json.loads((source / 'runtime-hooks.json').read_text())
    renamed = {}
    for role, key in [('survivor', 'steady-service'), ('pressure', 'quota-pressure')]:
        item = hooks[role]
        item['cache_path'] = item['env']['CUDA_DEVICE_MEMORY_SHARED_CACHE']
        client = next(c for c in mapping if c['pod'] == role)
        item.update(hami.mapped_cache(fields[str(client['host_pid'])]['maps'], item['cache_path']))
        renamed[key] = item
    (tmp_path / 'runtime-hooks.json').write_text(json.dumps(renamed))
    (tmp_path / 'proc-fields.json').write_text(json.dumps(fields))
    claims = json.loads((source / 'claims-allocated.json').read_text())
    (tmp_path / 'claims-allocated.json').write_text(json.dumps({'items': claims}))
    (tmp_path / 'trial.json').write_text(json.dumps({'case': 'scope'}))
    (tmp_path / 'processes-mapping.txt').write_bytes((source / 'processes-during.txt').read_bytes())
    control.runtime_identity(tmp_path)
    observed = json.loads((tmp_path / 'client-identity.json').read_text())
    assert len(observed) == 3
    pressure = [c for c in observed if c['pod_name'] == names['pressure']]
    assert len(pressure) == 2
    assert len({(c['cache_dev'], c['cache_inode']) for c in pressure}) == 1
    assert all(c['library_map_lines'] and c['cache_map_lines'] for c in observed)


def host_hook_fixture(tmp_path, monkeypatch):
    host = lab_module('host', 'scripts')
    host.HOST_ROOT = tmp_path
    original_footprint = host.footprint
    def root_owned_footprint():
        # The helper runs as root in the pinned debug image. These local files
        # have the test user's UID; retain all actual inode/bytes evidence while
        # representing the root UID required by the host cleanup contract.
        result = original_footprint()
        for rows in result.values():
            for row in rows:
                row['uid'] = 0
        return result
    monkeypatch.setattr(host, 'footprint', root_owned_footprint)
    before = host.footprint()
    for root in host.HOOK_ROOTS:
        host.host_path(root).mkdir(parents=True)
    library = host.host_path('/usr/local/vgpu/libvgpu.so')
    library.write_bytes(b'known-library-fixture')
    checkpoint = host.host_path('/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io/checkpoint.json')
    checkpoint.write_text('{"v2":{"preparedClaims":{}}}')
    installed = host.footprint()
    return host, before, installed, library, checkpoint


def test_host_cleanup_removes_only_the_known_empty_claims_parent(tmp_path, monkeypatch):
    host, before, installed, library, checkpoint = host_hook_fixture(tmp_path, monkeypatch)
    host.host_path('/usr/local/vgpu/claims').mkdir()
    checked = host.footprint()
    result = host.remove_hooks(before, installed, checked)
    assert all(not result[root] for root in host.HOOK_ROOTS)
    assert not library.exists() and not checkpoint.exists()


def test_host_cleanup_preserves_a_nonempty_driver_checkpoint(tmp_path, monkeypatch):
    host, before, installed, library, checkpoint = host_hook_fixture(tmp_path, monkeypatch)
    checkpoint.write_text('{"v2":{"preparedClaims":{"still-live":{}}}}')
    checked = host.footprint()
    with pytest.raises((ValueError, RuntimeError), match='prepared Claims|checkpoint'):
        host.remove_hooks(before, installed, checked)
    assert library.exists()
    assert 'still-live' in checkpoint.read_text()


def test_host_cleanup_preserves_unknown_claim_directories(tmp_path, monkeypatch):
    host, before, installed, library, checkpoint = host_hook_fixture(tmp_path, monkeypatch)
    foreign = host.host_path('/usr/local/vgpu/claims/foreign')
    foreign.mkdir(parents=True)
    (foreign / 'foreign.cache').write_text('preserve')
    checked = host.footprint()
    with pytest.raises((ValueError, RuntimeError), match='unknown|ownership'):
        host.remove_hooks(before, installed, checked)
    assert library.exists() and checkpoint.exists()
    assert (foreign / 'foreign.cache').read_text() == 'preserve'


def test_host_cleanup_checks_hami_kind_inside_cdi_yaml_before_deleting_any_root(tmp_path, monkeypatch):
    host, before, installed, library, checkpoint = host_hook_fixture(tmp_path, monkeypatch)
    cdi = host.host_path('/var/run/cdi/unrelated-name.yaml')
    cdi.parent.mkdir(parents=True)
    cdi.write_text('kind: hami-core-gpu.project-hami.io/gpu\n')
    checked = host.footprint()
    with pytest.raises((ValueError, RuntimeError), match='CDI'):
        host.remove_hooks(before, installed, checked)
    assert library.exists() and checkpoint.exists() and cdi.exists()


def test_hami_check_summary_cli_loads_shared_analysis_without_sys_path_changes():
    result = subprocess.run([sys.executable, str(ROOT / 'labs/04-isolation/hami-dra/analysis/control.py'),
                             'check-summary', str(E2 / 'hami-boundary-8g-r1')], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_hami_control_load_is_independent_of_another_chapter_analyze_module(monkeypatch):
    path = ROOT / 'labs/03-oom/analysis/analyze.py'
    spec = importlib.util.spec_from_file_location('analyze', path)
    other = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(other)
    monkeypatch.setitem(sys.modules, 'analyze', other)
    control = lab_module('control')
    slices = json.loads((E2 / 'hami-boundary-8g-r1/resourceslices.json').read_text())
    assert control.target(slices)['published_cores'] == 100
    assert sys.modules['analyze'] is other


def test_provider_host_projection_preserves_hashes_and_restored_boundaries():
    location = ROOT / 'data/e2b/provider-evidence'
    installed = json.loads((location / 'host-installed.json').read_text())
    restored = json.loads((location / 'host-restored-clean.json').read_text())
    owned_roots = ('/usr/local/vgpu', '/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io',
                   '/tmp/vgpulock')
    assert all(installed[root] and restored[root] == [] for root in owned_roots)
    library = next(row for row in installed['/usr/local/vgpu'] if row['path'].endswith('/libvgpu.so'))
    assert library['sha256'] == '56fc0944896337cc0ee439cbc7364105ccac11e8dc38add6a3dc467cea34b18f'
    assert library['size'] == 685576
    assert installed['/var/run/cdi'] == restored['/var/run/cdi']
    cdi = next(row for row in restored['/var/run/cdi'] if row['path'].endswith('.yaml'))
    assert cdi['path'] == '/var/run/cdi/management.nvidia.com-gpu.yaml'
    assert cdi['sha256'] == '4f3f2f9f4a5ff8e327dc51a01b86c36ef19cb76e680a2410699c631be03afd40'
    allowed = {'path', 'mode', 'dev', 'ino', 'uid', 'size', 'sha256'}
    assert all(set(row) <= allowed for snapshot in (installed, restored)
               for rows in snapshot.values() for row in rows)
    sources = (ROOT / 'data/e2b/SOURCES.md').read_text()
    hashes = json.loads((ROOT / 'data/e2b/source-sha256.json').read_text())
    prefix = 'results/experiment-2b-hami-scope/e2b-20261007t060500z/'
    for name, lines in [('host-installed.json', 105), ('host-restored-clean.json', 24),
                        ('provider-input.yaml', 349), ('deviceclass-input.yaml', 19),
                        ('resourceslices-provider.yaml', 120)]:
        assert f'`{prefix}{name}` L1–L{lines}' in sources
        assert len(hashes[prefix + name]) == 64


HAMI_KUSTOMIZATIONS = sorted((ROOT / 'labs/04-isolation/hami-dra').rglob('kustomization.yaml'))


@pytest.mark.parametrize('path', HAMI_KUSTOMIZATIONS,
                         ids=lambda path: str(path.parent.relative_to(ROOT / 'labs/04-isolation/hami-dra')))
def test_every_hami_kustomization_renders_with_pinned_binary(path):
    binary = ROOT / '.work/bin/kustomize'
    if not binary.is_file():
        pytest.skip('the pinned .work/bin/kustomize binary is absent')
    result = subprocess.run([str(binary), 'build', '--load-restrictor', 'LoadRestrictionsNone', str(path.parent)],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    objects = [value for value in yaml.safe_load_all(result.stdout) if value]
    assert objects and all(value.get('apiVersion') and value.get('kind') for value in objects)


def test_kc_get_list_normalizes_empty_ignore_not_found_output(tmp_path):
    """kubectl prints nothing for `get -f --ignore-not-found -o json` when no object exists."""
    import subprocess
    lib = ROOT / "labs/04-isolation/hami-dra/scripts/lib.sh"
    out = tmp_path / "existing.json"
    script = f'kc() {{ :; }}; eval "$(sed -n "/^kc_get_list()/,/^}}/p" {lib})"; kc_get_list {out} -f expected.json'
    subprocess.run(["bash", "-c", script], check=True)
    assert json.loads(out.read_text()) == {"apiVersion": "v1", "kind": "List", "items": []}


@pytest.mark.parametrize('name', ['pu.lock', 'cp.lock', 'checkpoint.json'])
def test_host_cleanup_accepts_only_named_plugin_runtime_files_created_after_install(tmp_path, monkeypatch, name):
    host, before, installed, library, checkpoint = host_hook_fixture(tmp_path, monkeypatch)
    checkpoint.unlink()
    installed = host.footprint()
    runtime = host.host_path('/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io/' + name)
    runtime.write_text('{"v2":{"preparedClaims":{}}}' if name == 'checkpoint.json' else '')
    result = host.remove_hooks(before, installed, host.footprint())
    assert all(not result[root] for root in host.HOOK_ROOTS)
    assert not runtime.exists() and not library.exists()


@pytest.mark.parametrize('name', ['other.lock', 'checkpoint-copy.json', 'nested/pu.lock', '../pu.lock'])
def test_host_cleanup_preserves_every_unnamed_or_misplaced_runtime_file(tmp_path, monkeypatch, name):
    host, before, installed, library, checkpoint = host_hook_fixture(tmp_path, monkeypatch)
    runtime = host.host_path('/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io') / name
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text('preserve')
    runtime = runtime.resolve()
    if name == '../pu.lock':
        # A file outside the managed root is never visited or deleted.
        host.remove_hooks(before, installed, host.footprint())
    else:
        with pytest.raises(RuntimeError, match='unknown'):
            host.remove_hooks(before, installed, host.footprint())
        assert library.exists() and checkpoint.exists()
    assert runtime.read_text() == 'preserve'


@pytest.mark.parametrize('change', ['symlink', 'directory', 'nonroot', 'other-device', 'prepared-claim'])
def test_new_runtime_files_must_be_regular_owned_and_checkpoint_empty(tmp_path, monkeypatch, change):
    host, before, installed, library, checkpoint = host_hook_fixture(tmp_path, monkeypatch)
    name = 'checkpoint.json' if change == 'prepared-claim' else 'pu.lock'
    runtime = host.host_path('/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io/' + name)
    if change == 'symlink':
        runtime.symlink_to(library)
    elif change == 'directory':
        runtime.mkdir()
    else:
        runtime.write_text('{"v2":{"preparedClaims":{"live":{}}}}' if change == 'prepared-claim' else '')
    original = host.footprint
    def changed():
        value = original()
        for row in value[host.HOOK_ROOTS[1]]:
            if row['path'].endswith('/pu.lock'):
                if change == 'nonroot': row['uid'] = 1000
                elif change == 'other-device': row['dev'] += 1
        return value
    monkeypatch.setattr(host, 'footprint', changed)
    with pytest.raises((RuntimeError, ValueError), match='unknown|regular|ownership|checkpoint|prepared Claims'):
        host.remove_hooks(before, installed, host.footprint())
    assert library.exists() and checkpoint.exists() and os.path.lexists(runtime)


@pytest.mark.parametrize('reply,exit_code', [('failed', 42), ('empty', 1), ('invalid', 1), ('valid', 0)])
def test_host_call_preserves_remote_failure_and_never_decodes_empty_exec_output(tmp_path, reply, exit_code):
    response = {'failed': "printf 'remote cleanup rejected\\n' >&2; return 42",
                'empty': ':', 'invalid': "printf 'not-json\\n'",
                'valid': "printf '{\"result\":{\"removed\":true}}\\n'"}[reply]
    script = '\n'.join([
        'source ' + shlex.quote(str(ROOT / 'labs/04-isolation/hami-dra/scripts/lib.sh')),
        'RUN_OUT=' + shlex.quote(str(tmp_path)), 'HOST_POD=fixture-host',
        'helper() { :; }',
        'kc() { if [[ " $* " == *" exec "* ]]; then ' + response + '; else printf "{}\\n"; fi; }',
        "host_call '{\"operation\":\"remove-hooks\"}'",
    ])
    result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert 'JSONDecodeError' not in result.stderr and 'Traceback' not in result.stderr
    if reply == 'valid':
        assert json.loads(result.stdout) == {'removed': True}
    else:
        assert not result.stdout
        assert 'host' in result.stderr.lower(), result.stderr


def test_kc_get_list_preserves_api_failure_instead_of_normalizing_it(tmp_path):
    script = '\n'.join([
        'source ' + shlex.quote(str(ROOT / 'labs/04-isolation/hami-dra/scripts/lib.sh')),
        'kc() { printf "API unavailable\\n" >&2; return 23; }',
        'kc_get_list ' + shlex.quote(str(tmp_path / 'objects.json')) + ' -f expected.json',
    ])
    result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
    assert result.returncode == 23
    assert (tmp_path / 'objects.json').read_text() == ''


@pytest.mark.parametrize('failure,expected', [('get', 23), ('identity', 17)])
def test_host_stop_inside_exit_trap_never_deletes_an_unverified_helper(tmp_path, failure, expected):
    script = '\n'.join([
        'source ' + shlex.quote(str(ROOT / 'labs/04-isolation/hami-dra/scripts/lib.sh')),
        'RUN_OUT=' + shlex.quote(str(tmp_path)), 'HOST_POD=fixture-host', 'RUN_ID=example',
        'kc() { if [[ " $* " == *" delete "* ]]; then touch "$RUN_OUT/unexpected-delete"; '
        + ('elif [[ " $* " == *" get "* ]]; then return 23; ' if failure == 'get' else '')
        + 'else printf "{}\\n"; fi; }',
        'helper() { ' + ('return 17;' if failure == 'identity' else ':;') + ' }',
        'status=0; if host_stop; then :; else status=$?; fi; exit "$status"',
    ])
    result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
    assert result.returncode == expected, result.stdout + result.stderr
    assert not (tmp_path / 'unexpected-delete').exists()


@pytest.fixture
def hami_uninstall_cluster(fake_provider_cluster, tmp_path):
    """Run real lifecycle scripts and host cleanup against local external boundaries."""
    cluster = fake_provider_cluster
    backup(cluster)
    assert run_provider(cluster, 'exclude-node.sh', 'dra-a40').returncode == 0
    lab, state_path, actions, env = cluster
    shutil.copytree(ROOT / 'labs/04-isolation/common', lab / 'common')
    shutil.copytree(ROOT / 'labs/04-isolation/hami-dra', lab / 'hami-dra')
    out = Path(env['OUT_DIR'])
    node = tmp_path / 'node'
    host = lab_module('host', 'scripts'); host.HOST_ROOT = node
    before = host.footprint()
    for root in host.HOOK_ROOTS:
        host.host_path(root).mkdir(parents=True)
    host.host_path('/usr/local/vgpu/libvgpu.so').write_bytes(b'installed-library')
    host.host_path('/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io/checkpoint.json').write_text('{"v2":{}}')
    installed = host.footprint()
    for rows in installed.values():
        for row in rows: row['uid'] = 0
    for name, value in [('host-before.json', before), ('host-installed.json', installed)]:
        (out / name).write_text(json.dumps(value))
    provider = yaml.safe_load((lab / 'hami-dra/manifests/provider/daemonset.yaml').read_text())
    provider['metadata'].update(uid='owned-hami', resourceVersion='1')
    provider['metadata']['labels']['app.kubernetes.io/instance'] = 'fixture'
    state = json.loads(state_path.read_text()); state['other_ds'] = [provider]
    state_path.write_text(json.dumps(state))
    expected = {'apiVersion': 'v1', 'kind': 'List', 'items': [provider]}
    for name in ['provider-expected.json', 'provider-owned.json']:
        (out / name).write_text(json.dumps(expected))
    binary = tmp_path / 'bin/kubectl'
    native_binary = binary.with_name('kubectl-native-fixture')
    binary.rename(native_binary)
    binary.write_text(f'#!{sys.executable}\n' + '''import json,os,sys
from pathlib import Path
args=[a for a in sys.argv[1:] if not a.startswith('--request-timeout=')]
if 'get' in args and '-f' in args:
    state=json.load(open(os.environ['LAB04_STATE']))
    print(json.dumps({'items':state['other_ds']}))
elif 'delete' in args and ('deviceclasses' in args or 'clusterrolebindings,clusterroles' in args):
    with open(os.environ['LAB04_ACTIONS'],'a') as f: f.write(json.dumps({'args':args})+'\\n')
elif 'delete' in args and 'daemonsets' in args:
    state=json.load(open(os.environ['LAB04_STATE']))
    labels=dict(pair.split('=',1) for pair in args[args.index('-l')+1].split(','))
    state['other_ds']=[obj for obj in state['other_ds'] if not all(obj['metadata']['labels'].get(k)==v for k,v in labels.items())]
    with open(os.environ['LAB04_STATE'],'w') as f: json.dump(state,f)
    with open(os.environ['LAB04_ACTIONS'],'a') as f: f.write(json.dumps({'args':args})+'\\n')
elif 'patch' in args and os.environ.get('HAMI_RESTORE_FAIL')=='1':
    print('injected native restore API failure',file=sys.stderr);raise SystemExit(37)
else:
    os.execv(''' + repr(str(native_binary)) + ''', ['kubectl',*sys.argv[1:]])
''')
    binary.chmod(0o755)
    bridge = tmp_path / 'host-bridge.py'
    bridge.write_text('''import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('offline_host',sys.argv[1])
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h);h.HOST_ROOT=Path(sys.argv[2])
actual=h.footprint
def footprint():
    result=actual()
    for rows in result.values():
        for row in rows: row['uid']=0
    return result
h.footprint=footprint
args=json.loads(sys.argv[4]);operation=args.pop('operation')
if operation=='remove-hooks':
    state=json.load(open(sys.argv[3]));out=Path(sys.argv[5])
    (out/'host-cleanup-native-state.json').write_text(json.dumps(state))
try: value={'footprint':h.footprint,'remove-hooks':h.remove_hooks}[operation](**args)
except (RuntimeError,ValueError) as error:
    print('host cleanup rejected: '+str(error),file=sys.stderr);raise SystemExit(41)
print(json.dumps(value))
''')
    call = ' '.join(shlex.quote(str(arg)) for arg in [sys.executable, bridge,
                    lab / 'hami-dra/scripts/host.py', node, state_path])
    with (lab / 'hami-dra/scripts/lib.sh').open('a') as stream:
        stream.write('\nhost_start() { HOST_POD=fixture-host; }\nhost_stop() { HOST_POD=""; }\n'
                     'host_call() { ' + call + ' "$1" "$RUN_OUT"; }\n')
    return cluster, node


@pytest.mark.parametrize('cleanup_failure', [False, True])
def test_uninstall_restores_native_provider_before_host_cleanup_even_when_cleanup_fails(hami_uninstall_cluster, cleanup_failure):
    cluster, node = hami_uninstall_cluster
    lab, state_path, actions, env = cluster
    if cleanup_failure:
        (node / 'var/lib/kubelet/plugins/hami-core-gpu.project-hami.io/unknown.lock').write_text('preserve')
    result = subprocess.run(['bash', str(lab / 'hami-dra/scripts/uninstall-provider.sh'), 'fixture'],
                            env=env, capture_output=True, text=True, timeout=12)
    assert result.returncode == (41 if cleanup_failure else 0), result.stdout + result.stderr
    state = json.loads(state_path.read_text())
    at_cleanup = json.loads((Path(env['OUT_DIR']) / 'host-cleanup-native-state.json').read_text())
    for value in (state, at_cleanup):
        assert value['ds']['metadata']['uid'] == 'native-ds'
        assert value['ds']['spec'] == native_ds()['spec']
        assert not value['other_ds']
        assert any(s['spec']['nodeName'] == 'dra-a40' and s['spec']['driver'] == 'gpu.nvidia.com' for s in value['slices'])
        assert next(p for p in value['pods'] if p['spec']['nodeName'] == 'dra-4090')['metadata']['uid'] == 'pod-dra-4090'
    if cleanup_failure:
        assert 'unknown.lock' in result.stderr and 'restored' in result.stderr.lower()
        assert not (Path(env['OUT_DIR']) / 'host-cleanup.verified').exists()
        assert (node / 'usr/local/vgpu/libvgpu.so').exists()
        restored_uid = next(p for p in state['pods'] if p['spec']['nodeName'] == 'dra-a40')['metadata']['uid']
        old_action_count = len(actions.read_text().splitlines())
        (node / 'var/lib/kubelet/plugins/hami-core-gpu.project-hami.io/unknown.lock').unlink()
        retry = subprocess.run(['bash', str(lab / 'hami-dra/scripts/uninstall-provider.sh'), 'fixture'],
                               env=env, capture_output=True, text=True, timeout=12)
        assert retry.returncode == 0, retry.stdout + retry.stderr
        retried_state = json.loads(state_path.read_text())
        assert retried_state['ds']['spec'] == native_ds()['spec']
        assert next(p for p in retried_state['pods'] if p['spec']['nodeName'] == 'dra-a40')['metadata']['uid'] == restored_uid
        retry_actions = [json.loads(line)['args'] for line in actions.read_text().splitlines()[old_action_count:]]
        assert not any('patch' in args or '--raw' in args for args in retry_actions)
        assert (Path(env['OUT_DIR']) / 'host-cleanup.verified').exists()
    else:
        assert (Path(env['OUT_DIR']) / 'host-cleanup.verified').exists()
        assert not (node / 'usr/local/vgpu').exists()


def test_uninstall_does_not_touch_host_files_when_native_restore_fails(hami_uninstall_cluster):
    cluster, node = hami_uninstall_cluster
    lab, state_path, actions, env = cluster
    env['HAMI_RESTORE_FAIL'] = '1'
    result = subprocess.run(['bash', str(lab / 'hami-dra/scripts/uninstall-provider.sh'), 'fixture'],
                            env=env, capture_output=True, text=True, timeout=12)
    assert result.returncode == 37, result.stdout + result.stderr
    assert not (Path(env['OUT_DIR']) / 'host-cleanup-native-state.json').exists()
    assert (node / 'usr/local/vgpu/libvgpu.so').exists()


def test_uninstall_rechecks_host_absence_even_when_a_cleanup_marker_exists(hami_uninstall_cluster):
    cluster, node = hami_uninstall_cluster
    lab, state_path, actions, env = cluster
    (Path(env['OUT_DIR']) / 'host-cleanup.verified').write_text('verified')
    result = subprocess.run(['bash', str(lab / 'hami-dra/scripts/uninstall-provider.sh'), 'fixture'],
                            env=env, capture_output=True, text=True, timeout=12)
    assert result.returncode != 0
    assert 'artifacts are present' in result.stderr
    assert json.loads(state_path.read_text())['ds']['spec'] == native_ds()['spec']
    assert (node / 'usr/local/vgpu/libvgpu.so').exists()


def isolated_hami_scripts(tmp_path):
    lab = tmp_path / 'labs/04-isolation'
    lab.mkdir(parents=True)
    shutil.copytree(ROOT / 'labs/common', lab.parent / 'common')
    shutil.copytree(ROOT / 'labs/04-isolation/common', lab / 'common')
    shutil.copytree(ROOT / 'labs/04-isolation/hami-dra', lab / 'hami-dra')
    return lab / 'hami-dra'


def namespace_cleanup_fixture(tmp_path, contents='empty', owner='dra-book', race=None, delayed=False):
    hami = isolated_hami_scripts(tmp_path)
    out = tmp_path / 'out'; out.mkdir()
    state_path = tmp_path / 'namespace-state.json'
    state_path.write_text(json.dumps({'present': True, 'owner': owner, 'contents': contents,
                                     'uid': 'original-ns', 'resourceVersion': '1', 'calls': [],
                                     'race': race, 'delayed': delayed}))
    bridge = tmp_path / 'namespace-api.py'
    bridge.write_text('''import json,sys
from pathlib import Path
p=Path(sys.argv[1]);state=json.load(open(p));args=sys.argv[2:];state['calls'].append(args)
status=0
if 'api-resources' in args: print('serviceaccounts\\nconfigmaps\\nsecrets\\nevents')
elif 'get' in args:
    kind=args[args.index('get')+1]
    if kind=='namespace':
        if state.get('deletion_reads')==0: state['present']=False
        if state['present']:
            meta={'name':'gpu-isolation-lab','uid':state['uid'],'resourceVersion':state['resourceVersion'],
                  'labels':{'app.kubernetes.io/part-of':state['owner']}}
            if state.get('deletion_reads'):
                meta['deletionTimestamp']='2026-10-12T00:00:00Z';state['deletion_reads']-=1
            print(json.dumps({'metadata':meta}))
    elif '-o' in args and args[args.index('-o')+1]=='name': pass
    else:
        rows=[]
        if kind=='serviceaccounts': rows=[{'metadata':{'name':'default'}}]
        elif kind=='configmaps': rows=[{'metadata':{'name':'kube-root-ca.crt'}}]
        elif kind=='secrets' and state['contents']!='empty':
            labels={'app.kubernetes.io/part-of':'dra-book'} if state['contents']=='owned' else {}
            rows=[{'metadata':{'name':'preserve-secret','labels':labels}}]
        print(json.dumps({'items':rows}))
elif 'delete' in args and ('namespaces' in args or '--raw' in args):
    # The replacement happens after cleanup.sh's final GET, at the API DELETE.
    if state['race']=='uid': state.update(uid='replacement-ns',resourceVersion='2')
    elif state['race']=='ownership': state.update(owner='foreign',resourceVersion='2')
    elif state['race']=='absent': state['present']=False
    if '--raw' in args:
        assert args[args.index('--raw')+1]=='/api/v1/namespaces/gpu-isolation-lab'
        options=json.load(open(args[args.index('-f')+1]));state['delete_options']=options
        if not state['present']:
            print('Error from server (NotFound): Namespace disappeared',file=sys.stderr);status=1
        elif options.get('preconditions',{}).get('uid')!=state['uid'] or options.get('preconditions',{}).get('resourceVersion')!=state['resourceVersion']:
            print('Error from server (Conflict): Namespace preconditions failed',file=sys.stderr);status=1
        elif state['delayed']: state['deletion_reads']=1
        else: state['present']=False
    elif state['owner']=='dra-book': state['present']=False
elif 'delete' not in args: raise SystemExit('unexpected API call')
p.write_text(json.dumps(state))
raise SystemExit(status)
''')
    call = ' '.join(shlex.quote(str(arg)) for arg in [sys.executable, bridge, state_path])
    with (hami / 'scripts/lib.sh').open('a') as stream:
        stream.write('\nkc() { ' + call + ' "$@"; }\n'
                     'host_start() { :; }\nhost_stop() { :; }\nwait_claim_artifacts_absent() { :; }\n'
                     'wait_gpu_clean() { :; }\nbash() { :; }\nsleep() { :; }\n')
    env = dict(os.environ, OUT_DIR=str(out))
    return hami / 'scripts/cleanup.sh', env, state_path


@pytest.mark.parametrize('contents,owner', [('empty', 'dra-book'), ('foreign', 'dra-book'),
                                         ('owned', 'dra-book'), ('empty', 'foreign')])
def test_cleanup_deletes_only_an_empty_dra_book_namespace(tmp_path, contents, owner):
    script, env, state_path = namespace_cleanup_fixture(tmp_path, contents, owner)
    result = subprocess.run(['bash', str(script), 'example'],
                            env=env, capture_output=True, text=True, timeout=10)
    state = json.loads(state_path.read_text())
    should_delete = contents == 'empty' and owner == 'dra-book'
    assert (result.returncode == 0) == should_delete, result.stdout + result.stderr
    assert state['present'] != should_delete
    deletes = [args for args in state['calls'] if 'delete' in args and ('namespaces' in args or '--raw' in args)]
    if should_delete:
        assert len(deletes) == 1
        assert state['delete_options'] == {'apiVersion': 'v1', 'kind': 'DeleteOptions',
                                          'preconditions': {'uid': 'original-ns', 'resourceVersion': '1'}}
        # A second cleanup sees an absent Namespace and is idempotent.
        repeated = subprocess.run(['bash', str(script), 'example'],
                                  env=env, capture_output=True, text=True, timeout=10)
        assert repeated.returncode == 0, repeated.stderr
    else:
        assert not deletes


@pytest.mark.parametrize('race', ['uid', 'ownership'])
def test_cleanup_preserves_namespace_changed_between_final_get_and_delete(tmp_path, race):
    script, env, state_path = namespace_cleanup_fixture(tmp_path, race=race)
    result = subprocess.run(['bash', str(script), 'example'], env=env,
                            capture_output=True, text=True, timeout=10)
    state = json.loads(state_path.read_text())
    assert result.returncode != 0, result.stdout + result.stderr
    assert state['present']
    assert state['uid'] == ('replacement-ns' if race == 'uid' else 'original-ns')
    assert state['owner'] == ('foreign' if race == 'ownership' else 'dra-book')
    assert 'Conflict' in result.stderr


def test_cleanup_accepts_namespace_already_gone_at_delete(tmp_path):
    script, env, state_path = namespace_cleanup_fixture(tmp_path, race='absent')
    result = subprocess.run(['bash', str(script), 'example'], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not json.loads(state_path.read_text())['present']


def test_cleanup_verifies_namespace_absence_after_raw_delete(tmp_path):
    script, env, state_path = namespace_cleanup_fixture(tmp_path, delayed=True)
    result = subprocess.run(['bash', str(script), 'example'], env=env,
                            capture_output=True, text=True, timeout=10)
    state = json.loads(state_path.read_text())
    assert result.returncode == 0, result.stdout + result.stderr
    assert not state['present']
    raw_delete = next(i for i, args in enumerate(state['calls']) if '--raw' in args)
    assert len([args for args in state['calls'][raw_delete + 1:] if 'namespace' in args]) >= 2


def test_runner_prints_each_final_round_verdict_once_after_cleanup(tmp_path):
    hami = isolated_hami_scripts(tmp_path)
    out = tmp_path / 'out'; out.mkdir()
    seed = tmp_path / 'seed-round.py'
    seed.write_text('''import json,shutil,sys
from pathlib import Path
source,p=Path(sys.argv[1]),Path(sys.argv[2]);shutil.copytree(source,p,dirs_exist_ok=True)
meta=json.load(open(p/'trial.json'));meta.update(valid=False,complete=False,cleanup={'verified':False})
(p/'trial.json').write_text(json.dumps(meta))
if (p/'cleanup.json').exists(): (p/'cleanup.json').unlink()
''')
    finish = tmp_path / 'verify-cleanup.py'
    finish.write_text('''import json,sys
from pathlib import Path
p=Path(sys.argv[1]);meta=json.load(open(p/'trial.json'))
meta.update(valid=True,complete=True,cleanup={'verified':True})
(p/'trial.json').write_text(json.dumps(meta));(p/'cleanup.json').write_text('{"verified":true}')
(p/'cleaned.json').write_text('{"cleaned":true}')
''')
    seed_call = ' '.join(shlex.quote(str(arg)) for arg in [sys.executable, seed])
    finish_call = ' '.join(shlex.quote(str(arg)) for arg in [sys.executable, finish])
    with (hami / 'scripts/lib.sh').open('a') as stream:
        stream.write('\npreflight() { :; }\nhost_start() { :; }\nhost_stop() { :; }\n'
                     'kc() { printf \'{"items":[]}\\n\'; }\n'
                     'fixture_round() { ' + seed_call + ' "$1" "$ROUND_DIR"; '
                     'OUT_DIR="$RUN_OUT" command bash "$HAMI_DIR/scripts/collect.sh" "$RUN_ID"; '
                     + finish_call + ' "$ROUND_DIR"; }\n'
                     'runtime_round() { if [[ "$2" == boundary ]]; then fixture_round '
                     + shlex.quote(str(E2 / 'hami-boundary-8g-r1')) + '; else fixture_round '
                     + shlex.quote(str(E2 / 'hami-scope-8g-r1')) + '; fi; }\n'
                     'scope_round() { fixture_round ' + shlex.quote(str(E2B / 'case4a-r1')) + '; }\n')
    # The runner owns sequencing; this external cleanup boundary accepts only
    # complete per-round cleanup evidence before recording the final check.
    (hami / 'scripts/cleanup.sh').write_text('''#!/usr/bin/env bash
set -euo pipefail
python3 - "$OUT_DIR" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);rounds=list(p.glob('*-r1'))
assert len(rounds)==3 and all(json.load(open(r/'cleanup.json'))['verified'] for r in rounds)
(p/'final-cleanup.verified').write_text('verified')
PY
bash "$(dirname "$0")/collect.sh" "$1"
''')
    env = dict(os.environ, OUT_DIR=str(out), SMOKE='1', MODE='runtime')
    result = subprocess.run(['bash', str(hami / 'scripts/run.sh'), 'example'],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    verdicts = [line for line in result.stdout.splitlines() if 'complete=' in line]
    assert len(verdicts) == 3, result.stdout
    assert all('complete=True' in line for line in verdicts)
    assert {line.split(':')[0] for line in verdicts} == {'single-r1', 'dual-process-r1', 'case4a-r1'}
    assert (out / 'final-cleanup.verified').exists()
    assert json.loads((out / 'completed.json').read_text())['complete']


def test_book_groups_contain_every_complete_hami_manifest():
    """Compact multi-document presentation must keep every input field and variant."""
    import re
    lab = ROOT / 'labs/04-isolation/hami-dra'
    page = (ROOT / 'book/04-isolation/hami-dra.md').read_text()
    blocks = re.findall(r'```yaml\n(.*?)\n```', page, re.S)
    seen = []
    groups = 0
    for block in blocks:
        paths = re.findall(r'^# (manifests/\S+\.yaml)$', block, re.M)
        if not paths:
            continue
        groups += 1
        expected = [obj for path in paths for obj in yaml.safe_load_all((lab / path).read_text())]
        assert list(yaml.safe_load_all(block)) == expected, paths
        seen.extend(paths)
    expected_paths = {str(path.relative_to(lab)) for path in (lab / 'manifests').rglob('*.yaml')
                      if path.name != 'kustomization.yaml'}
    assert groups == 6
    assert len(seen) == len(set(seen))
    assert set(seen) == expected_paths
    generators = [block for block in blocks if 'configMapGenerator:' in block]
    assert len(generators) == 1
    assert yaml.safe_load(generators[0]) == yaml.safe_load((lab / 'workload/kustomization.yaml').read_text())


@pytest.mark.parametrize('builder_name,labels', [
    ('e2_tensor_comparison', ['Claim 或每 client 設定上限']),
    ('hami_scope', ['共用 Claim 配額 8 GiB（8192 MiB）', '兩份獨立 Claim 配額合計 16 GiB（16384 MiB）',
                    '停滯輪次的最後完整屏障', '完成輪次']),
])
def test_hami_reference_marks_have_rendered_legends(tmp_path, builder_name, labels):
    """A lone labeled tick was previously lost by the shared SVG legend policy."""
    from tools.figures import ch04, style
    first = getattr(ch04, builder_name)(ROOT / 'data')
    output = tmp_path / 'first.svg'
    style.save_svg(first, output)
    rendered = output.read_text()
    for label in labels:
        assert f'<!-- {label} -->' in rendered
    assert '<text' not in rendered
    second = getattr(ch04, builder_name)(ROOT / 'data')
    repeat = tmp_path / 'second.svg'
    style.save_svg(second, repeat)
    assert output.read_bytes() == repeat.read_bytes()
