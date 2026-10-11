import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

import pytest
import yaml

from conftest import ROOT

LAB = ROOT / 'labs/06-heterogeneous'
E4 = ROOT / 'data/e4'
E4B = ROOT / 'data/e4b'


def analysis_module():
    path = LAB / 'analysis/analyze.py'
    assert path.is_file(), 'the chapter needs a stdlib analysis entry point'
    spec = importlib.util.spec_from_file_location('lab06_analysis', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lab_module(relative):
    path = LAB / relative
    assert path.is_file(), f'missing runnable lab helper: {relative}'
    spec = importlib.util.spec_from_file_location('lab06_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_e4_acceptance_reconstructs_report_without_erasing_failed_attempts():
    summary = analysis_module().analyze(E4, E4B)
    expected = json.loads((E4 / 'acceptance.json').read_text())['case_acceptance']
    assert summary['e4_acceptance'] == expected
    rows = {(r['stage'], r['case']): r for r in summary['e4_acceptance']}
    assert len(rows) == 14
    assert rows['stage1', 'static']['retained_nonaccepted_trials'] == 2
    assert rows['stage2', '4090']['retained_statuses'] == ['blocked']
    assert rows['stage2', 'memory-30gi']['retained_statuses'] == ['unsupported']
    assert rows['stage2', 'nonexistent']['negative_trials'] == 3
    assert sum(r['positive_trials'] for r in rows.values()) == 36


def test_e4b_placements_and_formal_verdicts_preserve_historical_invalid_status():
    summary = analysis_module().analyze(E4, E4B)
    rows = summary['e4b_placements']
    h1 = [r for r in rows if r['case'] == 'h1']
    h2 = [r for r in rows if r['case'] == 'h2']
    assert [(r['repeat'], r['node']) for r in h1] == [(1, 'dra-a40'), (2, 'dra-4090'), (3, 'dra-a40')]
    assert [(r['repeat'], r['node']) for r in h2] == [(1, 'dra-a40'), (2, 'dra-a40'), (3, 'dra-a40')]
    for repeat in (1, 2, 3):
        h5 = [r for r in rows if r['case'] == 'h5' and r['repeat'] == repeat]
        assert {r['node'] for r in h5} == {'dra-a40', 'dra-4090'}
        assert len({r['claim_uid'] for r in h5}) == 2
    assert summary['e4b_counts'] == {'formal_verified': 23, 'formal_invalid': 5, 'offline_verified': 25}
    l1_old = next(r for r in summary['e4b_verdicts'] if r['case'] == 'l1' and r['repeat'] == 1)
    assert l1_old['formal_status'] == 'invalid' and l1_old['offline_status'] == 'verified'


def test_pending_release_requires_unchanged_pod_and_claim_uids(tmp_path):
    analysis = analysis_module()
    summary = analysis.analyze(E4, E4B)
    assert len(summary['pending_windows']) == 3
    for row in summary['pending_windows']:
        assert row['verified'] and row['same_pod_uid'] and row['same_claim_uid']
        assert round(row['pending_seconds'], 1) == 34.0
        assert row['release_offset_seconds'] < row['allocated_offset_seconds']
    damaged = tmp_path / 'e4b'
    shutil.copytree(E4B, damaged)
    trials = json.loads((damaged / 'trials.json').read_text())
    next(t for t in trials if t['case'] == 'h3-h4')['pending_claim_uid'] = 'a-different-claim'
    (damaged / 'trials.json').write_text(json.dumps(trials))
    invalid = analysis.analyze(E4, damaged)['pending_windows'][0]
    assert not invalid['same_claim_uid'] and not invalid['verified']


def test_failure_cancel_retry_release_all_generated_claims_and_keep_api_quirk():
    summary = analysis_module().analyze(E4, E4B)
    rows = summary['lifecycle']
    assert len(rows) == 9
    for row in rows:
        assert row['claims_deleted'] and row['natural_lifecycle_observed']
        assert not row['manual_gpu_cleanup'] and row['claim_count_after'] == 0
        assert row['generated_claim_count'] >= len(row['claim_uids'])
        if row['case'] == 'l2':
            assert row['run_state'] == 'FAILED' and row['task_states'] == ['RUNNING']
            assert row['executor_uids'] == []
        if row['case'] == 'l3':
            assert row['attempt_indices'] == [0, 1]
            assert len(set(row['pod_uids'])) == len(set(row['claim_uids'])) == 2


def test_capacity_summaries_reconstruct_both_report_tables():
    summary = analysis_module().analyze(E4, E4B)
    original = json.loads((E4 / 'acceptance.json').read_text())['capacity_comparison']
    assert summary['e4_capacity_ranges'] == original
    h2 = next(r for r in summary['e4b_capacity_peak'] if r['case'] == 'h2' and r['node'] == 'dra-a40')
    assert h2['reserved_memory_mib'] == 30720
    assert h2['remaining_memory_mib'] == 15348
    assert h2['physical_free_mib'] == 44964
    assert h2['physical_free_minus_remaining_mib'] == 29616
    assert h2['atomic'] is False and abs(h2['nvml_offset_seconds']) < 5
    assert sum(p.stat().st_size for folder in (E4, E4B) for p in folder.rglob('*') if p.is_file()) < 4 * 1024 ** 2
    for folder in (E4, E4B):
        assert (folder / 'SOURCES.md').is_file() and (folder / 'source-hashes.json').is_file()


def test_run_output_summary_keeps_unverified_evidence_separate(tmp_path):
    analysis = analysis_module()
    case = tmp_path / 'cases/h3h4-pending-release-r1'
    case.mkdir(parents=True)
    trial = {'case': 'h3h4-pending-release', 'part': 'capacity', 'round': 1,
             'verdict': 'verified', 'placements': [{'node': 'dra-a40', 'pod_uid': 'p', 'claim_uid': 'c'}],
             'pending_seconds': 4.0, 'same_pod_uid': True, 'same_claim_uid': False, 'claims_deleted': True}
    (case / 'trial.json').write_text(json.dumps(trial))
    result = analysis.analyze_run(tmp_path)
    assert len(result['trials']) == 1
    assert result['trials'][0]['verdict'] == 'incomplete'
    assert 'same Claim UID' in result['trials'][0]['errors'][0]
    assert result['placements'][0]['node'] == 'dra-a40'


@pytest.mark.parametrize('passing_verdict', ['passed', 'PASS'])
def test_run_output_supports_script_passed_verdict_and_retains_failure_reason(tmp_path, passing_verdict):
    analysis = analysis_module()
    for case, trial in [
        ('h1-8gi-any', {'case': 'h1-8gi-any', 'verdict': passing_verdict, 'claims_deleted': False}),
        ('l1', {'case': 'l1', 'verdict': 'failed', 'error': 'executor evidence missing'})]:
        folder = tmp_path / case
        folder.mkdir()
        (folder / 'trial.json').write_text(json.dumps(trial))
    rows = {r['case']: r for r in analysis.analyze_run(tmp_path)['trials']}
    assert rows['h1-8gi-any']['verdict'] == 'incomplete'
    assert rows['h1-8gi-any']['errors'] == ['Claim deletion evidence is missing']
    assert rows['l1']['errors'] == ['executor evidence missing']


def test_run_output_discovers_kfp_case_directories_from_shared_run_root(tmp_path):
    folder = tmp_path / 'kubeflow/h1-r1'
    folder.mkdir(parents=True)
    trial = {'case': 'h1', 'part': 'kubeflow', 'round': 1, 'verdict': 'PASS',
             'claims_deleted': True, 'placements': [{'node': 'dra-a40', 'claim_uid': 'claim', 'pod_uid': 'pod'}]}
    (folder / 'trial.json').write_text(json.dumps(trial))
    result = analysis_module().analyze_run(tmp_path)
    assert len(result['trials']) == 1
    assert result['trials'][0]['case'] == 'h1' and result['trials'][0]['verdict'] == 'PASS'
    assert result['placements'][0]['claim_uid'] == 'claim'


@pytest.mark.parametrize('name', ['e4-acceptance', 'e4-capacity-ranges', 'placements',
                                  'pending-release', 'capacity-physical'])
def test_chapter_figures_render_reproducibly_as_paths(tmp_path, name):
    from tools.figures import REGISTRY, discover, style
    discover()
    assert '06-heterogeneous' in REGISTRY, 'chapter figures must use the shared rendering entry point'
    assert name in REGISTRY['06-heterogeneous']
    outputs = []
    for repeat in range(2):
        fig = REGISTRY['06-heterogeneous'][name](ROOT / 'data')
        path = tmp_path / f'{name}-{repeat}.svg'
        style.save_svg(fig, path)
        outputs.append(path.read_bytes())
    assert outputs[0] == outputs[1]
    assert b'<text' not in outputs[0] and b'dc:date' not in outputs[0]


def test_figure_placement_points_match_actual_allocations():
    from tools.figures import ch06, style
    fig = ch06.placement_chart(ROOT / 'data')
    points = fig.axes[0].collections[0].get_offsets().tolist()
    assert points == [[0.0, 1.0], [1.0, 0.0], [2.0, 1.0],
                      [3.0, 1.0], [4.0, 1.0], [5.0, 1.0]]
    style.plt.close(fig)


def test_pending_chart_legend_does_not_cover_measured_timeline(tmp_path):
    from tools.figures import ch06, style
    fig = ch06.pending_chart(ROOT / 'data')
    style.save_svg(fig, tmp_path / 'pending.svg')
    fig.canvas.draw()
    legends = fig.legends + ([fig.axes[0].get_legend()] if fig.axes[0].get_legend() else [])
    assert legends
    assert all(not legend.get_window_extent().overlaps(fig.axes[0].get_window_extent()) for legend in legends)


@pytest.mark.parametrize('folder', sorted(path.parent.relative_to(LAB)
    for path in LAB.rglob('kustomization.yaml')), ids=str)
def test_every_shipped_kustomization_renders_locally(folder):
    binary = ROOT / '.work/bin/kustomize'
    if not binary.is_file():
        pytest.skip('pinned .work/bin/kustomize is missing')
    result = subprocess.run([str(binary), 'build', '--load-restrictor',
                             'LoadRestrictionsNone', str(LAB / folder)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    objects = list(yaml.safe_load_all(result.stdout))
    assert objects and all(obj and obj.get('apiVersion') and obj.get('kind') for obj in objects)
    claims = {obj['metadata']['name'] for obj in objects if obj['kind'] in ('ResourceClaim', 'ResourceClaimTemplate')}
    for obj in objects:
        assert obj['metadata']['labels']['app.kubernetes.io/part-of'] == 'dra-book'
        assert obj['metadata']['labels']['app.kubernetes.io/component']
        if obj['kind'] == 'Job':
            assert obj['spec']['backoffLimit'] == 0
            pod_spec = obj['spec']['template']['spec']
            assert pod_spec['restartPolicy'] == 'Never'
            if pod_spec.get('resourceClaims'):
                assert not pod_spec.get('nodeName') and not pod_spec.get('nodeSelector')
            else:
                assert pod_spec['nodeSelector']['kubernetes.io/hostname'] == 'dra-non'
            for reference in pod_spec.get('resourceClaims', []):
                name = reference.get('resourceClaimTemplateName') or reference.get('resourceClaimName')
                assert name in claims
        elif obj['kind'] == 'Deployment':
            pod_spec = obj['spec']['template']['spec']
            assert pod_spec['nodeSelector']['kubernetes.io/hostname'] == 'dra-non'
        else:
            continue
        for container in pod_spec.get('containers', []) + pod_spec.get('initContainers', []):
            assert re.search(r'@sha256:[0-9a-f]{64}$', container['image']), container['image']


def test_plain_manifests_parse_and_generic_requests_have_no_hidden_pin():
    parsed = []
    for path in sorted(LAB.rglob('*.yaml')):
        objects = list(yaml.safe_load_all(path.read_text()))
        assert all(obj is not None for obj in objects), path
        parsed.extend(obj for obj in objects if isinstance(obj, dict))
    assert parsed
    for mode in ('native', 'capacity'):
        claims = list(yaml.safe_load_all((LAB / 'selection' / mode / 'claims.yaml').read_text()))
        for obj in claims:
            assert obj['metadata']['labels']['app.kubernetes.io/part-of'] == 'dra-book'
            assert obj['metadata']['labels']['app.kubernetes.io/component']
            assert obj['spec']['metadata']['labels']['app.kubernetes.io/part-of'] == 'dra-book'
            request = obj['spec']['spec']['devices']['requests'][0]['exactly']
            if mode == 'capacity' or 'any-gpu' in obj['metadata']['name']:
                assert not request.get('selectors')
            if mode == 'capacity':
                assert request['capacity']['requests']['memory'] in ('8Gi', '30Gi')
                assert str(request['capacity']['requests']['cores']) == '25'


def test_selection_smoke_defaults_and_recovery_reject_replaced_identity():
    control = lab_module('scripts/control.py')
    assert control.select_cases('native', '', True) == ['a40', 'rtx4090', 'any-gpu', 'any-gpu-pair', 'attribute', 'nonexistent', 'prefer-4090']
    assert control.select_cases('capacity', '', True) == ['h1-8gi-any', 'h2-30gi-any']
    with pytest.raises(ValueError):
        control.select_cases('capacity', 'h3h4-pending-release', True)
    control.verify_same_identity([{'pod_uid': 'pod', 'claim_uid': 'claim'}],
                                 [{'pod_uid': 'pod', 'claim_uid': 'claim'}])
    for new in ({'pod_uid': 'pod', 'claim_uid': 'replacement'},
                {'pod_uid': 'replacement', 'claim_uid': 'claim'}):
        with pytest.raises(ValueError, match='replaced'):
            control.verify_same_identity([{'pod_uid': 'pod', 'claim_uid': 'claim'}], [new])
    assert control.holder_requests(46068 * 1024 ** 2, 24564 * 1024 ** 2, 1024 ** 2) == {
        'pending': '15349Mi', 'a40': '37876Mi', 'rtx4090': '16372Mi'}


@pytest.mark.parametrize('name,guard', [
    ('kubeflow/scripts/install-kfp.sh', 'ALLOW_KFP_INSTALL'),
    ('kubeflow/scripts/uninstall-kfp.sh', 'ALLOW_KFP_UNINSTALL'),
    ('scripts/install-provider.sh', 'I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER'),
    ('scripts/restore-provider.sh', 'I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER'),
    ('../04-isolation/provider/scripts/backup-nodes.sh', 'I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER'),
    ('../04-isolation/provider/scripts/exclude-nodes.sh', 'I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER'),
    ('../04-isolation/provider/scripts/restore-nodes.sh', 'I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER'),
    ('../04-isolation/hami-dra/scripts/install-provider-nodes.sh', 'I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER'),
    ('../04-isolation/hami-dra/scripts/uninstall-provider-nodes.sh', 'I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER')])
def test_cluster_change_guards_stop_before_any_kubectl(name, guard, tmp_path):
    script = LAB / name
    assert script.is_file(), f'missing guarded lifecycle command: {name}'
    binary = tmp_path / 'kubectl'
    log = tmp_path / 'kubectl-calls'
    binary.write_text('#!/bin/sh\nprintf called >> "$LAB06_CALL_LOG"\nexit 97\n')
    binary.chmod(0o755)
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'LAB06_CALL_LOG': str(log), 'KFP_STATE_DIR': str(tmp_path / 'state')}
    env.pop('ALLOW_KFP_INSTALL', None)
    env.pop('ALLOW_KFP_UNINSTALL', None)
    env.pop('I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER', None)
    for value in (None, '0'):
        if value is None:
            env.pop(guard, None)
        else:
            env[guard] = value
        result = subprocess.run(['bash', str(script)], capture_output=True, text=True, env=env, timeout=10)
        assert result.returncode != 0
        assert guard in result.stderr
        assert not log.exists(), 'guard must be checked before read-only or mutating kubectl calls'


def test_kfp_cleanup_requires_recorded_uid_and_keeps_original_crd_body():
    state = lab_module('kubeflow/scripts/kfp_state.py')
    old = {'apiVersion': 'apiextensions.k8s.io/v1', 'kind': 'CustomResourceDefinition',
           'metadata': {'name': 'workflows.argoproj.io', 'uid': 'original-uid', 'resourceVersion': '7',
                        'labels': {'app.kubernetes.io/part-of': 'dra-book', 'dra-book/run-id': 'run'}},
           'spec': {'group': 'argoproj.io', 'scope': 'Namespaced'}, 'status': {'conditions': []}}
    live = copy.deepcopy(old)
    live['metadata']['resourceVersion'] = '8'
    live['status']['conditions'] = [{'type': 'Established'}]
    assert state.preserved_body(old) == state.preserved_body(live)
    assert state.compatible(live, {'kind': old['kind'], 'spec': {'group': 'argoproj.io'}})
    options = state.deletion_options(live, old, 'run')
    assert options['preconditions'] == {'uid': 'original-uid', 'resourceVersion': '8'}
    changed = copy.deepcopy(live)
    changed['metadata']['uid'] = 'new-owner'
    with pytest.raises(RuntimeError, match='UID changed'):
        state.deletion_options(changed, old, 'run')
    before = {'customresourcedefinitions': [old]}
    state.verify_preserved(before, {'customresourcedefinitions': [live]})
    for key, value in [('uid', 'new-owner'), ('spec', {'group': 'different.io'})]:
        changed = copy.deepcopy(live)
        if key == 'uid':
            changed['metadata'][key] = value
        else:
            changed[key] = value
        with pytest.raises(RuntimeError, match='Pre-existing'):
            state.verify_preserved(before, {'customresourcedefinitions': [changed]})


def test_two_node_provider_restore_refuses_changed_original_uid_or_spec(tmp_path):
    path = ROOT / 'labs/04-isolation/provider/scripts/control_nodes.py'
    spec = importlib.util.spec_from_file_location('lab06_provider_nodes', path)
    nodes = importlib.util.module_from_spec(spec)
    previous_path = sys.path[:]
    previous_control = sys.modules.pop('control', None)
    try:
        spec.loader.exec_module(nodes)
    finally:
        sys.path[:] = previous_path
        sys.modules.pop('control', None)
        if previous_control is not None:
            sys.modules['control'] = previous_control
    baseline = {'kind': 'DaemonSet', 'metadata': {'namespace': 'nvidia-dra-driver', 'name': 'native', 'uid': 'original'},
                'spec': {'updateStrategy': {'type': 'RollingUpdate'}, 'template': {'spec': {'containers': [{'image': 'pinned'}],
                  'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                    {'matchExpressions': [{'key': 'gpu', 'operator': 'Exists'}]},
                    {'matchExpressions': [{'key': 'alternate', 'operator': 'Exists'}]}]}}}}}}}
    folder = tmp_path / 'provider-nodes-backup'
    folder.mkdir()
    (folder / 'backup.json').write_text(json.dumps({'nodes': ['dra-4090', 'dra-a40'], 'uid': 'original', 'protected_pods': {}}))
    (folder / 'original-daemonset.json').write_text(json.dumps(baseline))
    session = nodes.NativeNodes(tmp_path, ['dra-4090', 'dra-a40'])
    assert session.phase(baseline) == 'original'
    excluded = session.excluded_spec(baseline['spec'])
    terms = excluded['template']['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
    for term in terms:
        assert {node for field in term['matchFields'] for node in field['values']} == {'dra-a40', 'dra-4090'}
        assert all(field['key'] == 'metadata.name' and field['operator'] == 'NotIn' for field in term['matchFields'])
    assert baseline['spec']['updateStrategy']['type'] == 'RollingUpdate'
    for damage in ('uid', 'spec'):
        changed = copy.deepcopy(baseline)
        if damage == 'uid':
            changed['metadata']['uid'] = 'replacement'
        else:
            changed['spec']['template']['spec']['containers'][0]['image'] = 'changed'
        with pytest.raises(ValueError, match='UID changed|spec changed'):
            session.phase(changed)
    pod = {'metadata': {'namespace': nodes.NS, 'name': 'protected-plugin', 'uid': 'protected-uid',
                        'ownerReferences': [{'kind': 'DaemonSet', 'uid': 'original', 'controller': True}]},
           'spec': {'nodeName': 'dra-4090'}}
    session.state['protected_pods'] = {'dra-4090': [nodes.original_control.identity(pod)]}
    session.protected({'items': [pod]})
    replaced = copy.deepcopy(pod)
    replaced['metadata']['uid'] = 'replacement-plugin'
    with pytest.raises(ValueError, match='unselected native plugin UID changed'):
        session.protected({'items': [replaced]})


def test_annotated_book_yaml_is_the_complete_linked_lab_file():
    blocks = []
    for page in (ROOT / 'book/06-heterogeneous').glob('*.md'):
        text = page.read_text()
        matches = re.findall(r'<!-- lab-file: ([^>]+) -->.*?```yaml\n(.*?)\n```', text, re.S)
        for filename, body in matches:
            path = ROOT / filename.strip()
            assert path.resolve().is_relative_to(ROOT / 'labs'), filename
            assert path.is_file(), filename
            assert body.rstrip() == path.read_text().rstrip(), (page.name, filename)
            blocks.append(filename)
    assert blocks, 'full deployed YAML blocks need lab-file markers for equality checks'


def book_table(header):
    text = (ROOT / 'book/06-heterogeneous/experiments.md').read_text()
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(header))
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith('|'):
            break
        rows.append([cell.strip() for cell in line.strip('|').split('|')])
    return rows


