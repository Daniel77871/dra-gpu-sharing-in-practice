"""A40 OOM evidence: physical usage, probe outcomes and observed recovery."""
import importlib.util
from pathlib import Path

from tools.figures import register
from tools.figures import style

ANALYSIS_PATH = Path(__file__).resolve().parents[2] / 'labs/03-oom/analysis/analyze.py'
spec = importlib.util.spec_from_file_location('lab03_analyze_figures', ANALYSIS_PATH)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)
TIMELINE_TRIAL = 'hold-r1'
MIB_PER_GIB = 1024


@register('03-oom', 'hold-timeline')
def hold_timeline(data_dir):
    root = data_dir / 'e1/raw' / TIMELINE_TRIAL
    a = analysis.rows(root / 'a.jsonl')
    b = analysis.rows(root / 'b.jsonl')
    gpu = analysis.rows(root / 'gpu.jsonl')
    markers = {event: next(row for row in b if row.get('event') == event)
               for event in ('B_GROW', 'B_HOLD', 'B_EXIT')}
    start = analysis.timestamp(markers['B_GROW']['utc'])
    hold = analysis.timestamp(markers['B_HOLD']['utc']) - start
    released = analysis.timestamp(markers['B_EXIT']['utc']) - start
    probes = {event: [analysis.timestamp(row['utc']) - start for row in a if row.get('event') == event]
              for event in ('A_PROBE_OK', 'A_PROBE_OOM')}
    in_hold_oom = [t for t in probes['A_PROBE_OOM'] if hold <= t < released]
    if [t for t in probes['A_PROBE_OK'] if hold <= t < released]:
        raise ValueError('the hold window must contain only OOM probes for this title')
    x = [analysis.timestamp(row['utc']) - start for row in gpu]
    used_gib = [float(row['used_mib']) / MIB_PER_GIB for row in gpu]

    fig, (top, bottom) = style.figure(
        f'B 保留 VRAM 時，A 的新配置 {len(in_hold_oom)} 次全部 OOM',
        subtitle='hold-r1 單輪；上圖為整張 GPU 已用 VRAM，下圖為 A 的 512 MiB 配置 probe 結果',
        note='來源：data/e1/raw/hold-r1。t = 0 為 B 開始成長；\n區段界線取自 B_GROW、B_HOLD、B_EXIT。',
        height=5.0, nrows=2, sharex=True, height_ratios=[3, 1])

    stages = ((0, hold, 'B 成長', 0.10), (hold, released, 'B 保留', 0.24), (released, x[-1], 'B 釋放後', 0.10))
    for panel in (top, bottom):
        for left, right, _, alpha in stages:
            panel.axvspan(left, right, color=style.NEUTRAL, alpha=alpha, linewidth=0, zorder=0)
    for left, right, label, _ in stages:
        top.text((left + right) / 2, 0.96, label, transform=top.get_xaxis_transform(),
                 ha='center', va='top', fontsize=style.LABEL_SIZE, color=style.INK)
    top.text(x[0] / 2, 0.96, 'B 成長前', transform=top.get_xaxis_transform(),
             ha='center', va='top', fontsize=style.LABEL_SIZE, color=style.SECONDARY)

    top.plot(x, used_gib, color=style.COLORS['NVIDIA A40'], linewidth=1.6, zorder=3)
    top.set_title('整張 GPU 已用 VRAM', loc='left', pad=20)
    top.set_xlim(x[0], x[-1])
    top.set_ylim(0, 56)
    top.set_yticks([0, 10, 20, 30, 40])
    style.unit(top, 'GiB')
    top.axvline(0, color=style.REFERENCE, linestyle='--', linewidth=1.0, zorder=2)
    top.annotate('t = 0', xy=(0, 1), xycoords=('data', 'axes fraction'), xytext=(-5, -4),
                 textcoords='offset points', ha='right', va='top', fontsize=style.LABEL_SIZE, color=style.INK)

    style.set_value_axis(bottom, 'x')
    bottom.spines['left'].set_visible(False)
    bottom.set_title('A 的 512 MiB 配置 probe', loc='left')
    for event, label, level, color in (('A_PROBE_OK', 'OK', 1, style.COLORS['ok']),
                                       ('A_PROBE_OOM', 'OOM', 0, style.COLORS['oom'])):
        times = probes[event]
        bottom.scatter(times, [level] * len(times), marker='|', s=150, linewidths=1.8,
                       color=color, label=label, zorder=3)
    bottom.axvline(0, color=style.REFERENCE, linestyle='--', linewidth=1.0, zorder=2)
    bottom.set_ylim(-0.6, 1.6)
    bottom.set_yticks([0, 1], ['OOM', 'OK'])
    bottom.set_xlim(x[0], x[-1])
    bottom.set_xticks([-60, -30, 0, 30, 60, 90, 120])
    style.unit(bottom, '秒')
    return fig


@register('03-oom', 'allocation-recovery')
def allocation_recovery(data_dir):
    summaries = analysis.load_summaries(data_dir / 'e1/raw')
    if len(summaries) != 6 or not all(row['complete'] for row in summaries):
        raise ValueError('expected six complete original OOM rounds')
    fig, ax = style.figure('六輪都在 3 秒內觀察到 A 的新配置成功',
                           subtitle='自 B 結束起算，首次觀察到 A 的 512 MiB 配置成功的時間（秒）',
                           note='exit 與 hold 的控制路徑不同，秒數不代表釋放耗時。\n來源：data/e1/raw 六輪的重算結果。',
                           height=4.4, value_axis='x')
    ticks, labels, points = [], [], []
    y = 0.0
    for mode, title in (('exit', 'exit 模式'), ('hold', 'hold 模式')):
        ticks.append(y)
        labels.append(title)
        y -= 1.0
        for row in sorted((r for r in summaries if r['mode'] == mode), key=lambda r: r['repeat']):
            ticks.append(y)
            labels.append(f"r{row['repeat']}")
            points.append((y, row['allocation_recovery_seconds']))
            y -= 1.0
        y -= 0.6
    ax.set_yticks(ticks, labels)
    ax.set_ylim(min(ticks) - 0.6, max(ticks) + 0.6)
    ax.set_xlim(0, 3.2)
    ax.set_xticks([0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0], ['0', '0.5', '1.0', '1.5', '2.0', '2.5', '3.0'])
    for y, value in points:
        style.row_dots(ax, y, [value], style.OK, size=64)
        style.value_label(ax, value, y, f'{style.fmt(value, 2)} 秒', dx=10)
    for tick in ax.yaxis.get_major_ticks():
        if tick.label1.get_text() in ('exit 模式', 'hold 模式'):
            tick.label1.set_fontweight(600)
    style.unit(ax, '秒')
    return fig
