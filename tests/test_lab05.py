import copy
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
import yaml

from conftest import ROOT

LAB = ROOT / 'labs/05-performance'
DATA = ROOT / 'data/e3'


@pytest.fixture
def kustomize_binary():
    binary = ROOT / '.work/bin/kustomize'
    if not binary.is_file():
        pytest.skip('pinned .work/bin/kustomize is missing')
    return binary


@pytest.fixture
def fake_kubectl(tmp_path, monkeypatch, kustomize_binary):
    # Only the external kubectl boundary is replaced. Rendering uses the real
    # pinned engine; client dry-run converts that YAML to a Kubernetes List.
    binary = tmp_path / 'bin/kubectl'
    binary.parent.mkdir()
    binary.write_text(f'#!{sys.executable}\n' + f'''
import json
import subprocess
import sys
import yaml

args = sys.argv[1:]
assert args.pop(0) == '--request-timeout=30s', args
if args[0] == 'kustomize':
    assert len(args) == 3 and args[2] == '--load-restrictor=LoadRestrictionsNone', args
    result = subprocess.run([{str(kustomize_binary)!r}, 'build', '--load-restrictor',
                             'LoadRestrictionsNone', args[1]], capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    sys.exit(result.returncode)
assert args == ['create', '--dry-run=client', '--validate=false', '-f', '-', '-o', 'json'], args
print(json.dumps({{'apiVersion': 'v1', 'kind': 'List',
                  'items': list(yaml.safe_load_all(sys.stdin.read()))}}))
''')
    binary.chmod(0o755)
    monkeypatch.setenv('PATH', str(binary.parent) + os.pathsep + os.environ.get('PATH', ''))
    output = tmp_path / 'commands'
    output.mkdir()
    return output


def module(name, folder='analysis'):
    path = LAB / folder / (name + '.py')
    spec = importlib.util.spec_from_file_location('lab05_' + name, path)
    value = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    previous = {key: sys.modules.pop(key, None) for key in ('analyze', 'run')}
    try:
        spec.loader.exec_module(value)
    finally:
        sys.path.pop(0)
        for key, old in previous.items():
            sys.modules.pop(key, None)
            if old is not None:
                sys.modules[key] = old
    return value


def markdown_tables(text):
    return re.findall(r'(?m)^\|[^\n]*', text)


def test_golden_batches_recompute_all_timing_latency_utilization_metrics():
    analysis = module('analyze')
    frozen = analysis.read_json(DATA / 'benchmark-config.json')
    originals = {row['trial_id']: row for row in analysis.load_summary(DATA / 'summary.csv')}
    fixtures = sorted((DATA / 'raw').glob('*/cell.json'))
    assert len(fixtures) == 5
    assert sum(p.stat().st_size for p in DATA.rglob('*') if p.is_file()) < 4 * 1024 ** 2
    for fixture in fixtures:
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture.parent.rglob('*') if p.is_file()}
        row = analysis.batch(fixture.parent, frozen)
        original = originals[row['trial_id']]
        assert row['valid'] and row['config_sha256'] == original['config_sha256']
        assert row['telemetry_samples'] == original['telemetry_samples']
        for key in ('makespan_seconds', 'throughput_tasks_per_second', 'mean_task_latency',
                    'p50_task_latency', 'p95_task_latency', 'mean_gpu_utilization',
                    'p50_gpu_utilization', 'p95_gpu_utilization', 'peak_vram_mib'):
            assert f"{row[key]:.3f}" == f"{original[key]:.3f}", (row['trial_id'], key)
        assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}


def test_all_chapter_tables_equal_the_report_from_summary_only():
    analysis = module('analyze')
    rows = analysis.load_summary(DATA / 'summary.csv')
    assert len(rows) == 180 and sum(row['concurrency'] for row in rows) == 420
    text = analysis.table_text(rows)
    expected = markdown_tables((DATA / 'report-tables.md').read_text())
    assert markdown_tables(text) == expected
    chapter = (ROOT / 'book/05-performance/README.md').read_text()
    block = chapter.split('<!-- results:start -->')[1].split('<!-- results:end -->')[0]
    assert markdown_tables(block) == expected
    groups = analysis.aggregate(rows)
    conditions = json.loads((DATA / 'condition-statistics.json').read_text())
    for group, condition in zip(groups, conditions):
        assert group['repeats'] == 5
        assert (group['workload_id'], group['mode'], group['concurrency']) == (condition['workload'], condition['mode'], condition['concurrency'])
        assert f"{group['p95_task_latency']:.3f}" == f"{condition['task_latency']['p95']:.3f}"


