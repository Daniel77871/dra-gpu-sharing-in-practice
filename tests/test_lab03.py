import csv
import hashlib
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

LAB = ROOT / 'labs/03-oom'
DATA = ROOT / 'data/e1'
IMAGE = 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755'


def module(name):
    path = LAB / 'analysis' / (name + '.py')
    spec = importlib.util.spec_from_file_location('lab03_' + name, path)
    loaded = importlib.util.module_from_spec(spec)
    # control.py imports the neighboring standard-library analyzer.
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(loaded)
    finally:
        sys.path.pop(0)
    return loaded


def csv_rows(path):
    with path.open(newline='') as stream:
        return {row['trial']: row for row in csv.DictReader(stream)}


def test_golden_analysis_reproduces_all_original_numeric_columns_and_preserves_raw(tmp_path):
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (DATA / 'raw').rglob('*') if p.is_file()}
    command = [sys.executable, str(LAB / 'analysis/analyze.py'), str(DATA / 'raw'), '--output', str(tmp_path)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    original = csv_rows(DATA / 'summary-original.csv')
    computed = csv_rows(tmp_path / 'summary.csv')
    assert len(computed) == 6 and computed.keys() == original.keys()
    exact = ('b_tensor_mib_at_oom', 'hold_probe_oom', 'hold_probe_ok', 'compute_errors', 'a_exit_code', 'b_exit_code')
    rounded = ('allocation_recovery_seconds', 'hold_latency_ratio')
    for trial, row in original.items():
        assert computed[trial]['complete'] == 'True'
        for key in exact:
            assert computed[trial][key] == row[key], (trial, key)
        for key in rounded:
            assert (f"{float(computed[trial][key]):.3f}" if computed[trial][key] else '') == (
                f"{float(row[key]):.3f}" if row[key] else ''), (trial, key)
        # The unrounded p95 inputs also remain identical.
        for key in ('baseline_samples', 'baseline_latency_p95_ms', 'hold_samples', 'hold_latency_p95_ms'):
            assert computed[trial][key] == row[key], (trial, key)
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}


def test_manifests_preserve_pod_semantics_and_shared_claim():
    assert hashlib.sha256((LAB / 'workload/workload.py').read_bytes()).hexdigest() == '4c9fa52ed409a803eb418b629bf0ed5ee29ffec015065056c8866bb3eeb752dd'
    for path in (LAB / 'manifests').glob('*.yaml'):
        item = yaml.safe_load(path.read_text())
        if item['kind'] == 'Kustomization':
            generator = item['configMapGenerator'][0]
            assert generator['files'] == ['workload.py=../workload/workload.py']
            labels = generator['options']['labels']
            assert labels['app.kubernetes.io/part-of'] == 'dra-book'
            assert labels['app.kubernetes.io/component'] == 'workload-code'
            assert item['generatorOptions']['disableNameSuffixHash'] is True
            continue
        assert item['metadata']['labels']['app.kubernetes.io/part-of'] == 'dra-book'
        assert item['metadata']['labels']['app.kubernetes.io/component']
        if item['kind'] == 'ResourceClaim':
            assert item['metadata']['name'] == 'a40-shared'
            exactly = item['spec']['devices']['requests'][0]['exactly']
            assert exactly == {'deviceClassName': 'gpu.nvidia.com', 'allocationMode': 'ExactCount', 'count': 1,
                               'selectors': [{'cel': {'expression': 'device.attributes["gpu.nvidia.com"].productName == "NVIDIA A40"'}}]}
            assert 'config' not in item['spec']['devices']
        if item['kind'] != 'Job':
            continue
        assert item['spec']['backoffLimit'] == 0
        template = item['spec']['template']
        assert template['metadata']['labels'] == item['metadata']['labels']
        spec = template['spec']
        assert spec['restartPolicy'] == 'Never'
        assert spec['terminationGracePeriodSeconds'] == 15
        assert spec['automountServiceAccountToken'] is False
        assert spec['tolerations'] == [{'key': 'nvidia.com/gpu', 'operator': 'Exists', 'effect': 'NoSchedule'}]
        assert spec['resourceClaims'] == [{'name': 'gpu', 'resourceClaimName': 'a40-shared'}]
        container = spec['containers'][0]
        assert container['image'] == IMAGE
        assert container['command'] == ['python', '-u', '/opt/lab/workload.py']
        assert container['args'] == ['--role', 'survivor' if item['metadata']['name'] == 'steady-service' else 'pressure']
        env = {v['name']: v['value'] for v in container['env']}
        assert env == ({'PYTORCH_NO_CUDA_MEMORY_CACHING': '1', 'OMP_NUM_THREADS': '1', 'OOM_MODE': 'exit'}
                       if item['metadata']['name'] == 'greedy-batch' else {'PYTORCH_NO_CUDA_MEMORY_CACHING': '1', 'OMP_NUM_THREADS': '1'})
        assert container['resources'] == {'requests': {'cpu': '2', 'memory': '4Gi'}, 'limits': {'cpu': '2', 'memory': '4Gi'},
                                          'claims': [{'name': 'gpu', 'request': 'gpu'}]}
        assert spec['volumes'] == [{'name': 'code', 'configMap': {'name': 'gpu-oom-workload'}}]


