"""E3 speedup, latency trade-offs and paired MPS/TimeSlicing ratios."""
import importlib.util
from pathlib import Path

from matplotlib.lines import Line2D
from matplotlib.transforms import Bbox
import numpy as np

from tools.figures import register, style

spec = importlib.util.spec_from_file_location('lab05_figures_analysis',
    Path(__file__).resolve().parents[2] / 'labs/05-performance/analysis/analyze.py')
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)

# Exclusive and sequential are the baseline (speedup 1.0), so only the shared modes get lines.
SHARED = ('timeslicing', 'mps', 'hami')
WORKLOAD_NAMES = {'continuous': '持續工作', 'bursty-aligned': '同步間歇工作', 'bursty-staggered': '錯開間歇工作'}
SPEEDUP_X = {1: 0, 2: 1, 4: 2}          # N as equally spaced categories
TRADEOFF_XLIM = {'continuous': (30, 320), 'bursty-aligned': (30, 122), 'bursty-staggered': (30, 122)}
TRADEOFF_YLIM = (0.016, 0.0585)
# Candidate offsets (points) for an N label, tried in order; the first one that avoids every mark wins.
COUNT_OFFSETS = ((0, 9, 'center', 'bottom'), (0, -9, 'center', 'top'), (8, 7, 'left', 'bottom'),
                 (8, -7, 'left', 'top'), (-8, 7, 'right', 'bottom'), (-8, -7, 'right', 'top'),
                 (10, 0, 'left', 'center'), (-10, 0, 'right', 'center'))


def groups(data_dir):
    rows = analysis.load_summary(data_dir / 'e3/summary.csv')
    if len(rows) != 180 or not all(r['valid'] for r in rows):
        raise ValueError('expected 180 valid original batches')
    values = analysis.aggregate(rows)
    if any(g['repeats'] != 5 for g in values):
        raise ValueError('expected five blocks per condition')
    return rows, values


def _series(values, workload, mode):
    """The aggregates of one mode in one workload, ordered by N (1, 2, 4)."""
    return sorted((g for g in values if g['workload_id'] == workload and g['mode'] == mode),
                  key=lambda g: g['concurrency'])


def _shared_reference(fig, axes, value):
    """One dashed reference line across all panels; they share one y scale, so one line serves every panel.

    It sits below the panel content (transparent panel backgrounds) so that data and knocked-out labels cover it.
    """
    fig.canvas.draw()
    first, last = axes[0], axes[-1]
    low, high = first.get_ylim()
    box = first.get_position()
    y = box.y0 + (value - low) / (high - low) * box.height
    for ax in axes:
        ax.patch.set_visible(False)
    fig.add_artist(Line2D([first.get_position().x0, last.get_position().x1], [y, y], transform=fig.transFigure,
                          color=style.REFERENCE, linewidth=1.2, linestyle='--', zorder=-1))


def _knock_out(labels):
    """Give direct labels a white box so a reference line or gridline passes behind them."""
    for label in labels:
        label.set_bbox(dict(boxstyle='square,pad=0.12', facecolor=style.SURFACE, edgecolor='none'))


def _freeze_layout(fig):
    """Pin the solved panel boxes to a 1e-6 grid and stop the layout engine.

    The constrained-layout solver leaves float noise in the last bits that differs between processes, and matplotlib
    writes those floats into the clip-path ids of the SVG. Rounding them keeps repeated renders byte-identical.
    """
    fig.canvas.draw()
    for ax in fig.axes:
        ax.set_position([round(value, 6) for value in ax.get_position().bounds])
    fig.set_layout_engine('none')


def _count_labels(ax, points, paths, name):
    """Write N=k once per cluster of coincident points, at the first offset that clears marks, lines and text.

    points: (x, y, N) in data coordinates; paths: one list of (x, y) per mode, in N order; name: panel name.
    """
    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    panel = ax.get_window_extent(renderer)
    avoid = [text.get_window_extent(renderer) for text in ax.texts]
    for x, y, _ in points:
        px, py = ax.transData.transform((x, y))
        avoid.append(Bbox.from_extents(px - 6, py - 6, px + 6, py + 6))
    for path in paths:
        vertices = ax.transData.transform(path)
        for (x0, y0), (x1, y1) in zip(vertices, vertices[1:]):
            for t in np.linspace(0, 1, 40):
                px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
                avoid.append(Bbox.from_extents(px - 2, py - 2, px + 2, py + 2))
    for n in sorted({n for _, _, n in points}):
        clusters = []
        for x, y, count in points:
            if count != n:
                continue
            px, py = ax.transData.transform((x, y))
            if all((px - cx) ** 2 + (py - cy) ** 2 > 12 ** 2 for cx, cy, _, _ in clusters):
                clusters.append((px, py, x, y))
        for _, _, x, y in clusters:
            for dx, dy, ha, va in COUNT_OFFSETS:
                label = ax.annotate(f'N={n}', xy=(x, y), xytext=(dx, dy), textcoords='offset points', ha=ha,
                                    va=va, fontsize=style.LABEL_SIZE, color=style.SECONDARY)
                box = label.get_window_extent(renderer)
                inside = (box.x0 >= panel.x0 and box.x1 <= panel.x1 and box.y0 >= panel.y0 and box.y1 <= panel.y1)
                if inside and not any(box.overlaps(other) for other in avoid):
                    avoid.append(box)
                    break
                label.remove()
            else:
                raise ValueError(f'no free place for N={n} in panel {name!r}')