def test_book_acceptance_and_capacity_tables_equal_analysis():
    summary = analysis_module().analyze(E4, E4B)
    expected = [[r['stage'], r['case'], r['mode'] or 'unavailable',
                 ', '.join(map(str, r['accepted_repeats'])) or '無',
                 str(r['positive_trials']), str(r['negative_trials']),
                 str(r['retained_nonaccepted_trials']), r['status']] for r in summary['e4_acceptance']]
    assert book_table('| 階段 | 案例 | Provider |') == expected
    def interval(values):
        return '不適用' if values is None else ' … '.join(f'{v / 1024 ** 3:.3f}' for v in values)
    expected = [[r['node'], r['allocation_mode'], str(r['matched_samples']),
                 interval(r['schedulable_remaining_range_bytes']), interval(r['physical_free_range_bytes']),
                 interval(r['difference_range_bytes']), str(r['maximum_match_gap_seconds'])]
                for r in summary['e4_capacity_ranges']]
    assert book_table('| 節點 | Provider 模型 |') == expected
    selected = [r for r in summary['e4b_capacity_peak'] if r['case'] in ('h1', 'h2', 'h3-h4', 'h5')]
    expected = [[r['case'], r['node'], f"{r['reserved_memory_mib']:.0f}", f"{r['remaining_memory_mib']:.0f}",
                 f"{r['reserved_cores']}／{r['remaining_cores']}",
                 f"{r['physical_used_mib']:.0f}／{r['physical_free_mib']:.0f}",
                 f"{r['physical_free_minus_remaining_mib']:.0f}", f"{r['nvml_offset_seconds']:.3f}"] for r in selected]
    assert book_table('| 案例 | 節點 | reserved MiB |') == expected


def test_book_placements_and_pending_windows_equal_analysis():
    summary = analysis_module().analyze(E4, E4B)
    expected = []
    for case in ('h1', 'h2'):
        rows = [r for r in summary['e4b_placements'] if r['case'] == case]
        expected.append([case.upper(), rows[0]['requested_memory'] + '／25 cores', *[r['node'] for r in rows]])
    assert book_table('| 案例 | 需求 | r1 |') == expected
    expected = [[f"r{r['repeat']}", f"{r['pending_seconds']:.1f}", f"{r['release_offset_seconds']:.3f}",
                 f"{r['allocated_offset_seconds']:.3f}", '是' if r['same_pod_uid'] else '否',
                 '是' if r['same_claim_uid'] else '否'] for r in summary['pending_windows']]
    assert book_table('| 輪次 | 確認 Pending 至 release s |') == expected


@pytest.mark.parametrize('relative', ['scripts/control.py', '../04-isolation/provider/scripts/control_nodes.py'])
@pytest.mark.parametrize('as_list', [False, True])
def test_kubectl_create_preserves_all_objects_from_json_stream(tmp_path, monkeypatch, relative, as_list):
    module = lab_module(relative)
    objects = [
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'code'}},
        {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': 'worker'}},
    ]
    output = json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': objects}) if as_list else '\n'.join(map(json.dumps, objects)) + '\n'

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr='')

    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    actual = module.Kube(tmp_path).call('create', '--dry-run=client', '-f', 'manifests.yaml', '-o', 'json', json_result=True)
    assert actual['items'] == objects