def visible_text(fig):
    """Every text a reader sees: direct labels, panel titles, tick labels and legend entries."""
    found = [text.get_text() for text in fig.texts]
    for ax in fig.axes:
        found += [ax.get_title(loc='left')] + [text.get_text() for text in ax.texts]
        found += [tick.get_text() for tick in ax.get_xticklabels() + ax.get_yticklabels() if tick.get_visible()]
    for legend in fig.legends:
        found += [text.get_text() for text in legend.get_texts()]
    return set(found)


@pytest.mark.parametrize('name', ['speedup', 'tradeoff'])
def test_small_multiples_keep_shared_scale_and_visible_mode_and_count_legends(tmp_path, name):
    from matplotlib.lines import Line2D
    from tools.figures import ch05, style
    modes = [style.LABELS[mode] for mode in ch05.SHARED]
    outputs = []
    for repeat in range(2):
        fig = getattr(ch05, name)(ROOT / 'data')
        assert [ax.get_title(loc='left') for ax in fig.axes] == [
            ch05.WORKLOAD_NAMES[workload] for workload in ch05.analysis.WORKLOADS]
        assert all(fig.axes[0].get_shared_y_axes().joined(fig.axes[0], ax) for ax in fig.axes)
        assert all(ax.get_ylim() == fig.axes[0].get_ylim() for ax in fig.axes)
        fig.canvas.draw()
        visible = visible_text(fig)
        assert set(modes) <= visible  # each shared mode is named by a direct label on its line
        assert {'N=1', 'N=2', 'N=4'} <= visible  # N stays readable from the tick labels or point labels
        if name == 'speedup':
            baseline = [artist for artist in fig.artists if isinstance(artist, Line2D)]
            assert len(baseline) == 1 and '基準' in visible  # the 1.0 baseline is drawn once and labelled once
        path = tmp_path / f'{name}-{repeat}.svg'
        style.save_svg(fig, path)
        outputs.append(path.read_bytes())
        assert b'<text' not in outputs[-1]
        assert all(label.encode() in outputs[-1] for label in [*modes, 'N=1', 'N=2', 'N=4'])
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize('damage', ['count', 'clock', 'lateness', 'numeric', 'exit', 'telemetry'])
def test_incomplete_or_invalid_raw_data_never_becomes_a_valid_batch(tmp_path, damage):
    analysis = module('analyze')
    source = DATA / 'raw/b2-continuous-mps-n2'
    path = tmp_path / source.name
    shutil.copytree(source, path)
    if damage in ('count', 'clock', 'lateness', 'numeric'):
        log = path / 'task-0.log'
        rows = analysis.jsonl(log)
        end = next(row for row in rows if row['event'] == 'BENCHMARK_END')
        if damage == 'count':
            end['completed_gemms'] -= 1
        elif damage == 'clock':
            end['clock_domain'] = 'another-clock'
        elif damage == 'lateness':
            for row in rows:
                if 'start_ns' in row:
                    row['start_ns'] = end['release_ns'] + 20_000_001
        else:
            next(row for row in rows if row['event'] == 'VERIFIED')['correct'] = False
        log.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    elif damage == 'exit':
        pods = analysis.read_json(path / 'terminated-pods.json')
        pods[0]['status']['containerStatuses'][0]['state']['terminated']['exitCode'] = 137
        (path / 'terminated-pods.json').write_text(json.dumps(pods))
    else:
        samples = analysis.jsonl(path / 'gpu-metrics.jsonl')
        samples = samples[::10]
        (path / 'gpu-metrics.jsonl').write_text(''.join(json.dumps(s) + '\n' for s in samples))
        final = analysis.read_json(path / 'observer-final.json')
        final['samples'] = len(samples)
        (path / 'observer-final.json').write_text(json.dumps(final))
    with pytest.raises((ValueError, KeyError)):
        analysis.batch(path, analysis.read_json(DATA / 'benchmark-config.json'))


def overlay_objects(mode):
    base = [yaml.safe_load((LAB / 'manifests/base' / name).read_text()) for name in ('namespace.yaml', 'job.yaml')]
    claim = yaml.safe_load((LAB / 'manifests/overlays' / mode / 'claim.yaml').read_text())
    if mode in ('hami', 'sequential'):
        base[1]['spec']['template']['spec']['resourceClaims'] = [{'name': 'gpu', 'resourceClaimTemplateName': 'benchmark-task-gpu'}]
    base.append(claim)
    return base


@pytest.mark.parametrize('folder', sorted(path.parent.relative_to(LAB)
    for path in (LAB / 'manifests').rglob('kustomization.yaml')), ids=str)