@register('05-performance', 'speedup')
def speedup(data_dir):
    _, values = groups(data_dir)
    fig, axes = style.figure('共用只在間歇工作提高吞吐量，持續工作仍接近或低於基準',
                             subtitle='相對同 block 基準的 Speedup（倍）；點為五個 block 的平均',
                             note='基準 1.0 為獨占（N=1）與逐個執行（N=2、4）；N 為同批 Task 數，三個 N 等距排列。',
                             height=4.4, ncols=3, sharey=True)
    ends_by_axis = []
    for ax, workload in zip(axes, analysis.WORKLOADS):
        ax.set_title(WORKLOAD_NAMES[workload])
        ends = []
        for mode in SHARED:
            color = style.COLORS[mode]
            series = _series(values, workload, mode)
            xs = [SPEEDUP_X[g['concurrency']] for g in series]
            ys = [g['speedup_vs_sequential'] for g in series]
            ax.plot(xs, ys, color=color, linewidth=2, marker='o', markersize=6, markeredgecolor=style.SURFACE,
                    zorder=4)
            ends.append((xs[-1], ys[-1], style.LABELS[mode], color))
        ax.set_xticks([0, 1, 2], ['N=1', 'N=2', 'N=4'])
        ax.set(xlim=(-0.3, 3.8), ylim=(0.85, 2.5))
        ax.set_yticks([1.0, 1.5, 2.0, 2.5])
        style.thousands(ax, 'y', decimals=1)
        ends_by_axis.append(ends)
    fig.canvas.draw()  # end_labels measures the panel height, so the final layout must exist first
    for ax, ends in zip(axes, ends_by_axis):
        _knock_out(style.end_labels(ax, ends))
    _shared_reference(fig, axes, 1.0)
    axes[-1].annotate('基準', xy=(3.45, 1.0), xytext=(0, 4), textcoords='offset points', ha='center', va='bottom',
                      fontsize=style.LABEL_SIZE, color=style.SECONDARY)
    _freeze_layout(fig)
    return fig


@register('05-performance', 'tradeoff')
def tradeoff(data_dir):
    _, values = groups(data_dir)
    fig, axes = style.figure('增加 N 會拉長 Task 延遲；間歇工作可換得較高吞吐量',
                             subtitle='縱軸為吞吐量（Task/s），橫軸為平均 Task 延遲（秒）；點為五個 block 的平均\n'
                                      '線依 N = 1、2、4 的順序連接',
                             note='三個面板的橫軸範圍不同，持續工作的範圍較寬；縱軸三個面板共用。',
                             height=5.0, ncols=3, sharey=True)
    panels = []
    for ax, workload in zip(axes, analysis.WORKLOADS):
        ax.set_title(WORKLOAD_NAMES[workload])
        ends, points, paths = [], [], []
        for mode in SHARED:
            color = style.COLORS[mode]
            series = _series(values, workload, mode)
            xs = [g['mean_task_latency'] for g in series]
            ys = [g['throughput_tasks_per_second'] for g in series]
            ax.plot(xs, ys, color=color, linewidth=2, marker='o', markersize=6, markeredgecolor=style.SURFACE,
                    zorder=4)
            ends.append((xs[-1], ys[-1], style.LABELS[mode], color))
            points += [(x, y, g['concurrency']) for x, y, g in zip(xs, ys, series)]
            paths.append(list(zip(xs, ys)))
        ax.set(xlim=TRADEOFF_XLIM[workload], ylim=TRADEOFF_YLIM)
        ax.set_yticks([0.02, 0.03, 0.04, 0.05])
        ax.set_xlabel('平均延遲（秒）', loc='right', fontsize=style.LABEL_SIZE, color=style.SECONDARY, labelpad=6)
        style.thousands(ax, 'y', decimals=3)
        panels.append((ax, WORKLOAD_NAMES[workload], ends, points, paths))
    fig.canvas.draw()  # labels are measured against the final layout, so draw once before placing them
    for ax, name, ends, points, paths in panels:
        _knock_out(style.end_labels(ax, ends))
        _count_labels(ax, points, paths, name)
    _freeze_layout(fig)
    return fig


@register('05-performance', 'mps-timeslicing-ratio')
def ratio_chart(data_dir):
    rows, _ = groups(data_dir)
    ratios = {(r['workload_id'], r['concurrency']): r['ratio'] for r in analysis.paired_ratios(rows)}
    fig, ax = style.figure('同步間歇的 N=2，MPS 吞吐量是 TimeSlicing 的 1.454 倍',
                           subtitle='MPS / TimeSlicing 的 Task/s 比值；點為五個 block 配對後的平均，1.0 表示相同',
                           note='資料來源：E3 的 180 個有效批次；每個比值先在同一 block 內配對，再平均五個 block。',
                           height=5.0, value_axis='x')
    headers = [WORKLOAD_NAMES[workload] for workload in analysis.WORKLOADS]
    labels, gaps = [], []
    for header in headers:
        labels += [header, 'N=1', 'N=2', 'N=4']
        gaps.append(len(labels) - 1)
    ys = style.rows_axis(ax, labels, gaps_after=gaps[:-1])
    for label in ax.get_yticklabels():
        if label.get_text() in headers:
            label.set_fontweight(600)
    for block, workload in enumerate(analysis.WORKLOADS):
        for offset, n in enumerate((1, 2, 4)):
            y, value = ys[block * 4 + 1 + offset], ratios[(workload, n)]
            style.row_dots(ax, y, [value], style.COLORS['mps'])
            style.value_label(ax, value, y, style.fmt(value, 3), dx=10)
    ax.set_xlim(0.97, 1.56)
    ax.set_xticks([1.0, 1.1, 1.2, 1.3, 1.4, 1.5])
    style.thousands(ax, 'x', decimals=1)
    style.unit(ax, '倍')
    style.ref_line(ax, 1.0, '相同')
    _freeze_layout(fig)
    return fig
