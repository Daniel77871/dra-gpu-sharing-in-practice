"""Isolation evidence: accounting, CUDA allocation and whole-device NVML."""
import csv
import importlib.util
from pathlib import Path

from tools.figures import register, style

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


@register('04-isolation', 'mps-quota')
def mps_quota(data):
    groups = mps_cases(data)
    fig, ax = style.new_chart('MPS 上限套在各 process，dual-process tensor 合計可超過 8 GiB', 'B 的條件', 'Tensor 或設定上限（GiB）')
    labels = ['single-process 4 GiB', 'single-process 8 GiB', 'dual-process 各 8 GiB']
    values = [group[0]['b_max_tensor_gib'] for group in groups]
    bars = ax.bar([0, 1, 2], values, width=.52, color=style.COLORS['mps'], label='B 成功 tensor 合計')
    ax.hlines([4, 8, 8], [-.16, .84, 1.84], [.16, 1.16, 2.16], linewidths=2,
              color=style.SECONDARY, label='每個 client 的設定上限')
    ax.bar_label(bars, padding=-14, color=style.INK)
    ax.set_xticks([0, 1, 2], labels)
    ax.set_ylim(0, 12)
    ax.text(1.6, 10.8, '兩個 client 各保留 5 GiB', color=style.SECONDARY, fontsize=9)
    return fig


@register('04-isolation', 'mps-nvml')
def mps_nvml(data):
    groups = mps_cases(data)
    fig, ax = style.new_chart('MPS 的 NVML 整卡用量含 A 與其他開銷', 'B 的條件', 'B 活躍期間整卡取樣峰值（MiB）')
    style.bars(ax, ['single-process 4 GiB', 'single-process 8 GiB', 'dual-process 各 8 GiB'],
               [max(r['nvml_peak_used_mib'] for r in g) for g in groups], ['mps'] * 3)
    ax.set_ylim(0, 15500)
    ax.text(.04, .91, '每個條件的三輪峰值相同，這不是 B Pod 的獨立用量', transform=ax.transAxes, fontsize=9, color=style.SECONDARY)
    return fig


@register('04-isolation', 'mps-talk')
def mps_talk(data):
    with (data / 'e2/mps-talk.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    fig, ax = style.new_chart('2019 年分享案例，小工作可因 MPS 提升吞吐量', '相對未啟用 MPS 的吞吐量倍數', '講者的案例')
    fig.set_size_inches(8, 3.7)
    ratios = [float(r['with_mps']) / float(r['without_mps']) for r in rows]
    bars = ax.barh(range(len(rows)), ratios, color=style.COLORS['mps'], height=.52)
    ax.bar_label(bars, labels=[r['quoted_speedup'] + '×' for r in rows], padding=5, color=style.INK)
    ax.axvline(1, color=style.BASELINE, linestyle='--', linewidth=1)
    ax.set_yticks(range(len(rows)), [f'{r["case"]}，{r["processes"]} 個 process' for r in rows])
    ax.set_xlim(0, 4.3)
    ax.set_ylim(-.5, 1.65)
    ax.text(.02, .93, '來源：吳磊（NVIDIA，2019-10-31），非本章 A40 實測', transform=ax.transAxes, fontsize=9, color=style.SECONDARY)
    return fig


@register('04-isolation', 'accounting-balance')
def accounting_balance(data):
    rows = rounds(data, 'consumable-shares', 'accounting')
    if len(rows) != 3:
        raise ValueError('expected three consumable accounting rounds')
    row = rows[0]
    fig, ax = style.new_chart('排程餘額與實體空閒量回答不同問題', '16 GiB 與 24 GiB Claim 保留後', '容量（MiB）')
    style.bars(ax, ['可排程餘額', '整卡實體可用量'],
               [row['schedulable_remaining_mib_before'], row['physical_free_mib_before']], ['consumable-shares', 'NVIDIA A40'])
    ax.axhline(8192, color=style.SECONDARY, linestyle='--', linewidth=1.2, label='第三份 Claim 申請 8 GiB（8192 MiB）')
    ax.set_ylim(0, 52000)
    return fig


@register('04-isolation', 'consumable-nvml')
def consumable_nvml(data):
    rows = rounds(data, 'consumable-shares', 'boundary')
    fig, ax = style.new_chart('ConsumableShares 超額後立即退出，取樣可能漏掉峰值', '輪次', 'B 活躍期間整卡取樣峰值（MiB）')
    style.bars(ax, [f'r{r["repeat"]}' for r in rows], [r['nvml_peak_used_mib'] for r in rows], ['consumable-shares'] * len(rows))
    ax.set_ylim(0, 13500)
    ax.text(.02, .93, '包含 A 與 driver 開銷，不等於 B Pod 用量', transform=ax.transAxes, fontsize=9, color=style.SECONDARY)
    return fig


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
              ('HAMi single-process', 'hami', hami_rounds(data, 'boundary')),
              ('HAMi dual-process', 'hami', hami_rounds(data, 'scope')),
              ('MPS single-process 4 GiB', 'mps', [r for r in rounds(data, 'mps', 'boundary') if r['quota_gib'] == 4]),
              ('MPS single-process 8 GiB', 'mps', [r for r in rounds(data, 'mps', 'boundary') if r['quota_gib'] == 8]),
              ('MPS dual-process', 'mps', rounds(data, 'mps', 'scope'))]
    if any(len(rows) != 3 for _, _, rows in groups):
        raise ValueError('comparison chart requires three rounds per condition')
    return groups