def test_every_kustomization_renders_with_correct_claim_references(folder, kustomize_binary):
    result = subprocess.run([str(kustomize_binary), 'build', '--load-restrictor',
                             'LoadRestrictionsNone', str(LAB / folder)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    objects = {obj['kind']: obj for obj in yaml.safe_load_all(result.stdout)}
    mode = folder.name
    expected = {'Namespace', 'ConfigMap', 'Job'}
    if mode != 'base':
        expected.add('ResourceClaimTemplate' if mode in ('sequential', 'hami') else 'ResourceClaim')
    assert set(objects) == expected
    for kind, obj in objects.items():
        if kind == 'Namespace':
            assert obj['metadata']['name'] == 'gpu-performance-lab'
        else:
            assert obj['metadata']['namespace'] == 'gpu-performance-lab'
    code = objects['ConfigMap']
    assert code['metadata']['name'] == 'performance-code'
    assert code['data']['worker.py'].encode() == (LAB / 'workload/worker.py').read_bytes()
    job = objects['Job']
    assert job['metadata']['name'] == 'benchmark-lead'
    assert job['spec']['backoffLimit'] == 0
    spec = job['spec']['template']['spec']
    assert spec['restartPolicy'] == 'Never'
    assert len(spec['resourceClaims']) == 1
    reference = spec['resourceClaims'][0]
    assert reference['name'] == 'gpu'
    if mode in ('sequential', 'hami'):
        # Kustomize preserves the null removal marker; client JSON may omit it.
        assert reference.get('resourceClaimName') is None
        assert reference['resourceClaimTemplateName'] == 'benchmark-task-gpu'
    else:
        assert reference == {'name': 'gpu', 'resourceClaimName': 'performance-gpu'}
    if mode == 'base':
        return
    claim = objects['ResourceClaimTemplate' if mode in ('sequential', 'hami') else 'ResourceClaim']
    assert claim['metadata']['name'] == ('benchmark-task-gpu' if mode in ('sequential', 'hami') else 'performance-gpu')
    devices = claim['spec']['spec']['devices'] if mode in ('sequential', 'hami') else claim['spec']['devices']
    request = devices['requests'][0]['exactly']
    assert request['allocationMode'] == 'ExactCount' and request['count'] == 1
    if mode == 'hami':
        assert request['deviceClassName'] == 'hami-core-gpu.project-hami.io'
        assert request['capacity']['requests'] == {'memory': '8Gi', 'cores': '100'}
    else:
        assert request['deviceClassName'] == 'gpu.nvidia.com'


@pytest.mark.parametrize('mode', ['sequential', 'hami'])
@pytest.mark.parametrize('n', [2, 4])
def test_runner_rendering_prepares_per_task_jobs_from_real_kustomize(fake_kubectl, mode, n):
    runner = module('run')
    kube = runner.Kube(fake_kubectl)
    options = runner.selection({'HAMI': '1'})
    run = {'nonce': 'a' * 32, 'options': options}
    frozen = runner.frozen_config(options)
    cell = {'mode': mode, 'concurrency': n, 'workload_id': 'bursty-aligned'}
    objects, jobs = runner.prepare(runner.rendered(kube, mode), cell, frozen, run)
    assert [job['metadata']['name'] for job in jobs] == ['benchmark-lead', 'benchmark-partner-1'] + (
        ['benchmark-partner-2', 'benchmark-partner-3'] if n == 4 else [])
    claim = next(obj for obj in objects if obj['kind'] == 'ResourceClaimTemplate')
    assert claim['metadata']['name'] == 'benchmark-task-gpu'
    if mode == 'hami':
        request = claim['spec']['spec']['devices']['requests'][0]['exactly']
        assert request['capacity']['requests'] == {'memory': '8Gi', 'cores': '50' if n == 2 else '25'}
    for i, job in enumerate(jobs):
        spec = job['spec']['template']['spec']
        assert len(spec['resourceClaims']) == 1
        reference = spec['resourceClaims'][0]
        assert reference['name'] == 'gpu' and reference.get('resourceClaimName') is None
        assert reference['resourceClaimTemplateName'] == 'benchmark-task-gpu'
        c = spec['containers'][0]
        cfg = json.loads(c['args'][1])
        assert cfg['task_id'] == str(i) and cfg['concurrency'] == n
        env = {entry['name']: entry.get('value') for entry in c['env']}
        assert env['OMP_NUM_THREADS'] == env['MKL_NUM_THREADS'] == '1'
        assert env['GPU_UUID'] == 'GPU-29ffee3e-bee4-e341-917a-fe7e43e977af'
        assert env['EXPERIMENT_RUN'] == 'a' * 32
        owner = {entry['name']: entry.get('value') for entry in spec['initContainers'][0]['env']}
        assert owner['TASK_ID'] == str(i) and owner['EXPERIMENT_RUN'] == 'a' * 32
    commands = [json.loads(line) for line in (fake_kubectl / 'commands.jsonl').read_text().splitlines()]
    assert len(commands) == 2 and all(command['exit_code'] == 0 for command in commands)


def test_runner_stops_on_render_failure_before_client_conversion(fake_kubectl, tmp_path, monkeypatch):
    runner = module('run')
    lab = tmp_path / 'broken-lab'
    shutil.copytree(LAB / 'manifests', lab / 'manifests')
    shutil.copytree(LAB / 'workload', lab / 'workload')
    patch = lab / 'manifests/overlays/sequential/job-patch.yaml'
    value = yaml.safe_load(patch.read_text())
    value['metadata'].pop('namespace', None)
    patch.write_text(yaml.safe_dump(value))
    monkeypatch.setattr(runner, 'LAB', lab)
    with pytest.raises(RuntimeError, match='no resource matches strategic merge patch'):
        runner.rendered(runner.Kube(fake_kubectl), 'sequential')
    commands = [json.loads(line) for line in (fake_kubectl / 'commands.jsonl').read_text().splitlines()]
    assert len(commands) == 1 and commands[0]['exit_code'] != 0


def test_manifests_preserve_workload_and_allocation_semantics():
    assert hashlib.sha256((LAB / 'workload/worker.py').read_bytes()).hexdigest() == 'dfbef9de5ef7746ccbecf1c16af4f138be2ddbfe1bcea83775e79d568696bf0f'
    base = yaml.safe_load((LAB / 'manifests/base/kustomization.yaml').read_text())
    generator = base['configMapGenerator'][0]
    assert generator['files'] == ['worker.py=../../workload/worker.py', 'policy.py=../../workload/policy.py', 'control.py=../../workload/control.py']
    assert base['generatorOptions']['disableNameSuffixHash'] is True
    assert generator['options']['labels']['app.kubernetes.io/part-of'] == 'dra-book'
    runner = module('run')
    options = runner.selection({})
    run = {'nonce': 'a' * 32, 'options': options}
    frozen = runner.frozen_config(options)
    for mode in ('exclusive', 'sequential', 'timeslicing', 'mps', 'hami'):
        for n in ((1,) if mode == 'exclusive' else (2, 4) if mode == 'sequential' else (1, 2, 4)):
            cell = {'mode': mode, 'concurrency': n, 'workload_id': 'bursty-staggered'}
            objects, jobs = runner.prepare(overlay_objects(mode), cell, frozen, run)
            claim = next(obj for obj in objects if obj['kind'] in ('ResourceClaim', 'ResourceClaimTemplate'))
            assert claim['metadata']['labels']['app.kubernetes.io/part-of'] == 'dra-book'
            devices = claim['spec']['spec']['devices'] if claim['kind'] == 'ResourceClaimTemplate' else claim['spec']['devices']
            req = devices['requests'][0]['exactly']
            assert req['count'] == 1 and req['allocationMode'] == 'ExactCount'
            assert frozen['gpu_uuid'] in req['selectors'][0]['cel']['expression']
            if mode == 'hami':
                assert claim['kind'] == 'ResourceClaimTemplate'
                assert req['capacity']['requests'] == {'memory': '8Gi', 'cores': str(100 // n)}
            if mode in ('exclusive', 'sequential', 'hami'):
                assert 'config' not in devices
            if mode == 'timeslicing':
                assert devices['config'][0]['opaque']['parameters']['sharing'] == {'strategy': 'TimeSlicing'}
            if mode == 'mps':
                assert devices['config'][0]['opaque']['parameters']['sharing'] == {'strategy': 'MPS', 'mpsConfig': {
                    'defaultActiveThreadPercentage': 100, 'defaultPinnedDeviceMemoryLimit': '8Gi', 'multiUser': False}}
            for i, job in enumerate(jobs):
                assert job['spec']['backoffLimit'] == 0
                spec = job['spec']['template']['spec']
                assert spec['restartPolicy'] == 'Never' and spec['terminationGracePeriodSeconds'] == 120
                assert spec['automountServiceAccountToken'] is False
                assert spec['nodeSelector'] == {'kubernetes.io/hostname': 'dra-a40'}
                c = spec['containers'][0]
                assert len(spec['containers']) == 1
                assert c['image'].endswith('@' + runner.IMAGE_DIGEST) and ':2.5.1-cuda12.4-cudnn9-runtime@' in c['image']
                assert c['command'] == ['python', '-u', '/opt/lab/worker.py']
                assert c['resources'] == {'requests': {'cpu': '1', 'memory': '2Gi'}, 'limits': {'cpu': '1', 'memory': '2Gi'}, 'claims': [{'name': 'gpu', 'request': 'gpu'}]}
                env = {e['name']: e.get('value') for e in c['env']}
                assert env['OMP_NUM_THREADS'] == env['MKL_NUM_THREADS'] == '1'
                assert 'PYTORCH_NO_CUDA_MEMORY_CACHING' not in env
                assert c['securityContext'] == {'runAsUser': 0, 'runAsGroup': 0}
                cfg = json.loads(c['args'][1])
                assert cfg['shape'] == 8192 and cfg['seed'] == 20261007 and cfg['iterations'] == 367
                assert cfg['prefix_sleep_ns'] + cfg['final_sleep_ns'] == cfg['sleep_ns'] == 73705924
                assert cfg['task_id'] == str(i)
                assert spec['resourceClaims'][0] == ({'name': 'gpu', 'resourceClaimTemplateName': 'benchmark-task-gpu'} if mode in ('hami', 'sequential') else {'name': 'gpu', 'resourceClaimName': 'performance-gpu'})


def test_smoke_matrix_and_wait_budget_keep_the_original_duty_cycle():
    runner = module('run')
    options = runner.selection({'SMOKE': '1'})
    cells = runner.matrix(options)
    assert {(c['mode'], c['concurrency']) for c in cells} == {('exclusive', 1), ('sequential', 2), ('timeslicing', 2), ('mps', 2)}
    assert len(cells) == 4 and {c['workload_id'] for c in cells} == {'bursty-aligned'}
    full = runner.frozen_config(runner.selection({}))
    smoke = runner.frozen_config(options)
    base = full['workloads']['bursty-aligned']
    short = smoke['workloads']['bursty-aligned']
    assert short['iterations'] == 37 and short['sleep_ns'] == base['sleep_ns']
    # The TOTAL planned wait and compute counts shrink together, exactly.
    assert short['iterations'] * short['sleep_ns'] / (base['iterations'] * base['sleep_ns']) == short['iterations'] / base['iterations']
    all_options = runner.selection({'HAMI': '1'})
    assert len(runner.matrix(all_options)) == 180
    assert len(runner.matrix(runner.selection({}))) == 135


def test_controller_wont_send_next_phase_before_all_tasks_reach_the_barrier(monkeypatch):
    runner = module('run')
    pods = [({'task_id': str(i)}, {'metadata': {'name': f'worker-{i}', 'uid': str(i)}}) for i in range(2)]
    class Fake:
        def get(self, resource, name, *args):
            return {'metadata': {'uid': name[-1]}, 'status': {'phase': 'Running'}}
    controls = {'worker-0': {'end.json': {'end_ns': 1}}, 'worker-1': {}}
    monkeypatch.setattr(runner, 'worker_read', lambda kube, name: controls[name])
    assert runner.barrier(Fake(), pods, 'end.json') is None
    controls['worker-1'] = {'end.json': {'end_ns': 2}}
    assert set(runner.barrier(Fake(), pods, 'end.json')) == {'0', '1'}
    controls['worker-1']['error.json'] = {'error': 'CUDA failure'}
    with pytest.raises(ValueError, match='worker failed'):
        runner.barrier(Fake(), pods, 'end.json')


def test_cleanup_rejects_replacement_and_delete_has_uid_precondition():
    runner = module('run')
    run = {'nonce': 'a' * 32}
    obj = {'kind': 'Job', 'metadata': {'name': 'benchmark-lead', 'uid': 'original',
           'labels': {'app.kubernetes.io/part-of': 'dra-book', 'dra-book-run': run['nonce']}}, 'spec': {'backoffLimit': 0}}
    class Fake:
        def __init__(self, replacement):
            self.replacement, self.calls = replacement, []
        def get(self, *args):
            return self.replacement
        def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
    changed = copy.deepcopy(obj)
    changed['metadata']['uid'] = 'replacement'
    fake = Fake(changed)
    with pytest.raises(ValueError, match='UID/spec changed'):
        runner.delete_owned(fake, obj, run)
    assert not fake.calls
    fake = Fake(obj)
    runner.delete_owned(fake, obj, run)
    args, kwargs = fake.calls[0]
    assert args[:2] == ('delete', '--raw')
    assert json.loads(kwargs['stdin'])['preconditions'] == {'uid': 'original'}


@pytest.fixture
def mps_listing():
    workers = [{'task_id': str(i), 'pid': 1, 'uid': 0, 'pod_name': name,
                'pod_uid': 'uid-' + str(i), 'run_id': 'a' * 32}
               for i, name in enumerate(('benchmark-lead-j5pdv', 'benchmark-partner-1-jr2ns'))]
    identities = [{'host_pid': pid, 'namespace_pids': [pid, 1], 'pid_namespace': f'pid:[{100 + i}]',
                   'uid': 0, 'command': ['python', '-u', '/opt/lab/worker.py'],
                   'env': {'POD_NAME': worker['pod_name'], 'POD_UID': worker['pod_uid'],
                           'EXPERIMENT_RUN': worker['run_id']}}
                  for i, (pid, worker) in enumerate(zip((248633, 248725), workers))]
    class Fake:
        def __init__(self):
            self.mode = 'Exclusive_Process'
            self.raw = '248675, nvidia-cuda-mps-server, 30\n248633, python, 794\n248725, python, 794\n'
            self.identities, self.calls = copy.deepcopy(identities), []
        def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            if '--query-gpu=compute_mode' in args:
                return self.mode + '\n'
            if '--query-compute-apps=pid,process_name,used_memory' in args:
                return self.raw
            assert args[-4] == 'processes'
            assert json.loads(args[-1]) == [248633, 248725]
            return json.dumps(self.identities)
    return workers, Fake()


def test_mps_accepts_server_and_two_batch_clients_and_records_full_listing(tmp_path, mps_listing):
    runner = module('run')
    workers, fake = mps_listing
    run = {'nonce': 'a' * 32, 'options': {'gpu_uuid': 'target-gpu'}}
    runner.mps_runtime(fake, 'driver-pod', run, workers, tmp_path)
    evidence = json.loads((tmp_path / 'mps-runtime.json').read_text())
    assert evidence['compute_mode'] == 'Exclusive_Process'
    assert evidence['processes_raw'] == fake.raw
    assert evidence['processes'] == [
        {'pid': 248675, 'process_name': 'nvidia-cuda-mps-server', 'used_memory_mib': '30'},
        {'pid': 248633, 'process_name': 'python', 'used_memory_mib': '794'},
        {'pid': 248725, 'process_name': 'python', 'used_memory_mib': '794'}]
    assert evidence['client_identities'] == fake.identities
    assert evidence['expected_workers'] == workers


@pytest.mark.parametrize('damage', ['mode', 'server', 'pod_uid', 'run', 'pid', 'command'])
def test_mps_rejects_missing_server_or_foreign_clients_even_when_named_python(tmp_path, mps_listing, damage):
    runner = module('run')
    workers, fake = mps_listing
    if damage == 'mode':
        fake.mode = 'Default'
    elif damage == 'server':
        fake.raw = '\n'.join(fake.raw.splitlines()[1:]) + '\n'
    elif damage == 'pod_uid':
        fake.identities[1]['env']['POD_UID'] = 'another-pod'
    elif damage == 'run':
        fake.identities[1]['env']['EXPERIMENT_RUN'] = 'another-run'
    elif damage == 'pid':
        fake.identities[1]['namespace_pids'][-1] = 42
    else:
        fake.identities[1]['command'] = ['python', '/opt/other.py']
    run = {'nonce': 'a' * 32, 'options': {'gpu_uuid': 'target-gpu'}}
    with pytest.raises(ValueError, match='MPS'):
        runner.mps_runtime(fake, 'driver-pod', run, workers, tmp_path)
    assert json.loads((tmp_path / 'mps-runtime.json').read_text())['processes_raw'] == fake.raw


def test_host_process_identity_maps_namespace_pid_without_recording_other_environment(tmp_path):
    observer = module('observer')
    process = tmp_path / '248633'
    (process / 'ns').mkdir(parents=True)
    (process / 'ns/pid').symlink_to('pid:[4026533424]')
    (process / 'status').write_text('Name:\tpython\nUid:\t0\t0\t0\t0\nNSpid:\t248633\t1\n')
    (process / 'cmdline').write_bytes(b'python\0-u\0/opt/lab/worker.py\0')
    (process / 'environ').write_bytes(b'POD_NAME=benchmark-lead\0POD_UID=worker-uid\0EXPERIMENT_RUN=nonce\0UNRELATED=private\0')
    identity = observer.process_identity(248633, tmp_path)
    assert identity['host_pid'] == 248633 and identity['namespace_pids'] == [248633, 1]
    assert identity['pid_namespace'] == 'pid:[4026533424]'
    assert identity['uid'] == 0 and identity['command'] == ['python', '-u', '/opt/lab/worker.py']
    assert identity['env'] == {'POD_NAME': 'benchmark-lead', 'POD_UID': 'worker-uid', 'EXPERIMENT_RUN': 'nonce'}
    assert 'UNRELATED' not in json.dumps(identity)


def test_execute_clients_checks_mps_identities_before_start(tmp_path, monkeypatch):
    runner = module('run')
    cfg = {'task_id': '0'}
    pod = {'metadata': {'name': 'benchmark-lead', 'uid': 'worker-uid'}, 'spec': {'nodeName': 'dra-a40',
           'containers': [{'image': 'image@' + runner.IMAGE_DIGEST}]}}
    job = {'metadata': {'name': 'benchmark-lead'}, 'spec': {'template': {'spec': {
           'containers': [{'args': ['--config', json.dumps(cfg)]}]}}}}
    ready = {'0': {'ready.json': {'pid': 1, 'uid': 0, 'config': cfg,
             'config_sha256': runner.analyze.digest(cfg), 'clock_domain': 'host-clock',
             'gpu_uuid': 'target-gpu', 'env': {'CUDA_MPS_PIPE_DIRECTORY': '/mps'}}}}
    stopped = []
    class FakeObserver:
        process = type('Process', (), {'poll': lambda self: None})()
        def __init__(self, *args):
            pass
        def ready(self):
            return {'clock_domain': 'host-clock'}
        def stop(self):
            stopped.append(True)
    class FakeKube:
        def apply(self, obj):
            assert obj is job
    monkeypatch.setattr(runner, 'Observer', FakeObserver)
    monkeypatch.setattr(runner, 'pod_for', lambda *args: pod)
    monkeypatch.setattr(runner, 'barrier', lambda *args: ready)
    monkeypatch.setattr(runner, 'host_call', lambda *args: json.dumps({'clock_domain': 'host-clock'}))
    def check(kube, driver, run, workers, out):
        assert workers == [{'task_id': '0', 'pid': 1, 'uid': 0, 'pod_name': 'benchmark-lead',
                            'pod_uid': 'worker-uid', 'run_id': 'a' * 32}]
        raise ValueError('MPS check reached before START')
    monkeypatch.setattr(runner, 'mps_runtime', check)
    run = {'nonce': 'a' * 32, 'options': {'node': 'dra-a40'}}
    with pytest.raises(ValueError, match='MPS check reached before START'):
        runner.execute_clients(FakeKube(), 'driver', run, {'mode': 'mps'}, {'gpu_uuid': 'target-gpu'},
                               [job], tmp_path, {})
    assert stopped == [True]


@pytest.fixture
def namespace_cleanup(tmp_path):
    namespace = {'kind': 'Namespace', 'metadata': {'name': 'gpu-performance-lab', 'uid': 'original',
                 'labels': {'app.kubernetes.io/part-of': 'dra-book'}}}
    class Fake:
        def __init__(self):
            self.out, self.calls, self.reads = tmp_path, [], 0
            self.namespace = copy.deepcopy(namespace)
            self.contents = {'pods': [], 'serviceaccounts': [{'metadata': {'name': 'default'}}],
                             'configmaps': [{'metadata': {'name': 'kube-root-ca.crt'}}]}
            self.replace = False
        def get(self, resource, *args):
            if resource == 'namespace':
                self.reads += 1
                if self.replace and self.reads == 2:
                    self.namespace['metadata']['uid'] = 'replacement'
                return copy.deepcopy(self.namespace)
            return {'items': self.contents[resource]}
        def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            if args[0] == 'api-resources':
                assert '--namespaced=true' in args and '--verbs=list' in args
                return '\n'.join([*self.contents, 'events', 'events.events.k8s.io']) + '\n'
            assert args == ('delete', '--raw', '/api/v1/namespaces/gpu-performance-lab', '-f', '-')
            assert json.loads(kwargs['stdin'])['preconditions'] == {'uid': 'original'}
            self.namespace = None
            return ''
    return Fake()


def test_cleanup_deletes_empty_owned_namespace_with_uid_precondition_and_waits(namespace_cleanup, monkeypatch):
    runner = module('run')
    waited = []
    def wait(check, timeout, description):
        waited.append((timeout, description))
        assert check() is True
    monkeypatch.setattr(runner, 'wait_for', wait)
    runner.cleanup_namespace(namespace_cleanup)
    assert namespace_cleanup.namespace is None and waited == [(300, 'Namespace removed')]
    calls = len(namespace_cleanup.calls)
    runner.cleanup_namespace(namespace_cleanup)
    assert len(namespace_cleanup.calls) == calls


@pytest.mark.parametrize('damage', ['label', 'foreign', 'other_run', 'replacement'])
def test_cleanup_preserves_namespace_with_foreign_or_remaining_objects(namespace_cleanup, damage):
    runner = module('run')
    fake = namespace_cleanup
    if damage == 'label':
        fake.namespace['metadata']['labels'].clear()
    elif damage in ('foreign', 'other_run'):
        labels = {} if damage == 'foreign' else {'app.kubernetes.io/part-of': 'dra-book', 'dra-book-run': 'another-run'}
        fake.contents['pods'] = [{'metadata': {'name': 'remaining-worker', 'labels': labels}}]
    else:
        fake.replace = True
    with pytest.raises(ValueError, match='Namespace|namespace'):
        runner.cleanup_namespace(fake)
    assert fake.namespace is not None and all(args[0] != 'delete' for args, kwargs in fake.calls)


@pytest.mark.parametrize('namespace_exists', [True, False])
def test_explicit_cleanup_removes_namespace_after_gpu_idle_and_is_repeatable(tmp_path, monkeypatch, namespace_cleanup, namespace_exists):
    runner = module('run')
    lab = tmp_path / 'lab'
    run = {'nonce': 'a' * 32, 'options': {'node': 'dra-a40', 'modes': ['exclusive']}}
    runner.save(lab / 'out/smoke/run.json', run)
    fake = namespace_cleanup
    if not namespace_exists:
        fake.namespace = None
    monkeypatch.setattr(runner, 'LAB', lab)
    monkeypatch.setattr(runner, 'Kube', lambda out: fake)
    steps = []
    monkeypatch.setattr(runner, 'collect', lambda *args: steps.append('collect'))
    def cleanup(kube, current_run, *, abort):
        assert abort and current_run == run
        steps.append('cleanup')
    monkeypatch.setattr(runner, 'cleanup', cleanup)
    monkeypatch.setattr(runner, 'driver', lambda *args: 'driver-pod')
    monkeypatch.setattr(runner, 'target', lambda *args: {})
    def idle(*args):
        assert fake.namespace is not None
        steps.append('idle')
        return True
    monkeypatch.setattr(runner, 'idle_if_ready', idle)
    assert runner.main(['cleanup', 'smoke']) == 0
    assert fake.namespace is None
    assert steps == (['collect', 'cleanup', 'idle'] if namespace_exists else [])
    assert any(args[0] == 'delete' for args, kwargs in fake.calls) == namespace_exists


def test_book_displays_every_manifest_in_full():
    chapter = (ROOT / 'book/05-performance/README.md').read_text()
    for path in (LAB / 'manifests').rglob('*.yaml'):
        assert path.read_text().rstrip() in chapter, str(path)


def test_native_provider_change_protects_original_identity_and_other_nodes(monkeypatch):
    settings = module('native_settings')
    spec = {'updateStrategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxUnavailable': '100%'}},
            'template': {'spec': {'containers': [{'name': 'gpus', 'image': 'original-image',
                'env': [{'name': 'FEATURE_GATES', 'value': 'MPSSupport=true,Unrelated=false,'}]}]}}}
    original = {'metadata': {'name': 'native', 'uid': 'original'}, 'spec': spec}
    expected = settings.prepared(spec)
    assert settings.gates(expected) == dict(settings.DESIRED, Unrelated='false')
    assert expected['template']['spec']['containers'][0]['image'] == 'original-image'
    assert spec['updateStrategy']['type'] == 'RollingUpdate'
    state = {'parameters': {'namespace': 'driver', 'daemonset': 'native', 'node': 'dra-a40'},
             'protected': {'other-node-plugin': 'protected'}}
    protected = {'metadata': {'name': 'other-node-plugin', 'uid': 'protected',
                  'ownerReferences': [{'kind': 'DaemonSet', 'uid': 'original'}]},
                 'spec': {'nodeName': 'dra-4090'}}
    target = {'metadata': {'name': 'target', 'uid': 'target',
              'ownerReferences': [{'kind': 'DaemonSet', 'uid': 'original'}]},
              'spec': dict(spec['template']['spec'], nodeName='dra-a40'), 'status': {}}
    class Fake:
        def __init__(self):
            self.current, self.live, self.calls = copy.deepcopy(original), [protected, target], []
        def get(self, resource, *args):
            return self.current if resource == 'daemonset' else {'items': self.live}
        def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
    fake = Fake()
    fake.current['metadata']['uid'] = 'replacement'
    with pytest.raises(ValueError, match='UID/spec changed'):
        settings.check(fake, state, original, [spec])
    fake = Fake()
    fake.live[0] = copy.deepcopy(protected)
    fake.live[0]['metadata']['uid'] = 'changed'
    with pytest.raises(ValueError, match='another node plugin changed'):
        settings.check(fake, state, original, [spec])
    assert not fake.calls
    fake = Fake()
    fake.current['spec'] = expected
    # Read-only verification must not delete an old-template plugin.
    def once(check, *args):
        assert check() is None
        raise ValueError('not yet ready')
    monkeypatch.setattr(settings.benchmark, 'wait_for', once)
    with pytest.raises(ValueError, match='not yet ready'):
        settings.restart_target(fake, state, original, expected, allow_delete=False)
    assert not fake.calls


def test_native_benchmark_rejects_the_setup_only_feature_gates():
    runner = module('run')
    pod = {'spec': {'containers': [{'name': 'gpus', 'env': [
           {'name': 'FEATURE_GATES', 'value': 'MPSSupport=true,'}]}]}}
    class Fake:
        def get(self, *args):
            return {'items': [pod]}
    with pytest.raises(ValueError, match='native feature gates differ'):
        runner.native_features(Fake(), {'node': 'dra-a40'})
    pod['spec']['containers'][0]['env'][0]['value'] = 'MPSSupport=true,TimeSlicingSettings=true,ConsumableShares=false'
    assert runner.native_features(Fake(), {'node': 'dra-a40'}) is pod
