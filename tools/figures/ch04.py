"""Isolation charts, part 1: MPS quota, MPS NVML, MPS talk and ConsumableShares accounting and NVML.

Part 2 (HAMi-DRA and the three-method comparisons) lives in ch04_hami.py and is registered from this module,
so tests and the chapter build can use ``ch04.<builder>`` for every isolation chart.
"""
import csv

from tools.figures import register, style
from tools.figures.e2data import mps_cases, rounds


def _same(rows, field):
    """The value every repeat agrees on; a chart that averages disagreeing repeats would hide a change."""
    values = {row[field] for row in rows}
    if len(values) != 1:
        raise ValueError(f'{field} differs across repeats: {sorted(values)}')
    return values.pop()


def _gib(value):
    """Two decimals with trailing zeros trimmed: 3.5 -> '3.5', 8.25 -> '8.25', 10.0 -> '10'."""
    return style.fmt(value, 2).rstrip('0').rstrip('.')


@register('04-isolation', 'mps-quota')
def mps_quota(data):
    single4, single8, dual = mps_cases(data)
    quota4, quota8 = _same(single4, 'quota_gib'), _same(single8, 'quota_gib')
    tensor4, tensor8 = _same(single4, 'b_max_tensor_gib'), _same(single8, 'b_max_tensor_gib')
    per_client = dual[0]['b_tensor_gib_by_client']
    quota_dual = _same(dual, 'quota_gib')
    total_dual = _same(dual, 'b_max_tensor_gib')
    if abs(sum(per_client.values()) - total_dual) > 1e-9 or max(per_client.values()) > quota_dual:
        raise ValueError('dual-process per-client tensors must sum to the total and stay within each client quota')
    fig, ax = style.figure('MPS 上限套在每個 client：兩個 client 合計可達 10 GiB',
                           subtitle='B 成功保留的 tensor（GiB），三輪結果相同；短直線是單一 client 的上限',
                           note='上限套在每個 CUDA client，不是整個 Pod 的上限。',
                           height=4.0, value_axis='x')
    ys = style.rows_axis(ax, [f'single-process {_gib(quota4)} GiB', f'single-process {_gib(quota8)} GiB',
                              f'dual-process 各 {_gib(quota_dual)} GiB'], gaps_after=(1,))
    for y, value, quota in [(ys[0], tensor4, quota4), (ys[1], tensor8, quota8)]:
        ax.barh(y, value, height=0.6, color=style.COLORS['mps'], zorder=3)
        style.row_ref(ax, y, quota)
        style.value_label(ax, quota, y, f'{_gib(value)}（上限 {_gib(quota)}）', dx=10)
    left = 0.0
    for client, amount in sorted(per_client.items()):
        ax.barh(ys[2], amount, left=left, height=0.6, color=style.COLORS['mps'], zorder=3,
                edgecolor=style.SURFACE, linewidth=2)
        style.value_label(ax, left + amount / 2, ys[2], f'client {client}：{_gib(amount)}（上限 {_gib(quota_dual)}）',
                          color=style.INK, dx=0, ha='center', weight=600)
        left += amount
    style.value_label(ax, left, ys[2], f'合計 {_gib(left)}', dx=10, weight=600)
    ax.set_xlim(0, 13)
    ax.set_xticks(range(0, 13, 2))
    style.unit(ax, 'GiB')
    return fig


@register('04-isolation', 'mps-nvml')
def mps_nvml(data):
    groups = mps_cases(data)
    peaks = [_same(group, 'nvml_peak_used_mib') for group in groups]
    frees = [_same(group, 'nvml_min_free_mib') for group in groups]
    fig, ax = style.figure('配額 OOM 時整張 A40 仍有大量 VRAM',
                           subtitle='B 活躍期間的整卡取樣峰值與同一窗口的最少空閒量（MiB），三輪結果相同',
                           note='峰值包含 A 與 driver 開銷，不等於 B Pod 用量。',
                           height=3.8, value_axis='x', legend=True)
    ys = style.rows_axis(ax, ['single-process 4 GiB', 'single-process 8 GiB', 'dual-process 各 8 GiB'])
    ax.barh(ys, peaks, height=0.6, color=style.COLORS['mps'], zorder=3)
    ax.barh(ys, frees, left=peaks, height=0.6, color=style.NEUTRAL, zorder=3)
    for y, peak, free in zip(ys, peaks, frees):
        style.value_label(ax, peak, y, style.fmt(peak), dx=-6, ha='right', color=style.text_on(style.COLORS['mps']))
        style.value_label(ax, peak + free, y, style.fmt(free), dx=-6, ha='right', color=style.INK)
    style.header_legend(fig, [style.swatch(style.COLORS['mps']), style.swatch(style.NEUTRAL)],
                        ['整卡取樣峰值', '同一窗口的最少空閒'])
    ax.set_xlim(0, 50000)
    style.thousands(ax, 'x')
    style.unit(ax, 'MiB')
    return fig