@register('04-isolation', 'hami-runtime-boundary')
def hami_runtime_boundary(data):
    rows = hami_rounds(data, 'boundary') + hami_rounds(data, 'scope')
    fig, ax = style.new_chart('HAMi 同 Pod 的 dual-process 成功 tensor 合計低於 8 GiB', 'B 的條件與輪次', 'B 成功 tensor 合計（GiB）')
    fig.set_size_inches(8.4, 4.6)
    labels = [f'{"single-process" if r["case"] == "boundary" else "dual-process"} r{r["repeat"]}' for r in rows]
    bars = ax.bar(range(6), [r['b_max_tensor_gib'] for r in rows], color=style.COLORS['hami'], width=.6, label='B 成功 tensor 合計')
    ax.bar_label(bars, padding=3, color=style.INK)
    ax.axhline(rows[0]['quota_gib'], color=style.SECONDARY, linestyle='--', linewidth=1.2, label='共享 Claim 配額 8 GiB')
    ax.set_xticks(range(6), labels)
    ax.set_ylim(0, 10)
    ax.text(.02, .91, '每輪皆遇到真實 CUDA 配置 OOM', transform=ax.transAxes, fontsize=9, color=style.SECONDARY)
    return fig


@register('04-isolation', 'hami-runtime-physical')
def hami_runtime_physical(data):
    rows = hami_rounds(data, 'boundary') + hami_rounds(data, 'scope')
    fig, ax = style.new_chart('HAMi dual-process 兩輪實體用量超過 8192 MiB', 'B 的條件與輪次', 'B process 實體用量合計（MiB）')
    fig.set_size_inches(8.4, 4.6)
    labels = [f'{"single-process" if r["case"] == "boundary" else "dual-process"} r{r["repeat"]}' for r in rows]
    bars = ax.bar(range(6), [r['b_nvml_used_mib'] for r in rows], color=style.COLORS['hami'], width=.6, label='B process 實體快照')
    ax.bar_label(bars, padding=3, color=style.INK)
    ax.axhline(rows[0]['quota_gib'] * 1024, color=style.SECONDARY, linestyle='--', linewidth=1.2, label='Claim 配額 8192 MiB')
    ax.set_xticks(range(6), labels)
    ax.set_ylim(0, 10500)
    ax.text(.02, .91, '共同保留期的 B process 快照，不包含 A', transform=ax.transAxes, fontsize=9, color=style.SECONDARY)
    return fig


