"""Guard the numeric evidence and the allocation safety stop in the shared lab."""
import csv
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import re

import pytest
import yaml

from conftest import ROOT

LAB = ROOT / 'labs/04-isolation/common'
RAW = ROOT / 'data/e2/raw'
GIB = 1024 ** 3


def module(name, directory='analysis'):
    path = LAB / directory / (name + '.py')
    assert path.is_file(), f'Missing shared implementation: {path}'
    spec = importlib.util.spec_from_file_location('lab04_' + name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(loaded)
    finally:
        sys.path.pop(0)
    return loaded


@pytest.mark.parametrize('prefix,quota,tensor,peak,free,ooms', [
    ('boundary-4g', 4, 3.5, 6283, 39208, 1),
    ('boundary-8g', 8, 7.5, 10379, 35112, 1),
    ('scope-8g', 8, 10, 13246, 32245, 0),
])
def test_mps_original_rounds_reproduce_retained_window_and_physical_results(prefix, quota, tensor, peak, free, ooms):
    analyze = module('analyze')
    for repeat in range(1, 4):
        row = analyze.analyze_round(RAW / f'{prefix}-r{repeat}')
        assert row['complete'], row['incomplete_reason']
        assert row['quota_gib'] == quota
        assert row['b_max_tensor_gib'] == tensor
        assert row['cuda_oom_count'] == ooms
        assert row['b_outcome'] == ('target_reached' if prefix == 'scope-8g' else 'allocation_oom')
        assert row['hold_probe_ok'] == 30
        assert row['hold_probe_oom'] == 0
        assert row['a_compute_errors'] == 0
        assert row['a_exit_code'] == row['b_exit_code'] == 0
        assert row['nvml_peak_used_mib'] == peak
        assert row['nvml_min_free_mib'] == free
        if prefix == 'scope-8g':
            assert row['b_tensor_gib_by_client'] == {'0': 5, '1': 5}
            assert round(row['hold_seconds'], 3) == [30.000, 29.985, 29.982][repeat - 1]
        else:
            assert round(row['hold_seconds'], 3) == 30.001


@pytest.mark.parametrize('repeat,peak,free', [(1, 10354, 35137), (2, 10866, 34625), (3, 9586, 35905)])
def test_consumable_rounds_use_b_active_samples_instead_of_post_exit_snapshot(repeat, peak, free):
    row = module('analyze').analyze_round(RAW / f'consumable-boundary-8g-r{repeat}')
    assert row['complete'], row['incomplete_reason']
    assert row['b_max_tensor_gib'] == 8.25
    assert row['quota_gib'] == 8
    assert row['b_outcome'] == 'limit_not_enforced'
    assert row['cuda_oom_count'] == 0
    assert row['hold_seconds'] is None
    assert row['hold_probe_ok'] is None
    assert row['a_probe_oom_pressure'] == 0
    assert row['a_compute_errors'] == 0
    assert row['a_exit_code'] == row['b_exit_code'] == 0
    assert row['nvml_peak_used_mib'] == peak
    assert row['nvml_min_free_mib'] == free


def test_accounting_recomputes_allocations_and_preserves_scheduler_failure():
    for repeat in range(1, 4):
        row = module('analyze').analyze_round(RAW / f'consumable-accounting-r{repeat}')
        assert row['complete'], row['incomplete_reason']
        assert row['published_mib'] == 46068
        assert row['reserved_mib_before'] == 40960
        assert row['schedulable_remaining_mib_before'] == 5108
        assert row['physical_free_mib_before'] == 45490
        assert row['reserved_mib_after'] == 24576
        assert row['schedulable_remaining_mib_after'] == 21492
        assert row['third_claim_pending'] is True
        assert row['third_claim_allocated_after_release'] is True
        assert 'cannot allocate all claims' in row['pending_messages']


def test_cli_summarizes_run_without_modifying_raw_evidence(tmp_path):
    analyze = module('analyze')
    before = {p: p.read_bytes() for p in RAW.glob('*/*') if p.is_file()}
    rows = analyze.analyze_run(RAW)
    assert len(rows) == 15
    assert sum(r['hold_probe_ok'] or 0 for r in rows if r['backend'] == 'mps') == 270
    command = [sys.executable, str(LAB / 'analysis/analyze.py'), str(RAW), '--output', str(tmp_path)]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(json.loads((tmp_path / 'summary.json').read_text())) == 15
    with (tmp_path / 'summary.csv').open() as stream:
        assert len(list(csv.DictReader(stream))) == 15
    assert before == {p: p.read_bytes() for p in before}


def copy_round(tmp_path, name='boundary-8g-r1'):
    destination = tmp_path / name
    shutil.copytree(RAW / name, destination)
    return destination


def test_missing_termination_is_unknown_and_not_inferred_from_normal_exit_log(tmp_path):
    path = copy_round(tmp_path)
    (path / 'pressure-final.json').unlink()
    meta = json.loads((path / 'trial.json').read_text())
    meta.pop('terminations', None)
    (path / 'trial.json').write_text(json.dumps(meta))
    row = module('analyze').analyze_round(path)
    assert row['b_exit_code'] is None
    assert not row['complete']
    assert 'termination' in row['incomplete_reason']


def test_dual_hold_counts_only_concurrent_clients_and_flags_missing_marker(tmp_path):
    path = copy_round(tmp_path, 'scope-8g-r1')
    lines = (path / 'task-b.log').read_text().splitlines()
    lines = [line for line in lines if not (json.loads(line)['event'] == 'B_HOLD_END'
                                           and json.loads(line).get('client_id') == '1')]
    (path / 'task-b.log').write_text('\n'.join(lines) + '\n')
    row = module('analyze').analyze_round(path)
    assert not row['complete']
    assert row['hold_probe_ok'] is None
    assert 'hold' in row['incomplete_reason']


def test_external_oomkill_is_not_reported_as_successful_quota_boundary(tmp_path):
    path = copy_round(tmp_path)
    pod = json.loads((path / 'pressure-final.json').read_text())
    pod['status']['containerStatuses'][0]['state']['terminated'].update(exitCode=137, reason='OOMKilled')
    (path / 'pressure-final.json').write_text(json.dumps(pod))
    row = module('analyze').analyze_round(path)
    assert row['b_exit_code'] == 137
    assert row['b_outcome'] == 'kubernetes_oomkilled'
    assert not row['complete']


def test_nvml_requires_sample_finish_within_b_interval_and_target_uuid(tmp_path):
    path = copy_round(tmp_path, 'consumable-boundary-8g-r1')
    rows = [json.loads(s) for s in (path / 'gpu-metrics.jsonl').read_text().splitlines()]
    inside = rows[0]
    b = [json.loads(s) for s in (path / 'task-b.log').read_text().splitlines()]
    end = next(r['utc'] for r in b if r['event'] == 'B_EXIT')
    rows += [dict(inside, **{'memory.used': '45000', 'finished_utc': end.replace('2026-', '2027-')}),
             dict(inside, **{'memory.used': '45000', 'uuid': 'GPU-other'})]
    (path / 'gpu-metrics.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    row = module('analyze').analyze_round(path)
    assert row['nvml_peak_used_mib'] == 10354
    assert not row['complete']  # A wrong-device sample is an evidence error.


def test_empty_run_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='round'):
        module('analyze').analyze_run(tmp_path)


def test_missing_recovery_log_samples_cannot_be_replaced_by_elapsed_metadata(tmp_path):
    path = copy_round(tmp_path)
    rows = [json.loads(line) for line in (path / 'task-a.log').read_text().splitlines()]
    (path / 'task-a.log').write_text(''.join(json.dumps(row) + '\n' for row in rows if row.get('phase') != 'recovery'))
    row = module('analyze').analyze_round(path)
    assert not row['complete']
    assert 'recovery' in row['incomplete_reason']


def test_runner_failure_stays_incomplete_even_when_collected_workload_logs_are_valid(tmp_path):
    path = copy_round(tmp_path)
    meta = json.loads((path / 'trial.json').read_text())
    meta.update(complete=False, valid=False, error='runner failed during cleanup')
    (path / 'trial.json').write_text(json.dumps(meta))
    row = module('analyze').analyze_round(path)
    assert not row['complete']
    assert 'runner failed' in row['incomplete_reason']


def test_missing_verified_cleanup_cannot_claim_completed_round(tmp_path):
    path = copy_round(tmp_path)
    meta = json.loads((path / 'trial.json').read_text())
    meta.pop('cleanup')
    (path / 'trial.json').write_text(json.dumps(meta))
    row = module('analyze').analyze_round(path)
    assert not row['complete']
    assert 'cleanup' in row['incomplete_reason']


@pytest.mark.parametrize('units', ['', ' MiB'])
def test_live_nvidia_csv_with_utc_timestamps_preserves_b_window_peak(tmp_path, units):
    path = copy_round(tmp_path, 'consumable-boundary-8g-r1')
    samples = [json.loads(line) for line in (path / 'gpu-metrics.jsonl').read_text().splitlines()]
    (path / 'gpu-metrics.jsonl').unlink()
    with (path / 'gpu.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['timestamp', 'uuid', 'memory.total [MiB]', 'memory.used [MiB]', 'memory.free [MiB]'])
        for sample in samples:
            writer.writerow([sample['utc'].split('+')[0].replace('-', '/').replace('T', ' '), sample['uuid'],
                             *(str(sample[key]) + units for key in ('memory.total','memory.used','memory.free'))])
    row = module('analyze').analyze_round(path)
    assert row['complete'], row['incomplete_reason']
    assert row['nvml_peak_used_mib'] == 10354
    assert row['nvml_min_free_mib'] == 35137


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class GPU:
    class oom_error(RuntimeError):
        pass

    def __init__(self, successful_blocks=None):
        self.successful_blocks = successful_blocks
        self.allocated = 0

    def allocate(self, nbytes):
        if self.successful_blocks is not None and self.allocated >= self.successful_blocks:
            raise self.oom_error('allocation boundary')
        self.allocated += 1
        return nbytes

    def synchronize(self):
        pass

    def stats(self):
        return {}


def run_pressure(gpu, **kwargs):
    workload = module('isolation_workload', 'workload')
    events = []
    clock = Clock()
    code = workload.run_pressure(gpu, lambda event, **fields: events.append({'event': event, **fields}),
                                 lambda: {}, quota_bytes=8 * GIB, clock=clock, **kwargs)
    return code, events, clock


def test_observer_stops_after_successful_825_gib_without_imposing_allocator_cap():
    code, events, clock = run_pressure(GPU())
    assert code == 0
    exceeded = next(e for e in events if e['event'] == 'B_LIMIT_EXCEEDED')
    assert exceeded['tensor_bytes'] == 8858370048
    assert events[-1]['outcome'] == 'limit_not_enforced'
    assert not any(e['event'] in ('B_HOLD', 'B_OOM') for e in events)
    assert clock.now < 30


def test_handled_allocation_oom_retains_tensor_then_exits_normally():
    code, events, clock = run_pressure(GPU(successful_blocks=30))
    assert code == 0
    assert next(e for e in events if e['event'] == 'B_OOM')['tensor_bytes'] == 8053063680
    assert next(e for e in events if e['event'] == 'B_HOLD')['hold_seconds'] == 30
    assert events[-1]['outcome'] == 'allocation_oom'
    assert clock.now >= 30


def test_target_client_holds_five_gib_for_dual_process_case():
    code, events, clock = run_pressure(GPU(), target_bytes=5 * GIB)
    assert code == 0
    assert next(e for e in events if e['event'] == 'B_TARGET')['tensor_bytes'] == 5368709120
    assert events[-1]['outcome'] == 'target_reached'
    assert clock.now >= 30


def test_e2_extractor_is_discovered_without_cli_registry_edit():
    from tools.extractors import REGISTRY, discover
    discover()
    assert 'e2' in REGISTRY


def test_mps_identity_and_process_snapshot_keep_client_attribution_evidence():
    identity_path = RAW / 'scope-8g-r1/mps-identity.json'
    assert identity_path.is_file()
    identity = json.loads(identity_path.read_text())
    pressure = [c for c in identity['clients'] if c['pod'] == 'pressure']
    assert {(c['client_id'], c['namespace_pid']) for c in pressure} == {('0', 40), ('1', 41)}
    assert len({c['pod_uid'] for c in pressure}) == 1
    assert all('container_id' not in c for c in identity['clients'])
    with (RAW / 'boundary-8g-r1/processes-during.txt').open() as stream:
        processes = [(int(pid), name.strip(), int(used)) for pid, name, used in csv.reader(stream)]
    assert processes == [(248675, 'nvidia-cuda-mps-server', 30), (248633, 'python', 2354), (248725, 'python', 7986)]


@pytest.mark.parametrize('directory', sorted(
    path.parent for name in ('common', 'consumable-shares', 'mps')
    for path in (ROOT / 'labs/04-isolation' / name).rglob('kustomization.yaml')
), ids=lambda path: str(path.relative_to(ROOT / 'labs/04-isolation')))
def test_shared_consumable_and_mps_kustomizations_render_with_pinned_binary(directory):
    kustomize = ROOT / '.work/bin/kustomize'
    if not kustomize.is_file():
        pytest.skip('pinned .work/bin/kustomize is missing')
    result = subprocess.run([str(kustomize), 'build', '--load-restrictor', 'LoadRestrictionsNone', str(directory)],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip(), f'No resources rendered from {directory}'


@pytest.mark.parametrize('builder,title,labels', [
    ('accounting_balance', '排程記帳只剩 5,108 MiB，整卡實體仍空閒 45,490 MiB',
     ['第三份 Claim 申請 8 GiB（8,192 MiB）']),
    ('mps_quota', 'MPS 上限套在每個 client：兩個 client 合計可達 10 GiB',
     ['7.5（上限 8）', 'client 1：5（上限 8）']),
])
def test_isolation_chart_thresholds_have_unambiguous_rendered_labels(tmp_path, builder, title, labels):
    """The reference values are explained by direct labels that appear as text in the SVG; the title is the finding."""
    from tools.figures import ch04, style
    fig = getattr(ch04, builder)(ROOT / 'data')
    assert fig.texts[0].get_text() == title
    output = tmp_path / f'{builder}.svg'
    style.save_svg(fig, output)
    rendered = re.findall(r'<!-- (.*?) -->', output.read_text())
    for label in labels:
        assert any(label in text for text in rendered), f'threshold label not rendered: {label}'


def test_isolation_figure_command_omits_redundant_runtime_and_stability_charts(tmp_path):
    (tmp_path / 'data').symlink_to(ROOT / 'data', target_is_directory=True)
    command = [sys.executable, str(ROOT / 'tools/make_figures.py'), '--only', '04-isolation', '--root', str(tmp_path)]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    names = {path.stem for path in (tmp_path / 'book/images/04-isolation').glob('*.svg')}
    assert {'accounting-balance', 'consumable-nvml', 'mps-quota', 'mps-nvml', 'mps-talk'} <= names
    assert not {'consumable-runtime', 'mps-stability'} & names


@pytest.mark.parametrize('group,originals', [
    ('consumable-shares/manifests/provider/provider.yaml',
     ['common/manifests/00-namespace.yaml', 'consumable-shares/manifests/provider/daemonset.yaml']),
    ('consumable-shares/manifests/accounting/resident.yaml',
     ['consumable-shares/manifests/accounting/00-capacity-resident-gpu.yaml',
      'consumable-shares/manifests/accounting/01-capacity-resident.yaml']),
    ('consumable-shares/manifests/accounting/release.yaml',
     ['consumable-shares/manifests/accounting/02-capacity-release-gpu.yaml',
      'consumable-shares/manifests/accounting/03-capacity-release.yaml']),
    ('consumable-shares/manifests/accounting/waiter.yaml',
     ['consumable-shares/manifests/accounting/04-capacity-waiter-gpu.yaml',
      'consumable-shares/manifests/accounting/05-capacity-waiter.yaml']),
    ('consumable-shares/manifests/runtime/steady-service.yaml',
     ['consumable-shares/manifests/runtime/00-steady-gpu.yaml',
      'consumable-shares/manifests/runtime/02-steady-service.yaml']),
    ('consumable-shares/manifests/runtime/quota-pressure.yaml',
     ['consumable-shares/manifests/runtime/01-pressure-gpu.yaml',
      'consumable-shares/manifests/runtime/03-quota-pressure.yaml']),
    ('mps/manifests/steady-service.yaml', ['mps/manifests/01-shared-claim.yaml', 'mps/manifests/02-steady-service.yaml']),
    ('mps/manifests/quota-pressure.yaml', ['mps/manifests/03-quota-pressure.yaml']),
    ('mps/manifests/dual-pressure.yaml', ['mps/manifests/04-dual-pressure.yaml']),
])
def test_grouped_book_manifests_keep_every_original_field_and_value(group, originals):
    lab = LAB.parent
    grouped = list(yaml.safe_load_all((lab / group).read_text()))
    expected = [yaml.safe_load((lab / path).read_text()) for path in originals]
    assert grouped == expected


@pytest.mark.parametrize('page', ['README.md', 'consumable-shares.md', 'mps.md'])
def test_book_deployment_yaml_is_complete_and_identical_to_shipped_files(page):
    lab = LAB.parent
    manifests = {path.read_text().strip() for subtree in ('common', 'consumable-shares', 'mps')
                 for path in (lab / subtree).rglob('*.yaml')}
    text = (ROOT / 'book/04-isolation' / page).read_text()
    for block in re.findall(r'```yaml\n(.*?)\n```', text, flags=re.S):
        # Published capacity is a marked output excerpt, not a deployment.
        if re.search(r'^kind: (Namespace|Job|ResourceClaim|DaemonSet)$', block, flags=re.M):
            list(yaml.safe_load_all(block))
            assert block.strip() in manifests, 'Deployment YAML differs from lab files in ' + page