@register('04-isolation', 'mps-talk')
def mps_talk(data):
    with (data / 'e2/mps-talk.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    fig, ax = style.figure('講者的 V100 案例：啟用 MPS 後吞吐量為 2.61 至 3.53 倍',
                           subtitle='吞吐量倍數 = 啟用 MPS ÷ 未啟用 MPS；條末數字為投影片所列倍數',
                           note='來源：吳磊（NVIDIA，2019-10-31），非本章 A40 實測。',
                           height=3.3, value_axis='x')
    ratios = [float(r['with_mps']) / float(r['without_mps']) for r in rows]
    ys = style.rows_axis(ax, [f'{r["case"]}，{r["processes"]} 個 process' for r in rows])
    style.hbars(ax, ys, ratios, style.COLORS['mps'], height=0.55,
                labels=[r['quoted_speedup'] + '×' for r in rows])
    style.ref_line(ax, 1, '未啟用 MPS = 1×')
    ax.set_xlim(0, 4.2)
    ax.set_xticks([0, 1, 2, 3, 4])
    style.unit(ax, '相對未啟用 MPS 的倍數')
    return fig


@register('04-isolation', 'accounting-balance')
def accounting_balance(data):
    rows = rounds(data, 'consumable-shares', 'accounting')
    if len(rows) != 3:
        raise ValueError('expected three consumable accounting rounds')
    remaining = _same(rows, 'schedulable_remaining_mib_before')
    physical = _same(rows, 'physical_free_mib_before')
    published, reserved = _same(rows, 'published_mib'), _same(rows, 'reserved_mib_before')
    fig, ax = style.figure(f'排程記帳只剩 {style.fmt(remaining)} MiB，整卡實體仍空閒 {style.fmt(physical)} MiB',
                           subtitle='16 GiB 與 24 GiB Claim 保留後，三輪結果相同（MiB）',
                           note=f'可排程餘額 = 公布 {style.fmt(published)} − 已保留 {style.fmt(reserved)}；'
                                '實體空閒量來自 NVML，未扣除 Claim 請求量。',
                           height=3.9, value_axis='x')
    ys = style.rows_axis(ax, ['ConsumableShares 可排程餘額', '整卡實體可用量（A40）'])
    ax.barh(ys[0], remaining, height=0.6, color=style.COLORS['consumable-shares'], zorder=3)
    ax.barh(ys[1], physical, height=0.6, color=style.COLORS['NVIDIA A40'], zorder=3)
    style.value_label(ax, remaining, ys[0], style.fmt(remaining), color=style.SURFACE, dx=-6, ha='right', weight=600)
    style.value_label(ax, physical, ys[1], style.fmt(physical), color=style.SURFACE, dx=-6, ha='right', weight=600)
    style.ref_line(ax, 8192, '第三份 Claim 申請 8 GiB（8,192 MiB）')
    ax.set_xlim(0, 50000)
    style.thousands(ax, 'x')
    style.unit(ax, 'MiB')
    return fig


@register('04-isolation', 'consumable-nvml')
def consumable_nvml(data):
    rows = sorted(rounds(data, 'consumable-shares', 'boundary'), key=lambda row: row['repeat'])
    tensor = _same(rows, 'b_max_tensor_gib')
    fig, ax = style.figure('ConsumableShares 超額後立即退出，取樣可能漏掉峰值',
                           subtitle=f'B 成功保留 {_gib(tensor)} GiB（宣告 8 GiB）後立即退出；每點為一輪的整卡取樣峰值（MiB）',
                           note='包含 A 與 driver 開銷，不等於 B Pod 用量。',
                           height=3.6, value_axis='x')
    ys = style.rows_axis(ax, [f'r{row["repeat"]}' for row in rows])
    for y, row in zip(ys, rows):
        value = row['nvml_peak_used_mib']
        style.row_dots(ax, y, [value], style.COLORS['consumable-shares'])
        style.value_label(ax, value, y, style.fmt(value), dx=10)
    ax.set_xlim(8000, 12000)
    style.thousands(ax, 'x')
    style.unit(ax, 'MiB')
    return fig


# Registering part 2 here keeps one discoverable module per chapter and lets tests use ch04.<builder>.
from tools.figures.ch04_hami import (  # noqa: E402,F401
    e2_accounting_comparison, e2_physical_comparison, e2_stability, e2_tensor_comparison,
    hami_runtime_boundary, hami_runtime_physical, hami_scope,
)
