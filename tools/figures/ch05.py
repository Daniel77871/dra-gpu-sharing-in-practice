"""E3 speedup, latency trade-offs and paired MPS/TimeSlicing ratios."""
import importlib.util
from pathlib import Path

from tools.figures import register, style
from matplotlib.lines import Line2D

spec = importlib.util.spec_from_file_location('lab05_figures_analysis',
    Path(__file__).resolve().parents[2] / 'labs/05-performance/analysis/analyze.py')
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)
MARKERS = {1: 'o', 2: '^', 4: 's'}
MODE_NAMES = {'exclusive': 'Exclusive', 'sequential': 'Sequential',
              'timeslicing': 'TimeSlicing', 'mps': 'MPS', 'hami': 'HAMi'}


def groups(data_dir):
    rows = analysis.load_summary(data_dir / 'e3/summary.csv')
    if len(rows) != 180 or not all(r['valid'] for r in rows):
        raise ValueError('expected 180 valid original batches')
    values = analysis.aggregate(rows)
    if any(g['repeats'] != 5 for g in values):
        raise ValueError('expected five blocks per condition')
    return rows, values


def panels(title, ylabel):
    style.configure()
    fig, axes = style.plt.subplots(1, 3, sharey=True, figsize=(12, 4.6))
    fig.suptitle(title, fontsize=13)
    axes[0].set_ylabel(ylabel)
    for ax, workload in zip(axes, analysis.WORKLOADS):
        ax.set_title(workload)
    modes = [Line2D([], [], color=style.COLORS[mode], marker='o',
                    label=MODE_NAMES[mode]) for mode in analysis.MODES]
    fig.legend(handles=modes, loc='upper center', bbox_to_anchor=(.5, -.01),
               ncol=5, frameon=False, labelcolor=style.INK)
    counts = [Line2D([], [], color=style.INK, marker=marker, linestyle='None',
                     label=f'N={n}') for n, marker in MARKERS.items()]
    fig.legend(handles=counts, loc='upper center', bbox_to_anchor=(.5, -.08),
               ncol=3, frameon=False, labelcolor=style.SECONDARY, fontsize=9)
    return fig, axes


@register('05-performance', 'speedup')
def speedup(data_dir):
    _, values = groups(data_dir)
    fig, axes = panels('共用只在間歇工作提高吞吐量，持續工作仍接近或低於基準',
                       '相對同 block 基準的 Speedup（倍）')
    x = {1: 0, 2: 1, 4: 2}
    offsets = {'exclusive': -.16, 'sequential': -.08, 'timeslicing': 0, 'mps': .08, 'hami': .16}
    for ax, workload in zip(axes, analysis.WORKLOADS):
        ax.axhline(1, color=style.BASELINE, linewidth=.8)
        for mode in analysis.MODES:
            for g in values:
                if g['workload_id'] != workload or g['mode'] != mode:
                    continue
                mean = g['speedup_vs_sequential']
                ax.errorbar(x[g['concurrency']] + offsets[mode], mean,
                            yerr=[[mean - g['speedup_min']], [g['speedup_max'] - mean]],
                            fmt=MARKERS[g['concurrency']], capsize=3, color=style.COLORS[mode],
                            markersize=6, linewidth=1.2)
        ax.set_xticks([0, 1, 2], ['1', '2', '4'])
        ax.set(xlabel='同批 Task 數 N', xlim=(-.5, 2.5), ylim=(.8, 2.75))
    return fig


@register('05-performance', 'tradeoff')
def tradeoff(data_dir):
    _, values = groups(data_dir)
    fig, axes = panels('增加 N 會拉長 Task 延遲；間歇工作可換得較高吞吐量',
                       '吞吐量（Task/s）')
    for ax, workload in zip(axes, analysis.WORKLOADS):
        for mode in analysis.MODES:
            data = [g for g in values if g['workload_id'] == workload and g['mode'] == mode]
            if len(data) > 1:
                ax.plot([g['mean_task_latency'] for g in data], [g['throughput_tasks_per_second'] for g in data],
                        color=style.COLORS[mode], alpha=.4, linewidth=1)
            for g in data:
                ax.scatter(g['mean_task_latency'], g['throughput_tasks_per_second'],
                           marker=MARKERS[g['concurrency']], color=style.COLORS[mode], s=54,
                           edgecolor=style.SURFACE, linewidth=.8)
        ax.set(xlabel='平均 Task 延遲（秒）', ylim=(.0185, .06),
               xlim=(35, 205) if workload == 'continuous' else (40, 100))
    return fig


@register('05-performance', 'mps-timeslicing-ratio')
def ratio_chart(data_dir):
    rows, _ = groups(data_dir)
    values = analysis.paired_ratios(rows)
    fig, ax = style.new_chart('同步間歇的 N=2，MPS 吞吐量是 TimeSlicing 的 1.454 倍',
                              'MPS / TimeSlicing 吞吐量（倍）', '')
    fig.set_size_inches(9, 5.3)
    y = list(range(len(values) - 1, -1, -1))
    ax.hlines(y, 1, [r['ratio'] for r in values], color=style.COLORS['mps'], linewidth=2)
    ax.scatter([r['ratio'] for r in values], y, color=style.COLORS['mps'], s=40)
    ax.axvline(1, color=style.BASELINE, linewidth=.8)
    ax.set_yticks(y, [r['workload_id'] + f"　N={r['concurrency']}" for r in values])
    ax.set_xlim(.97, 1.52)
    ax.set_ylim(-.7, 8.7)
    return fig