def test_incomplete_ram_oom_and_missing_gpu_sampling_are_not_success(tmp_path):
    analyzer = module('analyze')
    trial = tmp_path / 'hold-r1'
    shutil.copytree(DATA / 'raw/hold-r1', trial)
    final = json.loads((trial / 'pressure-final.json').read_text())
    state = final['status']['containerStatuses'][0]['state']['terminated']
    state.update(exitCode=137, reason='OOMKilled')
    (trial / 'pressure-final.json').write_text(json.dumps(final))
    (trial / 'gpu.jsonl').unlink()
    row = analyzer.load_summaries(tmp_path)[0]
    assert not row['complete']
    assert row['b_exit_code'] == 137
    assert 'OOMKilled' in row['incomplete_reason']
    assert 'GPU 取樣' in row['incomplete_reason']


def test_recovery_uses_later_cri_or_log_marker_and_hold_window_excludes_release():
    analyzer = module('analyze')
    a = analyzer.rows(DATA / 'raw/hold-r1/a.jsonl')
    b = analyzer.rows(DATA / 'raw/hold-r1/b.jsonl')
    meta = json.loads((DATA / 'raw/hold-r1/trial.json').read_text())
    # CRI can truncate finishedAt to a second before B_EXIT.
    meta['b_finished_utc'] = '2026-10-06T06:37:48Z'
    row = analyzer.summarize(a, b, meta)
    exited = next(e for e in b if e['event'] == 'B_EXIT')
    first = min(analyzer.timestamp(e['utc']) for e in a if e['event'] == 'A_PROBE_OK'
                and analyzer.timestamp(e['utc']) >= analyzer.timestamp(exited['utc']))
    assert row['allocation_recovery_seconds'] == first - analyzer.timestamp(exited['utc'])
    assert row['hold_probe_ok'] == 0


def test_stream_converter_preserves_original_keys_and_rejects_invalid_measurements():
    convert = module('gpu_csv').convert
    row = convert([' GPU-fixture', ' NVIDIA A40', ' 46068', ' 44900', ' 64', ' 0', ' Default'], '2026-10-06T00:00:00Z')
    assert tuple(row) == ('utc', 'gpu_uuid', 'gpu_name', 'total_mib', 'used_mib', 'free_mib', 'utilization_pct', 'compute_mode')
    assert row['used_mib'] == '44900' and row['compute_mode'] == 'Default'
    with pytest.raises(ValueError):
        convert(['wrong', 'columns'])