@register('04-isolation', 'hami-scope')
def hami_scope(data):
    rows = hami_analysis.analyze_run(data / 'e2b/raw')
    if len(rows) != 9 or sum(r['complete'] for r in rows) != 8:
        raise ValueError('scope chart requires eight completed and one stalled original round')
    style.configure()
    fig, axes = style.plt.subplots(2, 1, figsize=(10, 8.1))
    labels = [f'{r["case"]} r{r["repeat"]}' for r in rows]
    for ax, field, last, limit, ylabel in [
        (axes[0], 'tensor_total_gib', 'last_barrier_tensor_total_gib', 8, '成功 tensor 合計（GiB）'),
        (axes[1], 'nvml_process_total_mib', 'last_barrier_nvml_process_total_mib', 8192, '兩個測量 process 的實體用量合計（MiB）')]:
        indices = [i for i, r in enumerate(rows) if r['complete']]
        ax.bar(indices, [rows[i][field] for i in indices], width=.62, color=style.COLORS['hami'], label='完成輪次')
        failed = [i for i, r in enumerate(rows) if not r['complete']]
        ax.scatter(failed, [rows[i][last] for i in failed], facecolors='none', edgecolors=style.INK,
                   marker='D', s=65, label='停滯輪次的最後完整 barrier')
        ax.hlines(limit, -.4, 5.4, color=style.SECONDARY, linestyle='--', linewidth=1.2,
                  label='共用 Claim 配額 8 GiB（8192 MiB）')
        ax.hlines(limit * 2, 5.6, 8.4, color=style.MUTED, linestyle=':', linewidth=1.2,
                  label='兩份獨立 Claim 配額合計 16 GiB（16384 MiB）')
        ax.set_xticks(range(9), labels)
        ax.set_ylabel(ylabel)
        ax.set_ylim(0, limit * 2.2)
    axes[0].set_title('同一 Claim 與 cache 沒有保證跨 Pod 合計上限')
    axes[1].set_xlabel('條件與輪次')
    axes[0].text(.02, .91, '空心菱形不是最終配額結果；Case 4b 只測每個 process 6 GiB target',
                 transform=axes[0].transAxes, fontsize=9, color=style.SECONDARY)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(.5, 0), ncol=2,
               frameon=False, labelcolor=style.INK)
    fig.text(.5, -.07, 'Case 3：同 Pod 兩個 container；Case 4a：跨 Pod 共用 Claim；Case 4b：獨立 Claim',
             ha='center', va='top', fontsize=9, color=style.SECONDARY)
    return fig


@register('04-isolation', 'e2-tensor-comparison')
def e2_tensor_comparison(data):
    groups = comparison_cases(data)
    fig, ax = style.new_chart('排程記帳量與 CUDA process 上限具有不同範圍', '方案與 B 的條件', 'B 成功 tensor 合計（GiB）')
    fig.set_size_inches(11, 4.9)
    for i, (_, entity, rows) in enumerate(groups):
        ax.bar([i - .22, i, i + .22], [r['b_max_tensor_gib'] for r in rows], width=.19,
               color=style.COLORS[entity])
        ax.hlines(rows[0]['quota_gib'], i - .33, i + .33, color=style.SECONDARY,
                  linewidth=2, label='Claim 或每 client 設定上限' if i == 0 else None)
    ax.set_xticks(range(6), [label for label, _, _ in groups], rotation=12)
    ax.set_ylim(0, 13)
    ax.text(.02, .91, '每組由左至右為 r1、r2、r3；MPS 上限套在每個 client',
            transform=ax.transAxes, fontsize=9, color=style.SECONDARY)
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(.5, 0),
               frameon=False, labelcolor=style.INK)
    return fig


@register('04-isolation', 'e2-physical-comparison')
def e2_physical_comparison(data):
    groups = comparison_cases(data)
    style.configure()
    fig, axes = style.plt.subplots(2, 1, figsize=(11, 8))
    for ax, field, label in [(axes[0], 'nvml_peak_used_mib', 'B 活躍期間整卡取樣峰值（MiB）'),
                             (axes[1], 'nvml_min_free_mib', '同一觀察窗口整卡最少可用量（MiB）')]:
        for i, (_, entity, rows) in enumerate(groups):
            ax.bar([i - .22, i, i + .22], [r[field] for r in rows], width=.19, color=style.COLORS[entity])
        ax.set_xticks(range(6), [name for name, _, _ in groups], rotation=12)
        ax.set_ylabel(label)
    axes[0].set_title('整卡 NVML 包含 A 與其他開銷，不能直接判定 B 的配額')
    axes[0].set_ylim(0, 17000)
    axes[1].set_ylim(0, 45000)
    axes[1].set_xlabel('各組由左至右為 r1、r2、r3')
    axes[0].text(.02, .91, 'ConsumableShares 超額後立即退出，取樣可能漏掉瞬間峰值',
                 transform=axes[0].transAxes, fontsize=9, color=style.SECONDARY)
    return fig