def test_selection_rounds_use_distinct_release_keys(tmp_path, monkeypatch):
    module = lab_module('scripts/control.py')
    runner = module.Selection(tmp_path, 'same-run')
    monkeypatch.setattr(runner, 'snapshot', lambda *args: ({'items': []}, {'items': []}))
    monkeypatch.setattr(runner, 'release_reset', lambda: None)
    monkeypatch.setattr(runner, 'start_sampling', lambda *args: None)
    monkeypatch.setattr(runner, 'stop_sampling', lambda: None)
    captured = []
    monkeypatch.setattr(runner, 'apply', lambda directory, name, objects: captured.extend(objects))

    def stop_at_hold(*args):
        raise RuntimeError('fake stop before cluster work')

    monkeypatch.setattr(runner, 'wait_hold', stop_at_hold)
    job = {'kind': 'Job', 'metadata': {'name': 'native-any-gpu'}, 'spec': {'template': {'spec': {
        'containers': [{'args': ['--task-id', 'native-any-gpu']}],
    }}}}
    for repeat in (1, 2):
        with pytest.raises(RuntimeError, match='fake stop'):
            runner.case('native', 'any-gpu', repeat, False, [job])
    task_ids = [obj['spec']['template']['spec']['containers'][0]['args'][1] for obj in captured]
    assert len(task_ids) == len(set(task_ids)) == 2
    assert all(task_id != 'native-any-gpu' for task_id in task_ids)
    assert job['spec']['template']['spec']['containers'][0]['args'][1] == 'native-any-gpu'


@pytest.fixture
def provider_nodes_module(monkeypatch):
    monkeypatch.setitem(sys.modules, 'control_nodes', lab_module('../04-isolation/provider/scripts/control_nodes.py'))
    previous_path = sys.path[:]
    try:
        return lab_module('../04-isolation/hami-dra/scripts/provider_nodes.py')
    finally:
        sys.path[:] = previous_path


def test_provider_can_clean_created_helper_after_ready_failure(tmp_path, provider_nodes_module):
    module = provider_nodes_module
    session = module.HamiNodes(tmp_path, 'provider-test', ['dra-a40'])
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'created-helper', 'uid': 'helper-uid'},
           'spec': {'nodeName': 'dra-a40', 'containers': [{'name': 'hami-host-helper',
                     'image': module.HOST_IMAGE, 'securityContext': {'privileged': True}}],
                    'volumes': [{'name': 'host-root', 'hostPath': {'path': '/'}}]}}

    class FakeKube:
        def __init__(self):
            self.gets = 0
            self.deleted = []

        def get(self, *args):
            if args[0] == 'pod':
                return {} if self.deleted else pod
            self.gets += 1
            return {'items': [] if self.gets == 1 else [pod]}

        def call(self, *args, **kwargs):
            if args[0] == 'patch':
                return pod
            if args[0] == 'wait':
                raise RuntimeError('fake helper readiness failure')
            return ''

        def guarded_delete(self, obj):
            self.deleted.append(obj['metadata']['uid'])

        def wait(self, check, *args):
            assert check()

    session.kube = FakeKube()
    with pytest.raises(RuntimeError, match='fake helper readiness failure'):
        session.host_start('dra-a40')
    assert (tmp_path / 'dra-a40/host-helper-created.json').exists()
    session.stop_helpers()
    assert session.kube.deleted == ['helper-uid']
    assert not session.helpers


@pytest.fixture
def kfp_modules(monkeypatch):
    state = lab_module('kubeflow/scripts/kfp_state.py')
    monkeypatch.setitem(sys.modules, 'kfp_state', state)
    return state, lab_module('kubeflow/scripts/submit.py')


def test_kfp_owned_reuse_refuses_changed_code_or_claim_spec(kfp_modules, monkeypatch, tmp_path):
    state, _ = kfp_modules
    for desired in (
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'gpu-code', 'namespace': 'n'},
         'immutable': True, 'data': {'gpu_workload.py': 'new source'}},
        {'apiVersion': 'resource.k8s.io/v1', 'kind': 'ResourceClaimTemplate',
         'metadata': {'name': 'gpu-memory-small', 'namespace': 'n'},
         'spec': {'spec': {'devices': {'requests': [{'name': 'gpu', 'exactly': {'deviceClassName': 'new-class'}}]}}}}):
        recorded = copy.deepcopy(desired)
        state.put_labels(recorded, 'run')
        recorded['metadata']['uid'] = 'owned'
        if recorded['kind'] == 'ConfigMap':
            recorded['data']['gpu_workload.py'] = 'old source'
        else:
            recorded['spec']['spec']['devices']['requests'][0]['exactly']['deviceClassName'] = 'old-class'
        monkeypatch.setattr(state, 'get', lambda *args, **kwargs: copy.deepcopy(recorded))
        monkeypatch.setattr(state, 'kc', lambda *args, **kwargs: pytest.fail('reuse must not mutate the API'))
        with pytest.raises(RuntimeError, match='input|desired|changed'):
            state.create_owned(desired, {'run_id': 'run', 'created': [recorded]}, tmp_path / 'state.json')


def test_kfp_owned_reuse_accepts_api_defaults_and_refuses_removed_input(kfp_modules, monkeypatch, tmp_path):
    state, _ = kfp_modules
    desired = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'gpu-code', 'namespace': 'n'},
               'immutable': True, 'data': {'gpu_workload.py': 'same source'}}
    state.put_labels(desired, 'run')
    recorded = copy.deepcopy(desired)
    recorded['metadata'].update(uid='owned', resourceVersion='1', creationTimestamp='t')
    input_key = json.dumps(list(state.identity(desired)))
    installation = {'run_id': 'run', 'created': [recorded], 'inputs': {input_key: copy.deepcopy(desired)}}
    live = copy.deepcopy(recorded)
    live['metadata']['resourceVersion'] = '2'
    monkeypatch.setattr(state, 'get', lambda *args, **kwargs: copy.deepcopy(live))
    assert state.create_owned(copy.deepcopy(desired), installation, tmp_path / 'state.json') == live
    desired.pop('immutable')
    with pytest.raises(RuntimeError, match='input|desired|changed'):
        state.create_owned(desired, installation, tmp_path / 'state.json')


def test_kfp_control_uses_unique_trial_key_despite_old_projection(kfp_modules, monkeypatch, tmp_path):
    state_helper, submit = kfp_modules
    installation = {'run_id': 'run', 'namespace': 'n', 'device_class': 'class', 'created': []}
    monkeypatch.setattr(submit, 'get', lambda *args, **kwargs: None)
    monkeypatch.setattr(submit, 'create_owned', lambda obj, *args: obj)
    paths = []
    for task_id in ('h1-r1', 'h2-r1', 'h1-r2'):
        folder = tmp_path / task_id
        folder.mkdir()
        path = submit.prepare_case(tmp_path / 'state.json', installation, task_id[:2], folder, task_id)
        ir, platform = list(yaml.safe_load_all(path.read_text()))
        params = ir['root']['dag']['tasks']['gpu-task']['inputs']['parameters']
        assert params['task_id']['runtimeValue']['constant'] == task_id
        labels = platform['platforms']['kubernetes']['deploymentSpec']['executors']['exec-gpu-task']['podMetadata']['labels']
        assert labels['task-id'] == task_id and labels['dra-book/run-id'] == 'run'
        paths.append(params['task_id']['runtimeValue']['constant'])
    assert len(set(paths)) == 3
    old_control = {'run_id': 'run', 'release': {'h1-r1': True}}
    assert not old_control['release'].get(paths[-1], False)


def test_kfp_watch_reports_unexpected_eof(kfp_modules, tmp_path):
    _, submit = kfp_modules
    watch = submit.Watch.__new__(submit.Watch)
    watch.path, watch.resource, watch.error = tmp_path / 'watch.jsonl', 'pods', None
    watch.events, watch.seen = [], {}
    watch.lock, watch.stopping, watch.on_object = threading.Lock(), threading.Event(), None
    class Process:
        stdout = io.StringIO('')
        def poll(self): return 1
        def wait(self, timeout=None): return 1
    watch.process = Process()
    watch.read()
    assert watch.error and 'EOF' in str(watch.error)


def test_kfp_watch_starts_logs_for_executor_unseen_by_poll(kfp_modules, monkeypatch, tmp_path):
    _, submit = kfp_modules
    executor = {'kind': 'Pod', 'metadata': {'uid': 'p', 'name': 'short-lived', 'labels': {'task-id': 'l1-r1'}},
                'spec': {'resourceClaims': [{'name': 'gpu'}]},
                'status': {'containerStatuses': [{'name': 'main', 'state': {'running': {}}}]}}
    calls = []
    class LogProcess:
        def poll(self): return None
    monkeypatch.setattr(submit.subprocess, 'Popen', lambda args, **kwargs: calls.append(args) or LogProcess())
    capture = submit.LogCapture('n', tmp_path, 'l1-r1')
    watch = submit.Watch.__new__(submit.Watch)
    watch.path, watch.resource, watch.error = tmp_path / 'watch.jsonl', 'pods', None
    watch.events, watch.seen = [], {}
    watch.lock, watch.stopping = threading.Lock(), threading.Event()
    watch.on_object = capture.observe
    class WatchProcess:
        stdout = io.StringIO(json.dumps({'type': 'MODIFIED', 'object': executor}) + '\n')
        def poll(self): return 0
        def wait(self, timeout=None): return 0
    watch.process = WatchProcess()
    watch.stopping.set()
    watch.read()
    assert len(calls) == 1 and '--follow' in calls[0] and 'short-lived' in calls[0]
    assert 'p' in capture.seen
    for _, output, error in capture.streams.values():
        output.close(); error.close()


def test_kfp_natural_release_requires_deleted_allocated_claim_uid(kfp_modules):
    _, submit = kfp_modules
    placements = [{'pod_uid': 'p', 'claim_uid': 'allocated'}]
    deleted = lambda uid: {'type': 'DELETED', 'object': {'metadata': {
        'uid': uid, 'ownerReferences': [{'kind': 'Pod', 'uid': 'p', 'controller': True}]}}}
    assert not submit.natural_release(placements, [], {'items': []})
    assert not submit.natural_release(placements, [deleted('unrelated')], {'items': []})
    assert submit.natural_release(placements, [deleted('allocated')], {'items': []})
    assert not submit.natural_release(placements, [deleted('allocated')], {'items': [{}]})


def test_kfp_cuda_acceptance_binds_log_pod_claim_and_gpu_uuid(kfp_modules):
    _, submit = kfp_modules
    placement = {'pod_uid': 'p', 'claim_uid': 'c', 'driver': 'hami-core-gpu.project-hami.io',
                 'pool': 'dra-a40', 'node': 'dra-a40', 'device': 'gpu'}
    slices = {'items': [{'spec': {'driver': placement['driver'], 'pool': {'name': 'dra-a40'}, 'nodeName': 'dra-a40',
                                'devices': [{'name': 'gpu', 'attributes': {'uuid': {'string': 'GPU-right'}}}]}}]}
    events = [{ 'event': event, 'pod_uid': 'p', 'log_pod_uid': 'p', 'gpu_uuid': 'GPU-right',
                'task_id': 'l1-r1', 'run_id': 'run', 'correct': True,
                'intended_workload_exit_code': 42} for event in ('READY', 'CORRECT', 'CONTROLLED_FAILURE', 'WORKLOAD_EXIT')]
    assert submit.cuda_identity_verified([placement], events, slices, 'run', 'l1-r1', require_exit=42)
    for field, value in [('pod_uid', 'foreign'), ('gpu_uuid', 'GPU-wrong'), ('task_id', 'old-task'), ('log_pod_uid', 'other-log')]:
        damaged = [{**event, field: value} for event in events]
        assert not submit.cuda_identity_verified([placement], damaged, slices, 'run', 'l1-r1', require_exit=42)


@pytest.mark.parametrize('foreign_kind', ['Service', 'Secret', 'Deployment'])
def test_selection_refuses_foreign_namespace_resources(tmp_path, foreign_kind):
    module = lab_module('scripts/control.py')
    runner = module.Selection(tmp_path, 'run-one')
    namespace = {'kind': 'Namespace', 'metadata': {'name': module.NS, 'uid': 'namespace-uid', 'labels': {
        module.BOOK: 'dra-book', module.INSTANCE: 'run-one'}}}
    foreign = {'kind': foreign_kind, 'metadata': {'name': 'foreign-object', 'namespace': module.NS, 'uid': 'foreign-uid'}}
    foreign['metadata']['annotations'] = {'private-note': 'example'}
    if foreign_kind == 'Secret':
        foreign['data'] = {'private': 'example'}

    class FakeKube:
        def get(self, kind, *args):
            if kind == 'namespace':
                return namespace
            return {'items': [foreign] if kind == 'foreigns.example.io' else []}

        def call(self, *args):
            assert args[0] == 'api-resources'
            return 'foreigns.example.io\n'

    runner.kube = FakeKube()
    with pytest.raises(ValueError, match='foreign'):
        runner.namespace_owned()
    evidence = json.loads((tmp_path / 'namespace-ownership.json').read_text())
    assert evidence['objects'] == [{'kind': foreign_kind, 'name': 'foreign-object', 'uid': 'foreign-uid'}]


