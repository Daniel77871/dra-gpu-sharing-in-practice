"""GPU placement, retry allocation and separately sampled capacity evidence."""
import importlib.util
from pathlib import Path

from tools.figures import register, style

spec = importlib.util.spec_from_file_location('lab06_figures_analysis',
    Path(__file__).resolve().parents[2] / 'labs/06-heterogeneous/analysis/analyze.py')
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def summary(data_dir):
    return analysis.analyze(data_dir / 'e4', data_dir / 'e4b')


@register('06-heterogeneous', 'e4-acceptance')
def acceptance_chart(data_dir):
    rows = summary(data_dir)['e4_acceptance']
    fig, ax = style.new_chart('E4 保留失敗紀錄，13 個情境各驗收三輪', 'trial 數（輪）', '')
    fig.set_size_inches(9, 6.8)
    y = list(range(len(rows) - 1, -1, -1))
    left = [0] * len(rows)
    for field, entity, label in [('positive_trials', 'steady', '正向 CUDA'),
                                  ('negative_trials', 'consumable-shares', '合理 Pending'),
                                  ('retained_nonaccepted_trials', 'pressure', '保留未通過')]:
        values = [r[field] for r in rows]
        ax.barh(y, values, left=left, color=style.COLORS[entity], label=label, height=.7)
        left = [a + b for a, b in zip(left, values)]
    ax.set_yticks(y, [r['stage'] + ' / ' + r['case'] for r in rows])
    ax.set_xticks(range(6))
    ax.set_xlim(0, 5.8)
    return fig


@register('06-heterogeneous', 'e4-capacity-ranges')
def capacity_range_chart(data_dir):
    rows = summary(data_dir)['e4_capacity_ranges']
    fig, ax = style.new_chart('E4 的 exclusive provider 沒有可扣帳的剩餘容量', 'GPU 記憶體（GiB）', '')
    fig.set_size_inches(9, 4.5)
    for i, row in enumerate(rows):
        y = len(rows) - 1 - i
        for key, offset, color, label in [
            ('physical_free_range_bytes', -.14, style.COLORS['exclusive'], 'Physical free'),
            ('schedulable_remaining_range_bytes', .14, style.COLORS['hami'], 'DRA remaining')]:
            interval = row[key]
            if interval is None:
                ax.text(1, y + offset, 'DRA remaining：unavailable', color=style.SECONDARY, va='center', fontsize=9)
                continue
            low, high = [v / 1024 ** 3 for v in interval]
            ax.plot([low, high], [y + offset, y + offset], color=color, marker='|',
                    markersize=10, linewidth=3, label=label)
    ax.set_yticks(list(range(len(rows) - 1, -1, -1)),
                  [r['node'] + '\n' + r['allocation_mode'] for r in rows])
    ax.set_xlim(0, 48)
    ax.set_ylim(-.6, len(rows) - .3)
    return fig


@register('06-heterogeneous', 'placements')
def placement_chart(data_dir):
    rows = [r for r in summary(data_dir)['e4b_placements'] if r['case'] in ('h1', 'h2')]
    fig, ax = style.new_chart('H1 實際分配到兩種 GPU；30Gi 的 H2 都落在 A40', '情境與輪次', '配置節點')
    fig.set_size_inches(9, 3.8)
    colors = {'dra-a40': style.COLORS['NVIDIA A40'], 'dra-4090': style.COLORS['RTX 4090']}
    y = {'dra-a40': 1, 'dra-4090': 0}
    ax.scatter(list(range(len(rows))), [y[r['node']] for r in rows],
               color=[colors[r['node']] for r in rows], s=120, edgecolors=style.SURFACE)
    ax.set_xticks(list(range(len(rows))), [r['case'].upper() + f" r{r['repeat']}" for r in rows])
    ax.set_yticks([0, 1], ['dra-4090', 'dra-a40'])
    ax.axvline(2.5, color=style.BASELINE, linewidth=.8)
    ax.set_xlim(-.5, 5.5)
    ax.set_ylim(-.5, 1.5)
    return fig


@register('06-heterogeneous', 'pending-release')
def pending_chart(data_dir):
    rows = summary(data_dir)['pending_windows']
    if not all(r['verified'] for r in rows):
        raise ValueError('Pending recovery must retain the same Pod and Claim UID')
    fig, ax = style.new_chart('H3–H4 觀測到 34.0 秒 Pending，釋放後原 Claim 配置成功',
                              '自確認 Pending 起的時間（秒）', '輪次')
    fig.set_size_inches(9, 4.1)
    for i, row in enumerate(rows):
        y = len(rows) - 1 - i
        release, allocated = row['release_offset_seconds'], row['allocated_offset_seconds']
        ax.plot([0, release], [y, y], color=style.COLORS['pressure'], linewidth=4, label='確認 Pending 至發出釋放')
        ax.plot([release, allocated], [y, y], color=style.COLORS['consumable-shares'], linewidth=4,
                label='釋放至觀測到配置')
        ax.scatter([release], [y], color=style.SECONDARY, marker='x', s=64)
        ax.scatter([allocated], [y], color=style.COLORS['steady'], s=64, label='觀測到 allocation')
    ax.set_yticks(list(range(len(rows) - 1, -1, -1)), [f"r{r['repeat']}" for r in rows])
    ax.set_xlim(0, 45)
    ax.set_ylim(-.6, len(rows) - .25)
    handles, labels = ax.get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    fig.legend(unique.values(), unique.keys(), loc='upper center', bbox_to_anchor=(.5, -.01),
               ncol=3, frameon=False, labelcolor=style.INK, fontsize=9)
    return fig


@register('06-heterogeneous', 'capacity-physical')
def capacity_chart(data_dir):
    rows = summary(data_dir)['e4b_capacity_peak']
    fig, ax = style.new_chart('E4B 的預留額度與實體空間不同；H5 剩餘額度為零仍有 VRAM',
                              'GPU 記憶體（MiB）', '')
    fig.set_size_inches(10, 8.5)
    y = list(range(len(rows) - 1, -1, -1))
    remaining = [r['remaining_memory_mib'] for r in rows]
    free = [r['physical_free_mib'] for r in rows]
    for at, left, right in zip(y, remaining, free):
        ax.plot([left, right], [at, at], color=style.BASELINE, linewidth=1)
    ax.scatter(remaining, y, color=style.COLORS['hami'], marker='s', s=38, label='DRA remaining')
    ax.scatter(free, y, color=style.COLORS['exclusive'], marker='o', s=38, label='Physical free')
    ax.set_yticks(y, [r['case'] + ' / ' + r['node'] for r in rows])
    ax.set_xlim(-1000, 49000)
    return fig
