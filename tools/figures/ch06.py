"""GPU placement, retry allocation and separately sampled capacity evidence."""
import importlib.util
from pathlib import Path

from matplotlib.patches import Rectangle

from tools.figures import register, style

spec = importlib.util.spec_from_file_location('lab06_figures_analysis',
    Path(__file__).resolve().parents[2] / 'labs/06-heterogeneous/analysis/analyze.py')
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)

GPU_BY_NODE = {'dra-a40': 'NVIDIA A40', 'dra-4090': 'RTX 4090'}
STAGE_TITLES = {'stage1': 'stage1 · KFP Template 參照',
                'stage2': 'stage2 · 原生獨占選卡',
                'stage3': 'stage3 · HAMi 並行與容量'}
ACCEPTED_STATUS = '已驗收三輪'
CAPACITY_CASES = (('h1', 'H1'), ('h2', 'H2'), ('h3-h4', 'H3–H4'), ('h5', 'H5'))
NODE_ORDER = ('dra-4090', 'dra-a40')


def summary(data_dir):
    return analysis.analyze(data_dir / 'e4', data_dir / 'e4b')


@register('06-heterogeneous', 'e4-acceptance')
def acceptance_chart(data_dir):
    rows = summary(data_dir)['e4_acceptance']
    accepted = sum(1 for row in rows if row['status'] == ACCEPTED_STATUS)
    fig, ax = style.figure(f'E4 的 {accepted} 個情境各驗收三輪；保留未通過紀錄仍可見',
                           subtitle='每列為一個情境；條長為 trial 數（輪），虛線為驗收需要的 3 輪',
                           note='memory-30gi 為 unsupported，沒有正式執行的三輪證據。\n來源：data/e4/acceptance.json。',
                           height=5.6, legend=True, value_axis='x')
    labels, gaps, case_index = [], [], []
    previous = None
    for row in rows:
        if row['stage'] != previous:
            if previous is not None:
                gaps.append(len(labels) - 1)
            labels.append(STAGE_TITLES[row['stage']])
            previous = row['stage']
        case_index.append(len(labels))
        labels.append(row['case'])
    positions = style.rows_axis(ax, labels, gaps_after=tuple(gaps))
    for tick in ax.yaxis.get_major_ticks():
        if tick.label1.get_text() in STAGE_TITLES.values():
            tick.label1.set_fontweight(600)
    segments = (('positive_trials', style.OK, 'white'),
                ('negative_trials', style.PENDING, style.INK),
                ('retained_nonaccepted_trials', style.OOM, 'white'))
    for index, row in zip(case_index, rows):
        left = 0
        for field, color, text_color in segments:
            value = row[field]
            if not value:
                continue
            ax.barh(positions[index], value, left=left, height=0.64, color=color, zorder=3)
            ax.text(left + value / 2, positions[index], str(value), ha='center', va='center',
                    color=text_color, fontsize=style.LABEL_SIZE, fontweight=600, zorder=4)
            left += value
    ax.axvline(3, color=style.REFERENCE, linestyle='--', linewidth=1.2, zorder=4)
    style.value_label(ax, 3, positions[0], '驗收需要 3 輪', dx=6, color=style.REFERENCE)
    ax.set_xlim(0, 5.8)
    ax.set_xticks(range(6))
    style.unit(ax, '輪')
    style.header_legend(fig, [style.swatch(style.OK), style.swatch(style.PENDING), style.swatch(style.OOM)],
                        ['正向 CUDA 成功', '合理 Pending', '保留未通過'])
    return fig


@register('06-heterogeneous', 'e4-capacity-ranges')
def capacity_range_chart(data_dir):
    rows = summary(data_dir)['e4_capacity_ranges']
    fig, ax = style.figure('獨占 provider 沒有 DRA 剩餘量，不能用實體 free 代替',
                           subtitle='每組是一個節點與配置模式；橫條為取樣的最小至最大值（GiB）',
                           note='DRA remaining 來自 provider 公布的可扣帳容量，physical free 來自 NVML。\n兩者在附近五秒內配對，不是同一瞬間的讀取。',
                           height=4.4, legend=True, value_axis='x')
    series = (('schedulable_remaining_range_bytes', 0.2, style.COLORS['hami']),
              ('physical_free_range_bytes', -0.2, style.COLORS['exclusive']))
    centers = [-1.0 * index for index in range(len(rows))]
    ax.set_yticks(centers, [f"{row['node']}\n{row['allocation_mode']}" for row in rows])
    ax.set_ylim(min(centers) - 0.6, max(centers) + 0.6)
    for center, row in zip(centers, rows):
        for key, offset, color in series:
            interval = row[key]
            y = center + offset
            if interval is None:
                ax.text(0.3, y, 'DRA remaining 不適用', ha='left', va='center',
                        fontsize=style.LABEL_SIZE, color=style.SECONDARY)
                continue
            low, high = [value / 1024 ** 3 for value in interval]
            ax.hlines(y, low, high, colors=color, linewidth=6, zorder=3)
            ax.vlines([low, high], y - 0.1, y + 0.1, colors=color, linewidth=1.6, zorder=4)
            style.value_label(ax, high, y, f'{style.fmt(low, 1)}–{style.fmt(high, 1)}', dx=8)
    ax.set_xlim(0, 62)
    ax.set_xticks([0, 10, 20, 30, 40, 50])
    style.unit(ax, 'GiB')
    style.header_legend(fig, [style.line_handle(style.COLORS['hami']), style.line_handle(style.COLORS['exclusive'])],
                        ['DRA remaining（provider 公布）', 'physical free（NVML 取樣）'])
    return fig