def test_selection_allows_owned_controller_uid_closure(tmp_path):
    module = lab_module('scripts/control.py')
    runner = module.Selection(tmp_path, 'run-one')
    owned = {'kind': 'Job', 'metadata': {'name': 'owned-worker', 'uid': 'job-uid', 'labels': {
        module.BOOK: 'dra-book', module.INSTANCE: 'run-one'}}}
    pod = {'kind': 'Pod', 'metadata': {'name': 'owned-pod', 'uid': 'pod-uid', 'ownerReferences': [
        {'kind': 'Job', 'name': 'owned-worker', 'uid': 'job-uid', 'controller': True}]}}
    claim = {'kind': 'ResourceClaim', 'metadata': {'name': 'owned-claim', 'uid': 'claim-uid', 'ownerReferences': [
        {'kind': 'Pod', 'name': 'owned-pod', 'uid': 'pod-uid', 'controller': True}]}}
    default = {'kind': 'ServiceAccount', 'metadata': {'name': 'default', 'uid': 'default-sa-uid'}}
    root_ca = {'kind': 'ConfigMap', 'metadata': {'name': 'kube-root-ca.crt', 'uid': 'root-ca-uid'}}

    class FakeKube:
        def get(self, kind, *args):
            if kind == 'namespace':
                return {'kind': 'Namespace', 'metadata': {'name': module.NS, 'uid': 'namespace-uid', 'labels': {
                    module.BOOK: 'dra-book', module.INSTANCE: 'run-one'}}}
            return {'items': [claim, pod, owned, default, root_ca]}

        def call(self, *args):
            return 'objects.example.io\n'

    runner.kube = FakeKube()
    assert runner.namespace_owned() is True


def test_selection_foreign_noncontroller_reference_is_not_adopted(tmp_path):
    module = lab_module('scripts/control.py')
    runner = module.Selection(tmp_path, 'run-one')
    owned = {'kind': 'Job', 'metadata': {'name': 'owned-worker', 'uid': 'job-uid', 'labels': {
        module.BOOK: 'dra-book', module.INSTANCE: 'run-one'}}}
    foreign = {'kind': 'Secret', 'metadata': {'name': 'foreign-object', 'uid': 'foreign-uid', 'ownerReferences': [
        {'kind': 'Job', 'name': 'owned-worker', 'uid': 'job-uid', 'controller': False}]}}

    class FakeKube:
        def get(self, kind, *args):
            if kind == 'namespace':
                return {'kind': 'Namespace', 'metadata': {'name': module.NS, 'uid': 'namespace-uid', 'labels': {
                    module.BOOK: 'dra-book', module.INSTANCE: 'run-one'}}}
            return {'items': [] if ',' in kind else [owned, foreign]}

        def call(self, *args):
            return 'objects.example.io\n'

    runner.kube = FakeKube()
    with pytest.raises(ValueError, match='foreign'):
        runner.namespace_owned()


def test_kfp_natural_release_requires_deleted_unallocated_trial_claim_uid(kfp_modules):
    _, submit = kfp_modules
    placements = [{'claim_uid': 'allocated', 'pod_uid': 'p'}]
    def event(kind, uid, owner='p'):
        return {'type': kind, 'object': {'metadata': {'uid': uid, 'ownerReferences': [{'kind': 'Pod', 'uid': owner, 'controller': True}]}}}
    events = [event('ADDED', 'ephemeral'), event('DELETED', 'allocated'), event('ADDED', 'prior-trial', 'prior-pod')]
    assert submit.generated_claim_uids(events, {'p'}) == {'ephemeral', 'allocated'}
    assert not submit.natural_release(placements, events, {'items': []})
    events.append(event('DELETED', 'ephemeral'))
    assert submit.natural_release(placements, events, {'items': []})


def test_kfp_namespace_delete_refuses_replaced_recorded_child_uid(kfp_modules):
    state, _ = kfp_modules
    recorded = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'namespace': 'n', 'name': 'gpu-code', 'uid': 'original'}}
    state.put_labels(recorded, 'run')
    installation = {'namespace': 'n', 'run_id': 'run', 'created': [recorded]}
    state.require_recorded_namespace_objects([copy.deepcopy(recorded)], installation)
    replacement = copy.deepcopy(recorded)
    replacement['metadata']['uid'] = 'replacement'
    with pytest.raises(RuntimeError, match='UID changed'):
        state.require_recorded_namespace_objects([replacement], installation)



@pytest.mark.parametrize('change', ['uid', 'spec'])
def test_provider_refuses_replaced_publication_before_stopping_daemonsets(tmp_path, monkeypatch, provider_nodes_module, change):
    module = provider_nodes_module
    session = module.HamiNodes(tmp_path, 'provider-test', ['dra-a40'])
    daemonset = {'apiVersion': 'apps/v1', 'kind': 'DaemonSet', 'metadata': {
        'name': 'dra-a40-hami-provider', 'uid': 'own-provider-uid', 'namespace': module.NS}}
    original = {'apiVersion': 'resource.k8s.io/v1', 'kind': 'ResourceSlice', 'metadata': {
        'name': 'published-gpu', 'uid': 'original-publication-uid'}, 'spec': {
        'driver': module.DRIVER, 'nodeName': 'dra-a40', 'pool': {'name': 'dra-a40', 'generation': 1},
        'devices': [{'name': 'hami-gpu-0'}]}}
    current = json.loads(json.dumps(original))
    if change == 'uid':
        current['metadata']['uid'] = 'foreign-replacement-uid'
    else:
        current['spec']['pool']['generation'] = 2
    module.save(session.expected, {'items': [daemonset]})
    module.save(session.owned, {'items': [daemonset]})
    module.save(tmp_path / 'provider-nodes-ready-slices.json', {'items': [original]})
    for name in ('host-before.json', 'host-installed.json'):
        module.save(tmp_path / 'dra-a40' / name, {})
    deleted = []

    class FakeKube:
        def get(self, kind, *args):
            return {'items': [current] if kind == 'resourceslices' else []}

        def guarded_delete(self, obj):
            deleted.append(obj)

        def wait(self, *args):
            return None

    session.kube = FakeKube()
    monkeypatch.setattr(session, 'existing', lambda: {'items': [daemonset]})
    monkeypatch.setattr(session, 'host_start', lambda *args: None)
    monkeypatch.setattr(session, 'host', lambda *args: {'/usr/local/vgpu': [],
        '/var/lib/kubelet/plugins/' + module.DRIVER: [], '/tmp/vgpulock': []})
    for method in ('require_unallocated', 'protected', 'idle', 'check_slice', 'restore'):
        monkeypatch.setattr(session.native, method, lambda *args, **kwargs: None)
    monkeypatch.setattr(session.native, 'phase', lambda *args: 'excluded')
    with pytest.raises(ValueError, match='UID|spec|publication'):
        session.uninstall()
    assert deleted == []


def test_provider_records_partial_publication_without_adopting_replacements(tmp_path, monkeypatch, provider_nodes_module):
    module = provider_nodes_module
    session = module.HamiNodes(tmp_path, 'provider-test', ['dra-a40', 'dra-4090'])
    original = {'kind': 'DaemonSet', 'metadata': {'namespace': module.NS, 'name': module.DS, 'uid': 'native-uid'}}
    provider = {'kind': 'DaemonSet', 'metadata': {'namespace': module.NS, 'name': 'dra-a40-hami-provider', 'uid': 'owned-provider-uid'}}
    pod = {'kind': 'Pod', 'metadata': {'namespace': module.NS, 'name': 'owned-plugin', 'uid': 'pod-uid', 'ownerReferences': [
        {'kind': 'DaemonSet', 'name': 'dra-a40-hami-provider', 'uid': 'owned-provider-uid', 'controller': True}]},
        'spec': {'nodeName': 'dra-a40'}}
    publication = {'kind': 'ResourceSlice', 'metadata': {'name': 'published-gpu', 'uid': 'slice-uid'},
        'spec': {'driver': module.DRIVER, 'nodeName': 'dra-a40', 'pool': {'name': 'dra-a40'}, 'devices': [{'name': 'hami-gpu-0'}]}}

    class FakeKube:
        def get(self, kind, *args):
            if kind == 'daemonset':
                return original
            return {'items': [pod] if kind == 'pods' else [original, provider]}

    session.kube = FakeKube()
    monkeypatch.setattr(session, 'existing', lambda: {'items': [provider]})
    monkeypatch.setattr(session.native, 'phase', lambda *args: 'excluded')
    monkeypatch.setattr(session.native, 'native_absent', lambda: True)
    monkeypatch.setattr(session.native, 'baseline', lambda: original)
    monkeypatch.setattr(session.native, 'check_slice', lambda *args: None)
    session.record_publications({'items': [publication]})
    saved = module.load(tmp_path / 'provider-nodes-recorded-slices.json')
    assert saved['items'] == [publication]
    assert session.owned_publications({'items': [publication]}) == [publication]
    replacement = json.loads(json.dumps(publication))
    replacement['metadata']['uid'] = 'replacement-uid'
    with pytest.raises(ValueError, match='UID/spec'):
        session.record_publications({'items': [replacement]})
    assert module.load(tmp_path / 'provider-nodes-recorded-slices.json')['items'] == [publication]


U6_RESTORE_ROOT = ROOT


def _u6_restore_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def u6_restore_provider(monkeypatch):
    native = _u6_restore_module(U6_RESTORE_ROOT / 'labs/04-isolation/provider/scripts/control_nodes.py',
                                'u6_restore_native')
    monkeypatch.setitem(sys.modules, 'control_nodes', native)
    previous_path = sys.path[:]
    try:
        return _u6_restore_module(U6_RESTORE_ROOT / 'labs/04-isolation/hami-dra/scripts/provider_nodes.py',
                                  'u6_restore_hami')
    finally:
        sys.path[:] = previous_path