@register('04-isolation', 'e2-stability')
def e2_stability(data):
    consumable = rounds(data, 'consumable-shares', 'boundary')
    hami = hami_rounds(data, 'boundary') + hami_rounds(data, 'scope')
    mps = rounds(data, 'mps')
    counts = [sum(r['a_probe_ok_pressure'] for r in consumable), sum(r['hold_probe_ok'] for r in hami),
              sum(r['hold_probe_ok'] for r in mps)]
    errors = [sum(r['a_probe_oom_pressure'] for r in consumable), sum(r['hold_probe_oom'] for r in hami),
              sum(r['hold_probe_oom'] for r in mps)]
    fig, ax = style.new_chart('有限壓力下 A 都沒有配置 OOM；觀察窗口不同', '方案與統計窗口', 'A 配置 probe 次數')
    fig.set_size_inches(9.1, 4.7)
    style.bars(ax, ['ConsumableShares 壓力期 3輪', 'HAMi hold 6輪', 'MPS hold 9輪'], counts,
               ['consumable-shares', 'hami', 'mps'])
    ax.scatter(range(3), errors, marker='x', s=45, color=style.COLORS['pressure'], label='配置 OOM')
    ax.set_ylim(-8, 340)
    ax.text(.02, .91, '次數不構成強度排名；ConsumableShares 沒有共同 hold 窗口',
            transform=ax.transAxes, fontsize=9, color=style.SECONDARY)
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(.5, 0), ncol=4,
               frameon=False, labelcolor=style.INK)
    return fig


@register('04-isolation', 'e2-accounting-comparison')
def e2_accounting_comparison(data):
    consumable = rounds(data, 'consumable-shares', 'accounting')[0]
    hami = hami_rounds(data, 'accounting')[0]
    runtime = hami_rounds(data, 'boundary')[0]
    style.configure()
    fig, axes = style.plt.subplots(2, 1, figsize=(10, 8))
    labels = ['Consumable 釋放前', 'Consumable 釋放後', 'HAMi 釋放前', 'HAMi 釋放後', 'HAMi runtime A+B']
    remaining = [consumable['schedulable_remaining_mib_before'], consumable['schedulable_remaining_mib_after'],
                 hami['schedulable_remaining_mib_before'], hami['schedulable_remaining_mib_after'], runtime['schedulable_remaining_mib']]
    bars = axes[0].bar(range(5), remaining, width=.57, color=style.COLORS['hami'], label='memory 帳本餘額')
    axes[0].bar_label(bars, padding=3, color=style.INK)
    axes[0].scatter([0, 2], [consumable['physical_free_mib_before'], hami['physical_free_mib_before']],
                    marker='D', s=45, color=style.COLORS['NVIDIA A40'], label='記帳時整卡實體 free')
    axes[0].set(title='HAMi runtime 尚有 memory 餘額；cores 已全數記帳', ylabel='容量（MiB）')
    axes[0].set_xticks(range(5), labels, rotation=12)
    axes[0].set_ylim(0, 54000)
    before, after = hami['reserved_cores_before'], hami['reserved_cores_after']
    remaining_cores = [hami['schedulable_remaining_cores_before'], hami['schedulable_remaining_cores_after'], runtime['schedulable_remaining_cores']]
    axes[1].bar(range(3), [before, after, runtime['reserved_cores']], width=.57, color=style.COLORS['hami'], label='已記帳 cores')
    axes[1].bar(range(3), remaining_cores, bottom=[before, after, runtime['reserved_cores']], width=.57,
                color=style.BASELINE, label='cores 餘額')
    axes[1].set_xticks(range(3), ['HAMi 記帳釋放前', 'HAMi 記帳釋放後', 'HAMi runtime A+B'])
    axes[1].set(ylabel='cores', xlabel='記帳測試每份 Claim 為 10 cores；runtime 每份為 50 cores', ylim=(0, 120))
    return fig
