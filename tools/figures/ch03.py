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
    x = [analysis.timestamp(row['utc']) - start for row in gpu]
    used = [float(row['used_mib']) for row in gpu]
    fig, ax = style.new_chart('hold-r1：VRAM 耗盡時 A 的新配置失敗', '', '整張 GPU 已用 VRAM（MiB）')
    fig.set_size_inches(9.2, 6)
    fig.subplots_adjust(left=0.15, right=0.97, top=0.91, bottom=0.37)
    ax.axvspan(0, hold, facecolor=style.COLORS['pressure'], alpha=0.04, linewidth=0)
    ax.axvspan(hold, released, facecolor=style.COLORS['pressure'], alpha=0.12, linewidth=0)
    ax.axvspan(released, x[-1], facecolor=style.COLORS['steady'], alpha=0.05, linewidth=0)
    for left, right, label in ((0, hold, 'B 成長'), (hold, released, 'B 保留'), (released, x[-1], 'B 開始釋放後')):
        ax.text((left + right) / 2, 0.95, label, transform=ax.get_xaxis_transform(),
                ha='center', va='center', color=style.INK, fontsize=10)
    ax.plot(x, used, color=style.COLORS['NVIDIA A40'], label='整張 GPU 已用 VRAM', linewidth=1.5)
    ax.legend(loc='upper left', frameon=False)
    ax.set_xlim(x[0], x[-1])
    ax.set_ylim(0, 51000)
    ax.set_yticks([0, 10000, 20000, 30000, 40000])
    ax.tick_params(axis='x', labelbottom=False)

    # A related child axis gives probes a separate short panel below the main
    # plot. It shares time, never the VRAM scale, and uses the same SVG exporter.
    probe_ax = ax.inset_axes([0, -0.54, 1, 0.27], sharex=ax)
    for event, symbol, label, level, entity in (
            ('A_PROBE_OK', 'o', 'OK', 1, 'steady'),
            ('A_PROBE_OOM', 'x', 'OOM', 0, 'pressure')):
        probes = [analysis.timestamp(row['utc']) - start for row in a if row.get('event') == event]
        probe_ax.scatter(probes, [level] * len(probes), marker=symbol, s=22,
                         color=style.COLORS[entity], label=label, linewidths=1 if symbol == 'x' else 0)
    probe_ax.set_ylim(-0.45, 1.45)
    probe_ax.set_yticks([0, 1], ['OOM', 'OK'])
    probe_ax.set_ylabel('A probe\n512 MiB', rotation=0, labelpad=35, va='center')
    probe_ax.set_xlabel('相對 B 開始成長的時間（秒）')
    probe_ax.grid(axis='y')
    probe_ax.legend(loc='lower right', bbox_to_anchor=(1, 1.12), ncol=2,
                    frameon=False, labelcolor=style.INK, borderaxespad=0)
    return fig


@register('03-oom', 'allocation-recovery')
def allocation_recovery(data_dir):
    summaries = analysis.load_summaries(data_dir / 'e1/raw')
    if len(summaries) != 6 or not all(row['complete'] for row in summaries):
        raise ValueError('expected six complete original OOM rounds')
    fig, ax = style.new_chart('六輪都觀察到 A 的新配置恢復成功', '首次觀察到配置成功的時間（秒）', '輪次')
    fig.set_size_inches(8, 4.5)
    positions = [6, 5, 4, 2, 1, 0]
    values = [row['allocation_recovery_seconds'] for row in summaries]
    ax.axhspan(3.5, 6.5, color=style.BASELINE, alpha=0.12, linewidth=0)
    ax.hlines(positions, 0, values, color=style.BASELINE, linewidth=0.6)
    ax.scatter(values, positions, color=style.COLORS['steady'], s=48)
    ax.set_yticks(positions, [row['trial'] for row in summaries])
    ax.set_xlim(0, 3.1)
    ax.set_ylim(-0.6, 6.6)
    ax.text(0.05, 3.1, 'exit 與 hold 的控制路徑不同，秒數不代表釋放耗時', fontsize=9, color=style.SECONDARY)
    return fig
