"""Shared E2 data access for the isolation charts (MPS, ConsumableShares, HAMi-DRA)."""
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('lab04_figures_analysis', Path(__file__).resolve().parents[2] / 'labs/04-isolation/common/analysis/analyze.py')
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def rounds(data, backend, case=None):
    rows = [r for r in analysis.analyze_run(data / 'e2/raw') if r['backend'] == backend and (case is None or r['case'] == case)]
    if not rows or any(not r['complete'] for r in rows):
        raise ValueError('isolation chart requires complete raw rounds')
    return rows


def mps_cases(data):
    rows = rounds(data, 'mps')
    groups = [[r for r in rows if r['case'] == case and r['quota_gib'] == quota]
              for case, quota in [('boundary', 4), ('boundary', 8), ('scope', 8)]]
    if any(len(group) != 3 for group in groups):
        raise ValueError('expected three repeats of each MPS condition')
    return groups



HAMI_SPEC = importlib.util.spec_from_file_location('lab04_figures_hami', Path(__file__).resolve().parents[2] / 'labs/04-isolation/common/analysis/hami.py')
hami_analysis = importlib.util.module_from_spec(HAMI_SPEC)
HAMI_SPEC.loader.exec_module(hami_analysis)


def hami_rounds(data, case=None):
    rows = [r for r in hami_analysis.analyze_run(data / 'e2/hami/raw')
            if case is None or r['case'] == case]
    if not rows or any(not r['complete'] for r in rows):
        raise ValueError('HAMi runtime chart requires complete raw rounds')
    return rows


def comparison_cases(data):
    """Six conditions keep the distinct CUDA scopes and all three repeats."""
    groups = [('ConsumableShares single-process', 'consumable-shares', rounds(data, 'consumable-shares', 'boundary')),
              ('HAMi-DRA single-process', 'hami', hami_rounds(data, 'boundary')),
              ('HAMi-DRA dual-process', 'hami', hami_rounds(data, 'scope')),
              ('MPS single-process 4 GiB', 'mps', [r for r in rounds(data, 'mps', 'boundary') if r['quota_gib'] == 4]),
              ('MPS single-process 8 GiB', 'mps', [r for r in rounds(data, 'mps', 'boundary') if r['quota_gib'] == 8]),
              ('MPS dual-process', 'mps', rounds(data, 'mps', 'scope'))]
    if any(len(rows) != 3 for _, _, rows in groups):
        raise ValueError('comparison chart requires three rounds per condition')
    return groups