def test_preflight_refuses_ambiguous_a40_and_foreign_objects():
    control = module('control')
    device = {'name': 'gpu-0', 'attributes': {'productName': {'string': 'NVIDIA A40'}, 'uuid': {'string': 'GPU-fixture'}}}
    slices = {'items': [{'spec': {'nodeName': 'dra-a40', 'driver': 'gpu.nvidia.com', 'pool': {'name': 'dra-a40'}, 'devices': [device]}}]}
    assert control.target(slices, {}, 'dra-a40')['device'] == 'gpu-0'
    slices['items'].append({'spec': {'nodeName': 'another-worker', 'driver': 'gpu.nvidia.com', 'pool': {'name': 'another-worker'}, 'devices': [device]}})
    with pytest.raises(ValueError, match='exactly one'):
        control.target(slices, {}, 'dra-a40')
    with pytest.raises(ValueError, match='foreign'):
        control.ownership({'items': [{'kind': 'ResourceClaim', 'metadata': {'name': 'a40-shared'}}]})
    with pytest.raises(ValueError, match='configuration'):
        control.target({'items': []}, {'spec': {'config': [{'opaque': {}}]}}, 'dra-a40')


def test_manifest_split_accepts_kubectl_create_json_stream_or_list(tmp_path):
    control = module('control')
    items = [yaml.safe_load(p.read_text()) for p in sorted((LAB / 'manifests').glob('0*.yaml'))]
    items.append({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'gpu-oom-workload'}})
    for name, serialized in [('stream', '\n'.join(json.dumps(item) for item in items)),
                             ('list', json.dumps({'kind': 'List', 'items': items}))]:
        path = tmp_path / (name + '.json')
        path.write_text(serialized)
        assert control.manifest_items(path) == items
        destination = tmp_path / name
        destination.mkdir()
        control.split_manifests(control.manifest_items(path), destination)
        base = json.loads((destination / 'base.json').read_text())['items']
        assert {item['kind'] for item in base} == {'Namespace', 'ConfigMap'}
        assert all(item['kind'] != 'Job' for item in base)
        assert (destination / 'steady-service-manifest.json').is_file()
        assert (destination / 'greedy-batch-manifest.json').is_file()


def test_book_table_and_full_yaml_match_shipped_lab():
    chapter = (ROOT / 'book/03-oom/README.md').read_text()
    assert module('analyze').book_table(module('analyze').load_summaries(DATA / 'raw')) in chapter
    blocks = []
    for part in chapter.split('```yaml\n')[1:]:
        blocks.extend(yaml.safe_load_all(part.split('```', 1)[0]))
    for path in (LAB / 'manifests').glob('*.yaml'):
        assert yaml.safe_load(path.read_text()) in blocks, path.name
    # Generated workload ConfigMaps are presented through the full Kustomization.
    kustomization = next(item for item in blocks if item and item.get('kind') == 'Kustomization')
    assert kustomization['configMapGenerator'][0]['files'] == ['workload.py=../workload/workload.py']
    assert sum(path.stat().st_size for path in DATA.rglob('*') if path.is_file()) < 2_000_000