@register('06-heterogeneous', 'placements')
def placement_chart(data_dir):
    rows = [r for r in summary(data_dir)['e4b_placements'] if r['case'] in ('h1', 'h2')]
    fig, ax = style.figure('H1 落在兩種 GPU；30Gi 的 H2 三輪都落在 A40',
                           subtitle='每格是該輪實際配置到的 GPU；列為案例與記憶體需求，欄為輪次',
                           note='RTX 4090 公布的 memory 小於 30Gi，因此 H2 只能配置到 A40。\n來源：data/e4b/task-results.json。',
                           height=3.3)
    gap = 0.04
    labels = []
    for row_index, case in enumerate(('h1', 'h2')):
        requested = next(r['requested_memory'] for r in rows if r['case'] == case)
        labels.append(f"{case.upper()} · {requested}")
        for repeat in (1, 2, 3):
            row = next(r for r in rows if r['case'] == case and r['repeat'] == repeat)
            gpu = GPU_BY_NODE[row['node']]
            column = repeat - 1
            ax.add_patch(Rectangle((column - 0.5 + gap, row_index - 0.5 + gap), 1 - 2 * gap, 1 - 2 * gap,
                                   facecolor=style.COLORS[gpu], edgecolor='none'))
            ax.text(column, row_index, style.LABELS[gpu], ha='center', va='center', color=style.text_on(style.COLORS[gpu]),
                    fontsize=style.BASE_SIZE, fontweight=600)
    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(1.5, -0.5)
    ax.set_xticks([0, 1, 2], ['r1', 'r2', 'r3'])
    ax.set_yticks([0, 1], labels)
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    return fig


@register('06-heterogeneous', 'pending-release')
def pending_chart(data_dir):
    rows = summary(data_dir)['pending_windows']
    if not all(r['verified'] for r in rows):
        raise ValueError('Pending recovery must retain the same Pod and Claim UID')
    pending = style.fmt(sum(r['pending_seconds'] for r in rows) / len(rows), 1)
    fig, ax = style.figure(f'H3–H4 觀測到 {pending} 秒 Pending，釋放後原 Claim 配置成功',
                           subtitle='自確認 Pending 起的時間（秒）；三輪的 Pod UID 與 Claim UID 都未改變',
                           note=f'{pending} 秒是確認 Pending 至送出 release 的觀測窗，不是釋放後的配置延遲。\n'
                                '配置時間是 watch 收到 allocation 的時間，為觀測上界。',
                           height=4.0)
    positions = style.rows_axis(ax, [f"r{r['repeat']}" for r in rows])
    ax.set_xlim(0, 44)
    ax.set_xticks(range(0, 45, 5))
    style.unit(ax, '秒')
    for index, (y, row) in enumerate(zip(positions, rows)):
        release, allocated = row['release_offset_seconds'], row['allocated_offset_seconds']
        ax.barh(y, release, height=0.4, color=style.PENDING, zorder=3)
        ax.barh(y, allocated - release, left=release, height=0.4, color=style.OK, zorder=3)
        style.value_label(ax, allocated, y, f'{style.fmt(allocated, 1)} 秒', dx=8)
        if index == 0:
            style.value_label(ax, release / 2, y, f'Pending {pending} 秒', dy=12, ha='center', va='bottom')
            style.value_label(ax, (release + allocated) / 2, y, '釋放到觀測到配置', dy=12, ha='center', va='bottom')
    return fig


@register('06-heterogeneous', 'capacity-physical')
def capacity_chart(data_dir):
    peak = summary(data_dir)['e4b_capacity_peak']
    selected = [r for r in peak if r['case'] in {case for case, _ in CAPACITY_CASES}]
    fig, ax = style.figure('DRA 剩餘額度與實體 free 不同；H5 剩餘額度為 0',
                           subtitle='每組是一個案例，每列是一張 GPU；實心方形為 DRA remaining，空心圓為 physical free',
                           note='只列表格中的代表樣本，依最高 reservation 選取（同值取最高 physical used）。\n兩者為非原子觀測，配對在附近五秒內。',
                           height=5.6, legend=True, value_axis='x')
    ticks, labels, plotted = [], [], []
    y = 0.0
    for case, title in CAPACITY_CASES:
        for index, node in enumerate(NODE_ORDER):
            row = next(r for r in selected if r['case'] == case and r['node'] == node)
            ticks.append(y)
            if index == 0:
                labels.append(f'{title} · {node}')
            else:
                labels.append(node)
            plotted.append((y, row))
            y -= 1.0
        y -= 0.6
    ax.set_yticks(ticks, labels)
    ax.set_ylim(min(ticks) - 0.6, max(ticks) + 0.6)
    for y, row in plotted:
        remaining, free = row['remaining_memory_mib'], row['physical_free_mib']
        ax.hlines(y, min(remaining, free), max(remaining, free), colors=style.BASELINE, linewidth=1.4, zorder=2)
        style.row_dots(ax, y, [free], style.COLORS['exclusive'], hollow=True, size=60, zorder=4)
        style.row_dots(ax, y, [remaining], style.COLORS['hami'], marker='s', size=50, zorder=5)
        style.value_label(ax, remaining, y, style.fmt(remaining), dy=-7, ha='center', va='top')
        style.value_label(ax, free, y, style.fmt(free), dy=7, ha='center', va='bottom')
    ax.set_xlim(-1500, 49000)
    ax.set_xticks([0, 10000, 20000, 30000, 40000])
    style.thousands(ax, 'x')
    style.unit(ax, 'MiB')
    style.header_legend(fig, [style.dot(style.COLORS['hami'], marker='s'),
                              style.dot(style.COLORS['exclusive'], hollow=True)],
                        ['DRA remaining（剩餘額度）', 'physical free（NVML 實體可用量）'])
    return fig