class _U6RestoreCluster:
    """Replace kubectl and host RPCs while running the real restore controller."""

    def __init__(self, module, session, monkeypatch):
        self.module, self.session = module, session
        self.nodes = session.nodes
        self.events, self.host_observations = [], []
        self.host_failures, self.restore_failure = {}, None
        self.delete_failure = None
        self.host_present = {node: True for node in self.nodes}
        self.original = {'apiVersion': 'apps/v1', 'kind': 'DaemonSet', 'metadata': {
            'name': module.DS, 'namespace': module.NS, 'uid': 'native-uid', 'resourceVersion': '1'},
            'spec': {'selector': {'matchLabels': {'dra-driver-nvidia-gpu-component': 'kubelet-plugin'}},
                     'updateStrategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxUnavailable': 1}},
                     'template': {'metadata': {'labels': {'dra-driver-nvidia-gpu-component': 'kubelet-plugin'}},
                                  'spec': {'containers': [{'name': 'plugin', 'image': 'native-pinned'}],
                                           'affinity': {'nodeAffinity': {
                                               'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                                                   {'matchExpressions': [{'key': 'gpu', 'operator': 'Exists'}]}]}}}}}}}
        self.native = copy.deepcopy(self.original)
        self.native['spec'] = session.native.excluded_spec(self.original['spec'])
        labels = {module.BOOK: 'dra-book', module.INSTANCE: session.run_id,
                  'app.kubernetes.io/component': 'hami-provider'}
        self.providers = [{'apiVersion': 'apps/v1', 'kind': 'DaemonSet', 'metadata': {
            'name': node + '-hami-provider', 'namespace': module.NS, 'uid': node + '-provider-uid',
            'resourceVersion': '1', 'labels': dict(labels)},
            'spec': {'selector': {'matchLabels': dict(labels)}, 'template': {'metadata': {'labels': dict(labels)},
                     'spec': {'nodeName': node, 'containers': [{'name': 'plugin', 'image': 'hami-pinned'}]}}}}
            for node in self.nodes]
        self.device_class = {'apiVersion': 'resource.k8s.io/v1', 'kind': 'DeviceClass',
                             'metadata': {'name': 'owned-hami-class', 'uid': 'class-uid', 'resourceVersion': '1',
                                          'labels': dict(labels)}, 'spec': {'selectors': []}}
        self.objects = {(obj['kind'], obj['metadata']['name']): copy.deepcopy(obj)
                        for obj in self.providers + [self.device_class]}
        self.native_slices = [self.slice(node, 'gpu.nvidia.com', 'native') for node in self.nodes]
        self.slices = [self.slice(node, module.DRIVER, 'hami') for node in self.nodes]
        self.native_pods = []
        expected = {'apiVersion': 'v1', 'kind': 'List', 'items': self.providers + [self.device_class]}
        module.save(session.expected, expected)
        module.save(session.owned, expected)
        module.save(session.out / 'provider-nodes-ready-slices.json', {'items': self.slices})
        session.native.state = {'nodes': self.nodes, 'uid': 'native-uid', 'protected_pods': {},
                                'target_pods': {}, 'node_uids': {node: node + '-node-uid' for node in self.nodes}}
        module.save(session.native.backup_dir / 'backup.json', session.native.state)
        module.save(session.native.backup_dir / 'original-daemonset.json', self.original)
        module.save(session.native.backup_dir / 'original-slices.json', {'items': self.native_slices})
        for node in self.nodes:
            directory = session.native.backup_dir / node
            module.save(directory / 'original-daemonset.json', self.original)
            module.save(directory / 'original-slices.json', {'items': self.native_slices})
            module.save(directory / 'update-strategy.json', self.original['spec']['updateStrategy'])
            (directory / 'uid.txt').write_text('native-uid\n')
            module.save(directory / 'backup.json', {'daemonset': {'namespace': module.NS, 'name': module.DS,
                                                                  'uid': 'native-uid'}, 'target_node': node})
            for name in ('host-before.json', 'host-installed.json'):
                module.save(session.out / node / name, self.footprint(node))
        monkeypatch.setattr(session.kube, 'call', self.call)
        monkeypatch.setattr(session.kube, 'wait', self.wait)
        monkeypatch.setattr(session, 'host_start', self.host_start)
        monkeypatch.setattr(session, 'host', self.host)

    def slice(self, node, driver, prefix):
        return {'apiVersion': 'resource.k8s.io/v1', 'kind': 'ResourceSlice', 'metadata': {
            'name': prefix + '-' + node, 'uid': prefix + '-' + node + '-uid', 'resourceVersion': '1',
            'ownerReferences': [{'kind': 'Node', 'name': node, 'uid': node + '-node-uid'}]},
            'spec': {'driver': driver, 'nodeName': node, 'pool': {'name': node, 'generation': 1},
                     'devices': [{'name': 'gpu-0', 'attributes': {'uuid': {'string': 'GPU-' + node}}}]}}

    def plugin(self, node, native):
        return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {
            'name': ('native-' if native else 'hami-') + node, 'uid': ('native-' if native else 'hami-') + node + '-pod-uid',
            'namespace': self.module.NS, 'ownerReferences': [{'kind': 'DaemonSet', 'controller': True,
                'uid': 'native-uid' if native else node + '-provider-uid'}]},
            'spec': {'nodeName': node, 'containers': [{'name': 'plugin', 'image': 'pinned-plugin'}]},
            'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}

    def call(self, *args, json_result=False):
        if args[:2] == ('-n', 'gpu-operator'):
            args = args[2:]
        action = args[0]
        if action == 'get':
            kind = args[1]
            if kind == '-f':
                return {'items': list(copy.deepcopy(self.objects).values())}
            if kind == 'daemonset' and args[2] == self.module.DS:
                return copy.deepcopy(self.native)
            if kind == 'daemonsets':
                return {'items': [copy.deepcopy(self.native)] + [copy.deepcopy(obj) for obj in self.objects.values()
                                                                if obj['kind'] == 'DaemonSet']}
            if kind == 'resourceclaims':
                return {'items': []}
            if kind == 'resourceslices':
                return {'items': copy.deepcopy(self.slices)}
            if kind == 'node':
                return {'metadata': {'name': args[2], 'uid': args[2] + '-node-uid'}}
            if kind == 'pods':
                if 'app=nvidia-driver-daemonset' in args:
                    node = next(arg.split('=', 1)[1] for arg in args if str(arg).startswith('spec.nodeName='))
                    return {'items': [{'metadata': {'name': 'driver-' + node, 'uid': 'driver-' + node + '-uid'},
                                       'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}]}
                hami = [self.plugin(node, False) for node in self.nodes
                        if ('DaemonSet', node + '-hami-provider') in self.objects]
                return {'items': copy.deepcopy(hami if '-l' in args else hami + self.native_pods)}
            if kind == 'ResourceSlice':
                return copy.deepcopy(next((sl for sl in self.slices if sl['metadata']['name'] == args[2]), {}))
            return copy.deepcopy(self.objects.get((kind, args[2]), {}))
        if action == 'exec':
            if any(str(arg).startswith('--query-compute-apps=') for arg in args):
                return ''
            node = next(str(arg).removeprefix('driver-') for arg in args if str(arg).startswith('driver-dra-'))
            return 'GPU-' + node + ', NVIDIA A40, 46068, 0, 46068, 0, Default\n'
        if action == 'delete':
            name = args[2].rsplit('/', 1)[1]
            if name == self.delete_failure:
                raise RuntimeError('owned object deletion rejected')
            options = self.module.load(args[4])
            obj = next((obj for obj in self.objects.values() if obj['metadata']['name'] == name), None)
            if obj:
                assert options['preconditions'] == {'uid': obj['metadata']['uid'], 'resourceVersion': '1'}
                self.events.append(('delete', obj['kind'], name))
                del self.objects[obj['kind'], name]
            else:
                sl = next(sl for sl in self.slices if sl['metadata']['name'] == name)
                assert options['preconditions'] == {'uid': sl['metadata']['uid'], 'resourceVersion': '1'}
                self.events.append(('delete', 'ResourceSlice', name))
                self.slices.remove(sl)
            return ''
        if action == 'patch':
            if self.restore_failure == 'patch':
                raise RuntimeError('native restoration patch rejected')
            patch = self.module.load(args[args.index('--patch-file') + 1])
            assert patch[0] == {'op': 'test', 'path': '/metadata/uid', 'value': 'native-uid'}
            assert patch[1] == {'op': 'test', 'path': '/spec', 'value': self.native['spec']}
            self.native['spec'] = copy.deepcopy(patch[2]['value'])
            self.events.append(('native-patch', self.native['spec']['updateStrategy']['type']))
            self.native_pods = [self.plugin(node, True) for node in self.nodes]
            self.slices = copy.deepcopy(self.native_slices)
            if self.restore_failure == 'target-readiness':
                self.native_pods[0]['status']['conditions'][0]['status'] = 'False'
            elif self.restore_failure == 'native-slices':
                self.slices[0]['spec']['devices'][0]['attributes']['uuid']['string'] = 'GPU-foreign'
            elif self.native['spec']['updateStrategy']['type'] == 'RollingUpdate':
                if self.restore_failure == 'uid':
                    self.native['metadata']['uid'] = 'replaced-native-uid'
                elif self.restore_failure == 'spec':
                    self.native['spec']['template']['spec']['containers'][0]['image'] = 'foreign-plugin'
            return ''
        raise AssertionError('unexpected kubectl operation: ' + repr(args))

    def wait(self, check, message, *args):
        result = check()
        if not result:
            raise TimeoutError(message)
        return result

    def footprint(self, node):
        return {'/usr/local/vgpu': [{'path': '/usr/local/vgpu'}] if self.host_present[node] else [],
                '/var/lib/kubelet/plugins/' + self.module.DRIVER: [], '/tmp/vgpulock': [], '/var/run/cdi': []}

    def host_start(self, node):
        self.events.append(('host-start', node))
        self.host_observations.append({'node': node,
            'native_recorded': (self.session.out / 'native-restored.json').exists(),
            'daemonset': copy.deepcopy(self.native), 'pods': copy.deepcopy(self.native_pods),
            'slices': copy.deepcopy(self.slices),
            'hami_daemonsets': [obj for obj in self.objects.values() if obj['kind'] == 'DaemonSet']})

    def host(self, node, payload):
        if payload['operation'] == 'remove-hooks':
            self.events.append(('remove-hooks', node))
            if node in self.host_failures:
                raise RuntimeError(self.host_failures[node])
            self.host_present[node] = False
        return self.footprint(node)


def _u6_restore_session(tmp_path, module, monkeypatch, nodes=('dra-a40', 'dra-4090')):
    session = module.HamiNodes(tmp_path, 'provider-test', list(nodes))
    return session, _U6RestoreCluster(module, session, monkeypatch)


def test_multinode_restores_verified_native_provider_before_any_host_cleanup(tmp_path, monkeypatch, u6_restore_provider):
    # Moving host cleanup ahead of restoration would expose excluded native state here.
    module = u6_restore_provider
    session, cluster = _u6_restore_session(tmp_path, module, monkeypatch)
    session.uninstall()
    assert [row['node'] for row in cluster.host_observations] == ['dra-a40', 'dra-4090']
    for row in cluster.host_observations:
        assert row['native_recorded'], 'host cleanup ran before native restore was verified'
        assert row['daemonset'] == cluster.original
        assert not row['hami_daemonsets']
        assert {pod['spec']['nodeName'] for pod in row['pods']} == {'dra-a40', 'dra-4090'}
        assert all(pod['status']['conditions'][0]['status'] == 'True' for pod in row['pods'])
        assert {sl['spec']['driver'] for sl in row['slices']} == {'gpu.nvidia.com'}
    assert not cluster.objects
    assert module.load(tmp_path / 'provider-nodes-restored.json')['host_artifacts_absent']


def test_multinode_host_failure_preserves_native_restoration_and_cleans_remaining_node(tmp_path, monkeypatch, u6_restore_provider):
    # Aborting on the first host failure must neither strand native GPUs nor skip the next host.
    module = u6_restore_provider
    session, cluster = _u6_restore_session(tmp_path, module, monkeypatch)
    cluster.host_failures = {'dra-a40': 'unrecognized host artifact'}
    with pytest.raises(RuntimeError) as failure:
        session.uninstall()
    assert cluster.native == cluster.original, 'host failure prevented native restoration'
    assert (tmp_path / 'native-restored.json').exists()
    assert ('remove-hooks', 'dra-4090') in cluster.events
    assert not cluster.host_present['dra-4090']
    assert not cluster.objects
    assert 'restored' in str(failure.value).lower() and 'dra-a40' in str(failure.value)
    report = module.load(tmp_path / 'provider-nodes-cleanup.json')
    assert report['native_uid_spec_match'] and not report['host_artifacts_absent']
    assert report['failures'] == [{'stage': 'host', 'node': 'dra-a40', 'error': 'unrecognized host artifact'}]
    assert not (tmp_path / 'provider-nodes-restored.json').exists()
    cluster.host_failures = {}
    session.uninstall()
    assert not any(cluster.host_present.values())
    assert cluster.events.count(('remove-hooks', 'dra-4090')) == 1
    assert module.load(tmp_path / 'provider-nodes-cleanup.json')['failures'] == []
    assert module.load(tmp_path / 'provider-nodes-restored.json')['host_artifacts_absent']


def test_multinode_reports_all_host_cleanup_failures(tmp_path, monkeypatch, u6_restore_provider):
    # Raising at one failed cleanup would hide the other node's failure from the report.
    module = u6_restore_provider
    session, cluster = _u6_restore_session(tmp_path, module, monkeypatch)
    cluster.host_failures = {'dra-a40': 'A40 host changed', 'dra-4090': '4090 host changed'}
    with pytest.raises(RuntimeError) as failure:
        session.uninstall()
    assert cluster.native == cluster.original
    assert [event for event in cluster.events if event[0] == 'remove-hooks'] == [
        ('remove-hooks', 'dra-a40'), ('remove-hooks', 'dra-4090')]
    assert all(node in str(failure.value) and error in str(failure.value)
               for node, error in cluster.host_failures.items())
    report = module.load(tmp_path / 'provider-nodes-cleanup.json')
    assert report['failures'] == [{'stage': 'host', 'node': 'dra-a40', 'error': 'A40 host changed'},
                                  {'stage': 'host', 'node': 'dra-4090', 'error': '4090 host changed'}]


def test_multinode_owned_object_cleanup_failure_is_reported_without_skipping_hosts(tmp_path, monkeypatch, u6_restore_provider):
    # A failed DeviceClass/RBAC delete must leave native GPUs restored and still attempt all hosts.
    module = u6_restore_provider
    session, cluster = _u6_restore_session(tmp_path, module, monkeypatch)
    cluster.delete_failure = 'owned-hami-class'
    with pytest.raises(RuntimeError) as failure:
        session.uninstall()
    assert cluster.native == cluster.original
    assert not any(cluster.host_present.values())
    assert 'restored' in str(failure.value).lower() and 'owned-hami-class' in str(failure.value)
    report = module.load(tmp_path / 'provider-nodes-cleanup.json')
    assert report['native_uid_spec_match'] and report['host_artifacts_absent']
    assert report['failures'] == [{'stage': 'provider-object', 'kind': 'DeviceClass', 'name': 'owned-hami-class',
                                  'error': 'owned object deletion rejected'}]
    assert not (tmp_path / 'provider-nodes-restored.json').exists()


@pytest.mark.parametrize('name', ['host-before.json', 'host-installed.json'])
def test_multinode_missing_host_evidence_preserves_native_restoration(tmp_path, monkeypatch, u6_restore_provider, name):
    # Missing host ownership evidence must block only that host's cleanup, not native restoration.
    module = u6_restore_provider
    session, cluster = _u6_restore_session(tmp_path, module, monkeypatch)
    (tmp_path / 'dra-a40' / name).unlink()
    with pytest.raises((RuntimeError, ValueError)):
        session.uninstall()
    assert cluster.native == cluster.original
    assert cluster.host_present == {'dra-a40': True, 'dra-4090': False}
    report = module.load(tmp_path / 'provider-nodes-cleanup.json')
    assert len(report['failures']) == 1
    assert report['failures'][0]['stage'] == 'host' and report['failures'][0]['node'] == 'dra-a40'
    assert 'evidence missing' in report['failures'][0]['error']


@pytest.mark.parametrize('failure', ['patch', 'target-readiness', 'native-slices', 'uid', 'spec'])
def test_multinode_restore_failure_preserves_host_artifacts(tmp_path, monkeypatch, u6_restore_provider, failure):
    # Any failed native restore verification must gate destructive host cleanup.
    module = u6_restore_provider
    session, cluster = _u6_restore_session(tmp_path, module, monkeypatch)
    cluster.restore_failure = failure
    with pytest.raises((RuntimeError, ValueError, TimeoutError)):
        session.uninstall()
    assert cluster.host_observations == [], 'host cleanup began without a verified native restore'
    assert all(cluster.host_present.values())
    assert not (tmp_path / 'native-restored.json').exists()
    assert not (tmp_path / 'provider-nodes-restored.json').exists()


def _priority_module(relative):
    path = ROOT / 'labs/06-heterogeneous' / relative
    spec = importlib.util.spec_from_file_location('priority_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _priority_render():
    binary = ROOT / '.work/bin/kustomize'
    if not binary.is_file():
        pytest.skip('pinned .work/bin/kustomize is missing')
    result = subprocess.run([str(binary), 'build', '--load-restrictor', 'LoadRestrictionsNone',
                             str(ROOT / 'labs/06-heterogeneous/selection/native')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_native_smoke_runs_the_prioritized_case_with_existing_cases():
    # Omitting the new case from SMOKE must not leave its fallback unexercised.
    control = _priority_module('scripts/control.py')
    assert control.select_cases('native', '', True) == [
        'a40', 'rtx4090', 'any-gpu', 'any-gpu-pair', 'attribute', 'nonexistent', 'prefer-4090']


def test_native_render_exports_ordered_preferences_and_plain_exclusive_holder(tmp_path, monkeypatch):
    # A missing template, hidden node pin, or firstAvailable holder breaks the actual rendered boundary.
    control = _priority_module('scripts/control.py')
    kube = _PriorityKube(_priority_render())
    runner = control.Selection(tmp_path, 'priority-run')
    runner.kube = kube
    items = runner.render('native')
    by_name = {row['metadata']['name']: row for row in items}
    template = by_name.get('prefer-4090-gpu')
    assert template is not None, 'native rendering must include the existing preference template'
    request = template['spec']['spec']['devices']['requests'][0]
    assert [row['name'] for row in request['firstAvailable']] == ['rtx4090', 'a40']
    worker = by_name['native-prefer-4090']['spec']['template']['spec']
    assert worker['resourceClaims'] == [
        {'name': 'gpu', 'resourceClaimTemplateName': 'prefer-4090-gpu'}]
    assert not worker.get('nodeName') and not worker.get('nodeSelector') and not worker.get('affinity')
    holder = by_name['native-prefer-4090-holder']['spec']['template']['spec']
    assert holder['resourceClaims'] == [
        {'name': 'gpu', 'resourceClaimTemplateName': 'native-rtx4090-gpu'}]
    exclusive = by_name['native-rtx4090-gpu']['spec']['spec']['devices']['requests'][0]
    assert 'firstAvailable' not in exclusive
    assert exclusive['exactly']['allocationMode'] == 'ExactCount'
    assert exclusive['exactly']['count'] == 1
    assert exclusive['exactly']['selectors'][0]['cel']['expression'] == (
        'device.attributes["gpu.nvidia.com"].productName == "NVIDIA GeForce RTX 4090"')


def test_placements_preserve_the_allocated_prioritized_subrequest():
    # Dropping status.allocation.devices.results.request erases the priority decision.
    control = _priority_module('scripts/control.py')
    pods = {'items': [{'metadata': {'name': 'worker-pod', 'uid': 'worker-pod-uid',
                                   'ownerReferences': [{'kind': 'Job', 'name': 'native-prefer-4090'}]},
                       'spec': {'nodeName': 'dra-a40', 'resourceClaims': [
                           {'name': 'gpu', 'resourceClaimTemplateName': 'prefer-4090-gpu'}]},
                       'status': {'resourceClaimStatuses': [
                           {'name': 'gpu', 'resourceClaimName': 'worker-gpu'}]}}]}
    claims = {'items': [{'metadata': {'name': 'worker-gpu', 'uid': 'worker-claim-uid'},
                         'status': {'allocation': {'devices': {'results': [
                             {'request': 'gpu/a40', 'driver': 'gpu.nvidia.com',
                              'pool': 'dra-a40', 'device': 'a40-device'}]}}}}]}
    rows = control.placements(pods, claims)
    assert rows[0].get('request') == 'gpu/a40'
    assert rows[0]['node'] == 'dra-a40'


class _PriorityKube:
    """External Kubernetes boundary: real controller methods consume complete API fixtures."""
    def __init__(self, manifest, *, wrong_request=None, bad_holder_uuid=False, occupied=False):
        self.manifest = manifest
        self.templates, self.pods, self.claims, self.logs = {}, {}, {}, {}
        self.cm = None
        self.sequence = 0
        self.events = []
        self.wrong_request = wrong_request
        self.bad_holder_uuid = bad_holder_uuid
        self.slices = {'items': [
            {'apiVersion': 'resource.k8s.io/v1', 'kind': 'ResourceSlice',
             'metadata': {'name': node + '-slice'},
             'spec': {'driver': 'gpu.nvidia.com', 'pool': {'name': node}, 'nodeName': node,
                      'devices': [{'name': node + '-device', 'attributes': {
                          'uuid': {'string': node + '-uuid'}, 'productName': {'string': model}}}]}}
            for node, model in [('dra-a40', 'NVIDIA A40'), ('dra-4090', 'NVIDIA GeForce RTX 4090')]]}
        if occupied:
            self.claims['foreign-gpu'] = {
                'apiVersion': 'resource.k8s.io/v1', 'kind': 'ResourceClaim',
                'metadata': {'namespace': 'another-workload', 'name': 'foreign-gpu', 'uid': 'foreign-claim'},
                'status': {'allocation': {'devices': {'results': [
                    {'request': 'gpu', 'driver': 'gpu.nvidia.com', 'pool': 'dra-4090',
                     'device': 'dra-4090-device'}]}}}}

    def get(self, resource, *args):
        if resource == 'resourceslices':
            return copy.deepcopy(self.slices)
        if resource == 'deviceclass':
            return {'apiVersion': 'resource.k8s.io/v1', 'kind': 'DeviceClass',
                    'metadata': {'name': args[0]}, 'spec': {'selectors': []}}
        if resource == 'configmap':
            assert args[0] == 'selection-control'
            return copy.deepcopy(self.cm)
        if resource in ('pods', 'resourceclaims'):
            source = self.pods if resource == 'pods' else self.claims
            values = list(source.values())
            if '-A' not in args:
                values = [row for row in values if row['metadata'].get('namespace') == 'gpu-heterogeneous-lab']
            if '-l' in args:
                selector = args[args.index('-l') + 1]
                if 'component in (' in selector:
                    names = selector.split('component in (')[1].split(')')[0].split(',')
                    values = [row for row in values if row['metadata']['labels']['app.kubernetes.io/component'] in names]
                elif 'app.kubernetes.io/component=' in selector:
                    name = selector.split('app.kubernetes.io/component=')[1].split(',')[0]
                    values = [row for row in values if row['metadata']['labels']['app.kubernetes.io/component'] == name]
            return {'apiVersion': 'v1', 'kind': 'List', 'items': copy.deepcopy(values)}
        if resource == 'events':
            return {'apiVersion': 'v1', 'kind': 'EventList', 'items': []}
        raise AssertionError((resource, args))

    def call(self, *args, json_result=False):
        if args[0] == 'get':
            if args[1] == '--raw':
                return {'gitVersion': 'v1.37.0'}
            assert args[1] == 'events' and args[-1] == 'yaml'
            return 'apiVersion: v1\nkind: List\nitems: []\n'
        if args[0] == 'kustomize':
            return self.manifest
        if args[0] == 'create':
            return {'apiVersion': 'v1', 'kind': 'List', 'items': list(yaml.safe_load_all(self.manifest))}
        if args[0] == 'apply':
            for obj in json.loads(Path(args[-1]).read_text())['items']:
                if obj['kind'] == 'ResourceClaimTemplate':
                    self.templates[obj['metadata']['name']] = obj
                elif obj['kind'] == 'ConfigMap' and obj['metadata']['name'] == 'selection-control':
                    self.cm = copy.deepcopy(obj)
                    self.cm['metadata']['uid'] = 'control-uid'
                elif obj['kind'] == 'Job':
                    self._allocate(obj)
            return 'objects applied\n'
        if args[0] == 'logs':
            return self.logs[args[1]]
        if args[0] == 'patch':
            assert args[1:3] == ('configmap', 'selection-control')
            patch = json.loads(args[-1])
            self.cm['data']['control.json'] = next(row['value'] for row in patch
                                                   if row['path'] == '/data/control.json' and row['op'] == 'replace')
            releases = json.loads(self.cm['data']['control.json'])['release']
            for pod in self.pods.values():
                if releases.get(pod['_task_id']) and pod['status']['phase'] == 'Running':
                    job = pod['metadata']['ownerReferences'][0]['name']
                    self.events.append(('release', job, pod['_task_id']))
                    pod['status']['phase'] = 'Succeeded'
                    pod['status']['containerStatuses'][0]['state'] = {'terminated': {'exitCode': 0}}
            return 'configmap patched\n'
        if args[0] == 'wait':
            name = args[1].removeprefix('job/')
            assert any(pod['metadata']['ownerReferences'][0]['name'] == name and
                       pod['status']['phase'] == 'Succeeded' for pod in self.pods.values()), name
            return 'job condition met\n'
        if args[0] == 'delete':
            selector = args[args.index('-l') + 1]
            component = selector.split('app.kubernetes.io/component=')[-1] if 'app.kubernetes.io/component=' in selector else None
            if args[1] == 'jobs':
                self.pods = {name: row for name, row in self.pods.items()
                             if row['metadata']['labels']['app.kubernetes.io/component'] != component}
            elif args[1] == 'resourceclaims':
                self.claims = {name: row for name, row in self.claims.items()
                               if row['metadata'].get('namespace') != 'gpu-heterogeneous-lab' or
                               (component and row['metadata']['labels']['app.kubernetes.io/component'] != component)}
            else:
                raise AssertionError(args)
            self.events.append(('delete', args[1], component))
            return 'objects deleted\n'
        raise AssertionError(args)

    def _allocate(self, job):
        name = job['metadata']['name']
        assert all(row['metadata']['ownerReferences'][0]['name'] != name for row in self.pods.values())
        self.sequence += 1
        spec = copy.deepcopy(job['spec']['template']['spec'])
        template = self.templates[spec['resourceClaims'][0]['resourceClaimTemplateName']]
        request = template['spec']['spec']['devices']['requests'][0]
        held = any(result['pool'] == 'dra-4090' for claim in self.claims.values()
                   for result in claim['status']['allocation']['devices']['results'])
        if 'firstAvailable' in request:
            node, subrequest = ('dra-a40', 'gpu/a40') if held else ('dra-4090', 'gpu/rtx4090')
            if self.wrong_request:
                subrequest = self.wrong_request
        else:
            node, subrequest = 'dra-4090', 'gpu'
        spec['nodeName'] = node
        pod_name, claim_name = f'{name}-pod-{self.sequence}', f'{name}-claim-{self.sequence}'
        args = spec['containers'][0]['args']
        task_id = args[args.index('--task-id') + 1]
        pod_uid = f'pod-uid-{self.sequence}'
        self.pods[pod_name] = {
            'apiVersion': 'v1', 'kind': 'Pod',
            'metadata': {'name': pod_name, 'namespace': 'gpu-heterogeneous-lab', 'uid': pod_uid,
                         'labels': copy.deepcopy(job['spec']['template']['metadata']['labels']),
                         'ownerReferences': [{'apiVersion': 'batch/v1', 'kind': 'Job', 'name': name,
                                              'uid': name + '-uid', 'controller': True}]},
            'spec': spec, '_task_id': task_id,
            'status': {'phase': 'Running', 'resourceClaimStatuses': [
                {'name': 'gpu', 'resourceClaimName': claim_name}],
                'containerStatuses': [{'name': 'workload', 'state': {
                    'running': {'startedAt': '2026-10-12T00:00:00Z'}}, 'ready': True}]}}
        self.claims[claim_name] = {
            'apiVersion': 'resource.k8s.io/v1', 'kind': 'ResourceClaim',
            'metadata': {'name': claim_name, 'namespace': 'gpu-heterogeneous-lab',
                         'uid': f'claim-uid-{self.sequence}',
                         'labels': copy.deepcopy(template['spec']['metadata']['labels']),
                         'ownerReferences': [{'apiVersion': 'v1', 'kind': 'Pod', 'name': pod_name,
                                              'uid': pod_uid, 'controller': True}]},
            'spec': copy.deepcopy(template['spec']['spec']),
            'status': {'allocation': {'devices': {'results': [
                {'request': subrequest, 'driver': 'gpu.nvidia.com', 'pool': node,
                 'device': node + '-device'}]}}}}
        gpu_uuid = 'wrong-gpu-uuid' if self.bad_holder_uuid and name.endswith('-holder') else node + '-uuid'
        self.logs[pod_name] = '\n'.join(json.dumps(row) for row in [
            {'event': 'READY', 'pod_uid': pod_uid, 'gpu_uuid': gpu_uuid, 'task_id': task_id},
            {'event': 'CORRECT', 'correct': True, 'task_id': task_id},
            {'event': 'HOLD', 'task_id': task_id}]) + '\n'
        self.events.append(('allocate', name, node, subrequest, task_id, held))


def _priority_runner(tmp_path, monkeypatch, **kube_kwargs):
    control = _priority_module('scripts/control.py')
    runner = control.Selection(tmp_path, 'priority-run')
    kube = _PriorityKube(_priority_render(), **kube_kwargs)
    runner.kube = kube
    # Sampling is the other external process boundary; allocation/barrier/cleanup remain real.
    monkeypatch.setattr(runner, 'start_sampling', lambda directory: None)
    monkeypatch.setattr(runner, 'stop_sampling', lambda: None)
    items = runner.render('native')
    runner.apply(tmp_path, 'base', [obj for obj in items if obj['kind'] != 'Job'])
    real_wait = runner.wait
    monkeypatch.setattr(runner, 'wait', lambda check, message, timeout=300:
                        real_wait(check, message, timeout=0))
    return runner, kube, items


def test_preference_round_verifies_two_allocations_before_releasing_holder(tmp_path, monkeypatch):
    # Releasing the holder early, reusing a release key, or skipping fallback must break this run output.
    runner, kube, items = _priority_runner(tmp_path, monkeypatch)
    runner.case('native', 'prefer-4090', 1, True, items)
    trial = json.loads((tmp_path / 'cases/prefer-4090-r1/trial.json').read_text())
    assert trial['verdict'] == 'passed' and trial['claims_deleted']
    assert [(phase['phase'], phase['verdict'], phase['placements'][0]['node'],
             phase['placements'][0]['request']) for phase in trial.get('phases', [])] == [
        ('idle', 'passed', 'dra-4090', 'gpu/rtx4090'),
        ('rtx4090-held', 'passed', 'dra-a40', 'gpu/a40')]
    holder = trial['phases'][1]['holder_placements'][0]
    assert (holder['node'], holder['request']) == ('dra-4090', 'gpu')
    events = kube.events
    allocations = [event for event in events if event[0] == 'allocate']
    assert [(row[1], row[2], row[3], row[5]) for row in allocations] == [
        ('native-prefer-4090', 'dra-4090', 'gpu/rtx4090', False),
        ('native-prefer-4090-holder', 'dra-4090', 'gpu', False),
        ('native-prefer-4090', 'dra-a40', 'gpu/a40', True)]
    assert len({row[4] for row in allocations}) == 3
    fallback_release = next(index for index, event in enumerate(events)
                            if event[:2] == ('release', 'native-prefer-4090') and
                            event[2] == allocations[-1][4])
    holder_release = next(index for index, event in enumerate(events)
                          if event[:2] == ('release', 'native-prefer-4090-holder'))
    assert fallback_release < holder_release
    assert not kube.pods and not kube.claims


def test_preference_rejects_incorrect_subrequest_before_release(tmp_path, monkeypatch):
    # A node-only acceptance check would incorrectly release a workload with the wrong selected alternative.
    runner, kube, items = _priority_runner(tmp_path, monkeypatch, wrong_request='gpu/a40')
    with pytest.raises(ValueError, match='subrequest'):
        runner.case('native', 'prefer-4090', 1, True, items)
    trial = json.loads((tmp_path / 'cases/prefer-4090-r1/trial.json').read_text())
    assert trial['verdict'] == 'failed'
    assert not any(row[0] == 'release' for row in kube.events)
    assert len(kube.pods) == 1, 'failed evidence is retained for explicit manual cleanup'


def test_preference_does_not_launch_fallback_before_holder_claim_uuid_barrier(tmp_path, monkeypatch):
    # A holder log from another GPU must never authorize launching the fallback worker.
    runner, kube, items = _priority_runner(tmp_path, monkeypatch, bad_holder_uuid=True)
    with pytest.raises(ValueError, match='UUID'):
        runner.case('native', 'prefer-4090', 1, True, items)
    trial = json.loads((tmp_path / 'cases/prefer-4090-r1/trial.json').read_text())
    assert trial['verdict'] == 'failed'
    assert [row[1] for row in kube.events if row[0] == 'allocate'] == [
        'native-prefer-4090', 'native-prefer-4090-holder']
    assert not any(row[:2] == ('release', 'native-prefer-4090-holder') for row in kube.events)


def test_preference_idle_phase_rejects_an_existing_exclusive_allocation(tmp_path, monkeypatch):
    # An occupied RTX 4090 must not be mislabeled as the both-idle preference result.
    runner, kube, items = _priority_runner(tmp_path, monkeypatch, occupied=True)
    with pytest.raises(ValueError, match='idle'):
        runner.case('native', 'prefer-4090', 1, True, items)
    assert not any(row[0] == 'allocate' for row in kube.events)
    assert 'foreign-gpu' in kube.claims


def _priority_trial():
    return {'case': 'prefer-4090', 'part': 'native', 'round': 1,
            'verdict': 'passed', 'claims_deleted': True,
            'phases': [
                {'phase': 'idle', 'verdict': 'passed', 'claims_deleted': True,
                 'expected_node': 'dra-4090', 'expected_request': 'gpu/rtx4090',
                 'placements': [{'job': 'native-prefer-4090', 'node': 'dra-4090',
                                 'request': 'gpu/rtx4090', 'pod_uid': 'idle-pod', 'claim_uid': 'idle-claim'}]},
                {'phase': 'rtx4090-held', 'verdict': 'passed', 'claims_deleted': True,
                 'expected_node': 'dra-a40', 'expected_request': 'gpu/a40',
                 'holder_placements': [{'job': 'native-prefer-4090-holder', 'node': 'dra-4090',
                                        'request': 'gpu', 'pod_uid': 'holder-pod', 'claim_uid': 'holder-claim'}],
                 'placements': [{'job': 'native-prefer-4090', 'node': 'dra-a40',
                                 'request': 'gpu/a40', 'pod_uid': 'fallback-pod', 'claim_uid': 'fallback-claim'}]}]}


def test_live_analysis_reports_both_priority_phase_placements_and_verdicts(tmp_path):
    # Ignoring phase-only evidence loses the selected alternatives from the reader-facing summary.
    (tmp_path / 'trial.json').write_text(json.dumps(_priority_trial()))
    result = _priority_module('analysis/analyze.py').analyze_run(tmp_path)
    assert [(row['phase'], row['node'], row['request']) for row in result['placements']] == [
        ('idle', 'dra-4090', 'gpu/rtx4090'), ('rtx4090-held', 'dra-a40', 'gpu/a40')]
    assert [(row['phase'], row['verdict']) for row in result.get('priority_phases', [])] == [
        ('idle', 'passed'), ('rtx4090-held', 'passed')]
    assert result['trials'][0]['verdict'] == 'passed'


@pytest.mark.parametrize('damage', ['wrong-node', 'wrong-subrequest', 'missing-phase', 'missing-holder'])
def test_live_analysis_requires_both_priority_allocations_to_accept(tmp_path, damage):
    # A forged success flag cannot substitute for the recorded node plus selected subrequest and holder.
    trial = _priority_trial()
    if damage == 'wrong-node':
        trial['phases'][0]['placements'][0]['node'] = 'dra-a40'
    elif damage == 'wrong-subrequest':
        trial['phases'][1]['placements'][0]['request'] = 'gpu/rtx4090'
    elif damage == 'missing-phase':
        trial['phases'].pop()
    else:
        trial['phases'][1].pop('holder_placements')
    (tmp_path / 'trial.json').write_text(json.dumps(trial))
    row = _priority_module('analysis/analyze.py').analyze_run(tmp_path)['trials'][0]
    assert row['verdict'] == 'incomplete'
    assert row['errors']


def _priority_uid_analysis():
    path = ROOT / 'labs/06-heterogeneous/analysis/analyze.py'
    spec = importlib.util.spec_from_file_location('priority_uid_analysis', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _priority_uid_trial():
    return {'case': 'prefer-4090', 'part': 'native', 'round': 1,
            'verdict': 'passed', 'claims_deleted': True,
            'phases': [
                {'phase': 'idle', 'verdict': 'passed', 'claims_deleted': True,
                 'placements': [{'node': 'dra-4090', 'request': 'gpu/rtx4090',
                                 'pod_uid': 'idle-pod', 'claim_uid': 'idle-claim'}]},
                {'phase': 'rtx4090-held', 'verdict': 'passed', 'claims_deleted': True,
                 'holder_placements': [{'node': 'dra-4090', 'request': 'gpu',
                                        'pod_uid': 'holder-pod', 'claim_uid': 'holder-claim'}],
                 'placements': [{'node': 'dra-a40', 'request': 'gpu/a40',
                                 'pod_uid': 'fallback-pod', 'claim_uid': 'fallback-claim'}]}]}


def _priority_uid_summary(tmp_path, trial):
    (tmp_path / 'trial.json').write_text(json.dumps(trial))
    return _priority_uid_analysis().analyze_run(tmp_path)


@pytest.mark.parametrize('phase_index, placements_key', [
    (0, 'placements'), (1, 'placements'), (1, 'holder_placements')])
@pytest.mark.parametrize('uid_key', ['pod_uid', 'claim_uid'])
def test_priority_analysis_requires_recorded_worker_and_holder_identities(
        tmp_path, phase_index, placements_key, uid_key):
    # Accepting a node/subrequest without its Pod or Claim UID loses allocation identity evidence.
    trial = _priority_uid_trial()
    trial['phases'][phase_index][placements_key][0].pop(uid_key)
    result = _priority_uid_summary(tmp_path, trial)
    assert result['trials'][0]['verdict'] == 'incomplete'
    assert result['priority_phases'][phase_index]['verdict'] == 'incomplete'
    assert any('UID' in error for error in result['priority_phases'][phase_index]['errors'])
    assert result['priority_phases'][1 - phase_index]['verdict'] == 'passed'


@pytest.mark.parametrize('uid_key', ['pod_uid', 'claim_uid'])
def test_priority_analysis_rejects_empty_holder_identity(tmp_path, uid_key):
    # A present but empty UID must not pass the nonempty allocation identity requirement.
    trial = _priority_uid_trial()
    trial['phases'][1]['holder_placements'][0][uid_key] = ''
    result = _priority_uid_summary(tmp_path, trial)
    assert result['trials'][0]['verdict'] == 'incomplete'
    assert any('UID' in error for error in result['priority_phases'][1]['errors'])


@pytest.mark.parametrize('uid_key', ['pod_uid', 'claim_uid'])
def test_priority_analysis_requires_a_new_worker_identity_after_idle_cleanup(tmp_path, uid_key):
    # Reusing either idle worker UID disproves the recorded delete-and-recreate phase transition.
    trial = _priority_uid_trial()
    trial['phases'][1]['placements'][0][uid_key] = trial['phases'][0]['placements'][0][uid_key]
    result = _priority_uid_summary(tmp_path, trial)
    assert result['trials'][0]['verdict'] == 'incomplete'
    assert result['priority_phases'][0]['verdict'] == 'passed'
    assert result['priority_phases'][1]['verdict'] == 'incomplete'
    assert any('UID' in error for error in result['priority_phases'][1]['errors'])


@pytest.mark.parametrize('worker_phase', [0, 1])
@pytest.mark.parametrize('uid_key', ['pod_uid', 'claim_uid'])
def test_priority_analysis_requires_independent_holder_identity(tmp_path, worker_phase, uid_key):
    # A holder sharing a worker identity cannot prove a separate exclusive allocation.
    trial = _priority_uid_trial()
    trial['phases'][1]['holder_placements'][0][uid_key] = trial['phases'][worker_phase]['placements'][0][uid_key]
    result = _priority_uid_summary(tmp_path, trial)
    assert result['trials'][0]['verdict'] == 'incomplete'
    assert result['priority_phases'][0]['verdict'] == 'passed'
    assert result['priority_phases'][1]['verdict'] == 'incomplete'
    assert any('UID' in error for error in result['priority_phases'][1]['errors'])


def test_priority_identity_checks_preserve_partial_failure_errors(tmp_path):
    # Missing identities in a failed phase must add evidence errors without erasing its original failure.
    trial = _priority_uid_trial()
    trial.update(verdict='failed', error='CUDA barrier failed')
    phase = trial['phases'][1]
    phase.update(verdict='failed', error='holder allocation lost', errors=['saved phase error'], placements=[])
    phase['holder_placements'][0].pop('claim_uid')
    result = _priority_uid_summary(tmp_path, trial)
    failed = result['priority_phases'][1]
    assert result['trials'][0]['verdict'] == failed['verdict'] == 'failed'
    assert failed['error'] == 'holder allocation lost'
    assert 'saved phase error' in failed['errors'] and 'holder allocation lost' in failed['errors']
    assert 'CUDA barrier failed' in result['trials'][0]['errors']
    assert any('UID' in error for error in failed['errors'])
    assert result['priority_phases'][0]['verdict'] == 'passed'
    assert result['placements'][0]['pod_uid'] == 'idle-pod'


class _U6HelperCleanupCluster(_U6RestoreCluster):
    """Keep the real CLI and guarded DELETE logic; replace only cluster RPCs."""

    def __init__(self, module, session, monkeypatch):
        self.helper_pods, self.helper_failures, self.helper_generations = {}, {}, {}
        super().__init__(module, session, monkeypatch)

    def host_start(self, node):
        super().host_start(node)
        if node not in self.helper_pods:
            generation = self.helper_generations.get(node, 0) + 1
            self.helper_generations[node] = generation
            self.helper_pods[node] = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {
                'name': 'helper-' + node, 'namespace': self.module.NS,
                'uid': 'helper-' + node + '-uid-' + str(generation), 'resourceVersion': '1',
                'labels': {self.module.BOOK: 'dra-book', self.module.INSTANCE: self.session.run_id,
                           'app.kubernetes.io/component': 'hami-host-helper'}},
                'spec': {'nodeName': node, 'containers': [{'name': 'hami-host-helper',
                         'image': self.module.HOST_IMAGE, 'securityContext': {'privileged': True}}],
                         'volumes': [{'name': 'host-root', 'hostPath': {'path': '/'}}]}}
        pod = copy.deepcopy(self.helper_pods[node])
        self.session.helpers[node] = pod
        self.module.save(self.session.out / node / 'host-helper-created.json', pod)

    def call(self, *args, json_result=False):
        if args[0] == 'get' and args[1].lower() == 'pod':
            node = next((node for node, pod in self.helper_pods.items()
                         if pod['metadata']['name'] == args[2]), None)
            if node is not None:
                if self.helper_failures.get(node) == 'uid':
                    self.helper_pods[node]['metadata']['uid'] = 'replacement-helper-uid'
                return copy.deepcopy(self.helper_pods[node])
            return {}
        if args[0] == 'delete' and '/pods/' in args[2]:
            name = args[2].rsplit('/', 1)[1]
            node = next(node for node, pod in self.helper_pods.items() if pod['metadata']['name'] == name)
            pod = self.helper_pods[node]
            options = self.module.load(args[4])
            assert options['preconditions'] == {'uid': pod['metadata']['uid'], 'resourceVersion': '1'}
            if self.helper_failures.get(node) == 'delete':
                raise RuntimeError('helper DELETE rejected on ' + node)
            self.events.append(('delete-helper', node))
            if self.helper_failures.get(node) == 'wait':
                pod['metadata']['deletionTimestamp'] = '2026-10-12T00:00:00Z'
            else:
                del self.helper_pods[node]
            return ''
        return super().call(*args, json_result=json_result)


def _u6_cli_helper_session(tmp_path, monkeypatch, module):
    sessions, clusters = [], []
    original_class = module.HamiNodes

    def session_factory(out, run_id, nodes):
        session = original_class(out, run_id, nodes)
        if not clusters:
            clusters.append(_U6HelperCleanupCluster(module, session, monkeypatch))
        else:
            cluster = clusters[0]
            cluster.session = session
            monkeypatch.setattr(session.kube, 'call', cluster.call)
            monkeypatch.setattr(session.kube, 'wait', cluster.wait)
            monkeypatch.setattr(session, 'host_start', cluster.host_start)
            monkeypatch.setattr(session, 'host', cluster.host)
        sessions.append(session)
        return session

    monkeypatch.setattr(module, 'HamiNodes', session_factory)
    monkeypatch.setenv('I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER', '1')
    monkeypatch.setenv('OUT_DIR', str(tmp_path))
    monkeypatch.setenv('NODES', 'dra-a40,dra-4090')
    monkeypatch.setattr(sys, 'argv', ['provider_nodes.py', 'uninstall', 'provider-test'])
    # Seed one real session so failure injection exists before main performs the work.
    session_factory(tmp_path, 'provider-test', ['dra-4090', 'dra-a40'])
    return sessions, clusters[0]


@pytest.mark.parametrize('failure', ['delete', 'wait'])
def test_cli_helper_cleanup_failure_releases_lock_and_allows_same_run_retry(tmp_path, monkeypatch, u6_restore_provider, failure):
    # A helper DELETE/wait failure in finally must neither skip its peer nor retain the operation lock.
    module = u6_restore_provider
    sessions, cluster = _u6_cli_helper_session(tmp_path, monkeypatch, module)
    cluster.helper_failures = {'dra-4090': failure}
    with pytest.raises((RuntimeError, TimeoutError)) as error:
        module.main()
    assert cluster.native == cluster.original
    assert (tmp_path / 'native-restored.json').exists()
    assert not (tmp_path / '.provider-nodes-lock').exists(), 'helper cleanup stranded the CLI lock'
    assert set(cluster.helper_pods) == {'dra-4090'}, 'first helper failure skipped the second helper'
    assert set(sessions[-1].helpers) == {'dra-4090'}
    assert 'dra-4090' in str(error.value)
    cluster.helper_failures = {}
    module.main()
    assert not cluster.helper_pods and not sessions[-1].helpers
    assert not (tmp_path / '.provider-nodes-lock').exists()
    assert cluster.native == cluster.original


def test_cli_reports_failures_from_each_helper_cleanup(tmp_path, monkeypatch, u6_restore_provider):
    # Only reporting the first error hides an uncleared helper on the other node.
    module = u6_restore_provider
    sessions, cluster = _u6_cli_helper_session(tmp_path, monkeypatch, module)
    cluster.helper_failures = {'dra-4090': 'delete', 'dra-a40': 'wait'}
    with pytest.raises((RuntimeError, TimeoutError)) as error:
        module.main()
    assert ('delete-helper', 'dra-a40') in cluster.events
    assert set(sessions[-1].helpers) == {'dra-4090', 'dra-a40'}
    assert all(node in str(error.value) for node in ('dra-4090', 'dra-a40'))
    assert 'DELETE rejected' in str(error.value) and 'host helper remains' in str(error.value)
    assert not (tmp_path / '.provider-nodes-lock').exists()


def test_cli_replaced_helper_uid_is_preserved_while_other_helper_is_removed(tmp_path, monkeypatch, u6_restore_provider):
    # Best-effort cleanup must keep guarded DELETE checks and refuse a replacement helper UID.
    module = u6_restore_provider
    sessions, cluster = _u6_cli_helper_session(tmp_path, monkeypatch, module)
    cluster.helper_failures = {'dra-4090': 'uid'}
    with pytest.raises((RuntimeError, ValueError)) as error:
        module.main()
    assert set(cluster.helper_pods) == {'dra-4090'}
    assert cluster.helper_pods['dra-4090']['metadata']['uid'] == 'replacement-helper-uid'
    assert ('delete-helper', 'dra-4090') not in cluster.events
    assert set(sessions[-1].helpers) == {'dra-4090'}
    assert 'UID/spec changed' in str(error.value) and 'dra-4090' in str(error.value)
    assert not (tmp_path / '.provider-nodes-lock').exists()


def test_cli_helper_failure_preserves_post_restore_host_cleanup_report(tmp_path, monkeypatch, u6_restore_provider):
    # Helper teardown must retain the independently recorded host cleanup failure and release the lock.
    module = u6_restore_provider
    _, cluster = _u6_cli_helper_session(tmp_path, monkeypatch, module)
    cluster.host_failures = {'dra-4090': 'unknown host entry'}
    cluster.helper_failures = {'dra-4090': 'delete'}
    with pytest.raises(RuntimeError):
        module.main()
    report = module.load(tmp_path / 'provider-nodes-cleanup.json')
    assert report['native_uid_spec_match'] and not report['host_artifacts_absent']
    assert report['failures'] == [{'stage': 'host', 'node': 'dra-4090', 'error': 'unknown host entry'}]
    assert cluster.native == cluster.original
    assert not (tmp_path / '.provider-nodes-lock').exists()


@pytest.mark.parametrize('failure', ['delete', 'wait'])
def test_cli_failed_helper_teardown_never_leaves_success_marker(tmp_path, monkeypatch, u6_restore_provider, failure):
    # A failing mandatory helper teardown must invalidate the all-cleanup success marker.
    module = u6_restore_provider
    sessions, cluster = _u6_cli_helper_session(tmp_path, monkeypatch, module)
    cluster.helper_failures = {'dra-4090': failure}
    with pytest.raises(RuntimeError):
        module.main()
    assert not (tmp_path / 'provider-nodes-restored.json').exists(), 'failed CLI retained a successful restore marker'
    assert module.load(tmp_path / 'native-restored.json')['uid_spec_match']
    report = module.load(tmp_path / 'provider-nodes-cleanup.json')
    assert report['native_uid_spec_match'] and report['host_artifacts_absent']
    assert report['failures'] == []
    assert cluster.native == cluster.original
    assert not (tmp_path / '.provider-nodes-lock').exists()
    assert set(cluster.helper_pods) == {'dra-4090'}
    cluster.helper_failures = {}
    module.main()
    assert not cluster.helper_pods and not sessions[-1].helpers
    assert not (tmp_path / '.provider-nodes-lock').exists()
    complete = module.load(tmp_path / 'provider-nodes-restored.json')
    assert complete['native_uid_spec_match'] and complete['host_artifacts_absent']