def test_timeline_separates_vram_and_probes_with_direct_outcome_labels(tmp_path):
    from matplotlib.axes import Axes
    from matplotlib.colors import to_hex
    from tools.figures import style
    from tools.figures.ch03 import hold_timeline

    fig = hold_timeline(ROOT / 'data')
    try:
        style.save_svg(fig, tmp_path / 'timeline.svg')
        fig.canvas.draw()
        top = fig.axes[0]
        panels = fig.findobj(Axes)
        assert len(panels) == 2
        probes = next(panel for panel in panels if panel is not top)
        assert top.get_shared_x_axes().joined(top, probes)
        assert probes.get_position().y1 < top.get_position().y0
        assert top.get_position().y0 - probes.get_position().y1 < 0.12, 'the two panels stay close together'
        assert top.get_ylim()[0] == 0
        assert top.get_title('left') == '整張 GPU 已用 VRAM'
        assert any(text.get_text() == 'GiB' for text in top.texts)
        assert {'B 成長', 'B 保留', 'B 釋放後', 't = 0'} <= {text.get_text() for text in top.texts}
        analyze = module('analyze')
        gpu = analyze.rows(DATA / 'raw/hold-r1/gpu.jsonl')
        assert list(top.lines[0].get_ydata()) == pytest.approx([float(row['used_mib']) / 1024 for row in gpu])
        # Outcomes are event bars at their measured times, named directly on the left (no floating legend).
        assert probes.get_legend() is None
        assert [text.get_text() for text in probes.get_yticklabels()] == ['OOM', 'OK']
        a = analyze.rows(DATA / 'raw/hold-r1/a.jsonl')
        b = analyze.rows(DATA / 'raw/hold-r1/b.jsonl')
        start = analyze.timestamp(next(row['utc'] for row in b if row.get('event') == 'B_GROW'))
        series = {artist.get_label(): artist for artist in probes.collections}
        assert to_hex(series['OK'].get_edgecolor()[0]) == style.COLORS['ok']
        assert to_hex(series['OOM'].get_edgecolor()[0]) == style.COLORS['oom']
        for label, event in (('OK', 'A_PROBE_OK'), ('OOM', 'A_PROBE_OOM')):
            expected = [analyze.timestamp(row['utc']) - start for row in a if row.get('event') == event]
            assert sorted(series[label].get_offsets()[:, 0].tolist()) == pytest.approx(sorted(expected))
        assert len(series['OOM'].get_offsets()) == 30
    finally:
        style.plt.close(fig)


@pytest.fixture
def fake_cluster(tmp_path):
    lab = tmp_path / 'labs/03-oom'
    shutil.copytree(LAB, lab, ignore=shutil.ignore_patterns('out', '__pycache__'))
    shutil.copytree(ROOT / 'labs/common', tmp_path / 'labs/common', ignore=shutil.ignore_patterns('out', '__pycache__'))
    binary = tmp_path / 'bin'
    binary.mkdir()
    actions = tmp_path / 'actions.jsonl'
    fake = binary / 'kubectl'
    fake.write_text(f'#!{sys.executable}\n' + '''import json, os, sys, time
from pathlib import Path
args = [a for a in sys.argv[1:] if not a.startswith('--request-timeout=')]
with Path(os.environ['LAB03_ACTIONS']).open('a') as stream:
    stream.write(json.dumps(args)+'\\n')
if 'get' in args:
    kind = args[args.index('get')+1]
    output = args[args.index('-o')+1] if '-o' in args else ''
    if kind == 'namespace':
        if output == 'name':
            print('namespace/gpu-oom-lab')
        elif output.startswith('jsonpath'):
            print(os.environ.get('LAB03_OWNER', 'dra-book'))
        else:
            print(json.dumps({'metadata': {'name': 'gpu-oom-lab', 'labels': {'app.kubernetes.io/part-of': os.environ.get('LAB03_OWNER', 'dra-book')}}}))
    elif kind == 'pods':
        print(json.dumps({'items':[{'metadata':{'name':'driver-fixture'},'spec':{'nodeName':'dra-a40'},'status':{'phase':'Running','containerStatuses':[{'name':'nvidia-driver-ctr','ready':True}]}}]}))
    elif kind == 'pod':
        pod = {'metadata': {'name': 'steady-service-fixture', 'uid': 'replacement', 'labels': {'app.kubernetes.io/part-of': 'dra-book'}}}
        if os.environ.get('LAB03_WAIT_EVENT'):
            counter = Path(os.environ['LAB03_ACTIONS'] + '.pod-gets')
            count = int(counter.read_text()) + 1 if counter.exists() else 1
            counter.write_text(str(count))
            state = {'waiting': {'reason': 'ContainerCreating'}} if count == 1 else (
                {'terminated': {'exitCode': 1, 'reason': 'Error'}} if os.environ.get('LAB03_TERMINATED') else
                {'running': {'startedAt': '2026-10-11T00:00:00Z'}})
            pod['spec'] = {'containers': [{'name': 'workload'}]}
            pod['status'] = {'phase': 'Pending' if count == 1 else ('Failed' if 'terminated' in state else 'Running'),
                             'containerStatuses': [{'name': 'workload', 'ready': 'running' in state, 'restartCount': 0, 'state': state}]}
        print(json.dumps(pod))
    elif kind in ('configmaps', 'serviceaccounts', 'secrets'):
        items = []
        if kind == 'configmaps':
            items = [{'metadata': {'name': 'kube-root-ca.crt'}}]
        elif kind == 'serviceaccounts':
            items = [{'metadata': {'name': 'default'}}]
        elif os.environ.get('LAB03_FOREIGN_SECRET'):
            items = [{'metadata': {'name': 'foreign-secret'}}]
        print(json.dumps({'items': items}))
    else:
        raise SystemExit('unexpected fake get')
elif 'api-resources' in args:
    print('configmaps\\nserviceaccounts\\nsecrets\\nevents\\nevents.events.k8s.io')
elif 'logs' in args:
    count = int(Path(os.environ['LAB03_ACTIONS'] + '.pod-gets').read_text())
    if count == 1:
        print('Error from server (BadRequest): container "workload" is waiting to start: ContainerCreating', file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({'event': os.environ['LAB03_WAIT_EVENT'], 'utc': '2026-10-11T00:00:01Z'}))
elif 'exec' in args:
    assert args[args.index('-n')+1] == 'gpu-operator'
    assert args[args.index('-c')+1] == 'nvidia-driver-ctr'
    assert args[args.index('-lms')+1] == '500'
    assert 'compute_mode' in next(a for a in args if a.startswith('--query-gpu='))
    while True:
        print('GPU-fixture, NVIDIA A40, 46068, 1024, 44000, 0, Default', flush=True)
        time.sleep(0.05)
elif 'delete' not in args:
    raise SystemExit('unexpected fake command')
''')
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update(PATH=str(binary) + os.pathsep + env['PATH'], LAB03_ACTIONS=str(actions))
    return lab, actions, env


def test_runner_rejects_foreign_namespace_before_any_apply(fake_cluster):
    lab, actions, env = fake_cluster
    env['LAB03_OWNER'] = 'foreign'
    result = subprocess.run(['bash', str(lab / 'scripts/run.sh'), 'foreign'], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert 'refusing to adopt' in result.stderr
    assert all('apply' not in json.loads(line) for line in actions.read_text().splitlines())


@pytest.mark.parametrize('owner', ['dra-book', 'foreign'])
def test_cleanup_only_deletes_owned_component_selectors(fake_cluster, owner):
    lab, actions, env = fake_cluster
    env['LAB03_OWNER'] = owner
    result = subprocess.run(['bash', str(lab / 'scripts/cleanup.sh'), 'fixture'], env=env, capture_output=True, text=True, timeout=5)
    deletes = [json.loads(line) for line in actions.read_text().splitlines() if '"delete"' in line]
    if owner == 'foreign':
        assert result.returncode != 0 and deletes == []
    else:
        assert result.returncode == 0, result.stderr
        namespace = [args for args in deletes if 'namespace' in args]
        assert namespace == [['delete', 'namespace', 'gpu-oom-lab', '--ignore-not-found', '--wait=true', '--timeout=300s']]
        objects = [args for args in deletes if 'namespace' not in args]
        assert len(objects) == 4
        for args in objects:
            assert args[args.index('-n')+1] == 'gpu-oom-lab'
            selector = args[args.index('-l')+1]
            assert 'app.kubernetes.io/part-of=dra-book' in selector
            assert 'app.kubernetes.io/component' in selector
            assert 'namespace' not in args


def test_cleanup_keeps_owned_namespace_containing_foreign_objects(fake_cluster):
    lab, actions, env = fake_cluster
    env['LAB03_FOREIGN_SECRET'] = '1'
    result = subprocess.run(['bash', str(lab / 'scripts/cleanup.sh'), 'fixture'], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert 'foreign secrets/foreign-secret' in result.stderr
    deletes = [json.loads(line) for line in actions.read_text().splitlines() if '"delete"' in line]
    assert all('namespace' not in args for args in deletes)


@pytest.mark.parametrize('terminated', [False, True])
def test_event_wait_skips_containercreating_and_reads_started_or_terminated_logs(fake_cluster, terminated):
    lab, actions, env = fake_cluster
    env['LAB03_WAIT_EVENT'] = 'B_OOM' if terminated else 'A_READY'
    if terminated:
        env['LAB03_TERMINATED'] = '1'
    # Exercise the actual runner function against fake kubectl, without starting
    # workloads or waiting through the unrelated 60-second observation windows.
    source = (lab / 'scripts/run.sh').read_text()
    function = 'wait_event() {' + source.split('wait_event() {', 1)[1].split('\ncontrol() {', 1)[0]
    round_dir = lab / 'out/wait/hold-r1'
    round_dir.mkdir(parents=True)
    harness = round_dir / 'wait.sh'
    harness.write_text('set -euo pipefail\n' +
                       'source ' + shlex.quote(str(lab.parent / 'common/lib.sh')) + '\n' +
                       'HELPER=' + shlex.quote(str(lab / 'analysis/control.py')) + '\n' +
                       'ROUND_DIR=' + shlex.quote(str(round_dir)) + '\n' +
                       'export OUT_DIR="$ROUND_DIR"\n' + function + '\n' +
                       f'wait_event steady-service-fixture a {env["LAB03_WAIT_EVENT"]} 5\n')
    result = subprocess.run(['bash', str(harness)], env=env, capture_output=True, text=True, timeout=7)
    assert result.returncode == 0, result.stderr
    assert 'BadRequest' not in result.stderr
    calls = [json.loads(line) for line in actions.read_text().splitlines()]
    assert [args[args.index('get') + 1] for args in calls if 'get' in args] == ['pod', 'pod']
    assert len([args for args in calls if 'logs' in args]) == 1
    assert json.loads((round_dir / f'a-{env["LAB03_WAIT_EVENT"]}.json').read_text())['event'] == env['LAB03_WAIT_EVENT']


def test_collection_does_not_overwrite_a_recreated_pod(fake_cluster):
    lab, _, env = fake_cluster
    trial = lab / 'out/interrupted/hold-r1'
    trial.mkdir(parents=True)
    (trial / 'trial.json').write_text('{}')
    (trial / 'a-pod-name.txt').write_text('steady-service-fixture\n')
    (trial / 'a-created.json').write_text(json.dumps({'metadata': {'uid': 'original'}}))
    (trial / 'a.jsonl').write_text('saved evidence\n')
    result = subprocess.run(['bash', str(lab / 'scripts/collect.sh'), 'interrupted'], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0 and 'UID changed' in result.stderr
    assert (trial / 'a.jsonl').read_text() == 'saved evidence\n'


def test_sampler_streams_expected_schema_and_stops_its_own_process_group(fake_cluster):
    lab, _, env = fake_cluster
    output = lab / 'out/fixture/hold-r1/gpu.jsonl'
    env['OUT_DIR'] = str(output.parent)
    command = ['bash', str(lab / 'scripts/sample.sh')]
    try:
        result = subprocess.run(command + ['start', 'dra-a40', str(output)], env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        record = json.loads(Path(str(output) + '.pid').read_text())
        assert record['state'] == 'ready'
        sample = json.loads(output.read_text().splitlines()[0])
        assert sample['gpu_uuid'] == 'GPU-fixture' and sample['compute_mode'] == 'Default'
        assert sample['used_mib'] == '1024'
    finally:
        result = subprocess.run(command + ['stop', 'dra-a40', str(output)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not Path(str(output) + '.pid').exists()
