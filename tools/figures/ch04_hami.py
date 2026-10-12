"""Isolation charts, part 2: HAMi-DRA runtime and scope, and the three-method comparisons."""
from tools.figures import register, style
from tools.figures.e2data import comparison_cases, hami_analysis, hami_rounds, rounds


def _gib(value):
    """Short GiB text without trailing zeros: 7.5, 7.25, 12."""
    return style.fmt(value, 2).rstrip('0').rstrip('.')


def _range_text(values):
    """One number when the rounds agree, otherwise the low-high range."""
    low, high = min(values), max(values)
    return style.fmt(low) if low == high else f'{style.fmt(low)}–{style.fmt(high)}'


def _freeze_layout(fig):
    """Pin the constrained layout to positions rounded to 1e-9 so every build writes identical SVG clip paths.

    The solver's last float digits vary between processes, and save_svg hashes unrounded clip boxes into ids.
    """
    fig.canvas.draw()
    for ax in fig.axes:
        ax.set_position([round(value, 9) for value in ax.get_position().bounds])
    fig.set_layout_engine('none')
    return fig


E2_TWO_LINE = {
    'ConsumableShares single-process': 'ConsumableShares\nsingle-process',
    'HAMi-DRA single-process': 'HAMi-DRA\nsingle-process',
    'HAMi-DRA dual-process': 'HAMi-DRA\ndual-process',
    'MPS single-process 4 GiB': 'MPS 4 GiB\nsingle-process',
    'MPS single-process 8 GiB': 'MPS 8 GiB\nsingle-process',
    'MPS dual-process': 'MPS\ndual-process',
}


@register('04-isolation', 'hami-runtime-boundary')
def hami_runtime_boundary(data):
    rows = hami_rounds(data, 'boundary') + hami_rounds(data, 'scope')
    quota = rows[0]['quota_gib']
    if any(row['b_max_tensor_gib'] >= quota for row in rows):
        raise ValueError('boundary title requires every B tensor total below the shared quota')
    fig, ax = style.figure(f'HAMi-DRA 成功保留的 tensor 合計都低於 {_gib(quota)} GiB 配額',
                           subtitle='B 成功保留的 tensor 合計（GiB）；每輪 B 都遇到真實 CUDA 配置 OOM',
                           height=3.9)
    labels = [f'{"single-process" if r["case"] == "boundary" else "dual-process"} r{r["repeat"]}' for r in rows]
    ys = style.rows_axis(ax, labels, gaps_after=(2,))
    for y, row in zip(ys, rows):
        value = row['b_max_tensor_gib']
        style.row_dots(ax, y, [value], style.COLORS['hami'])
        style.value_label(ax, value, y, _gib(value), dx=10)
    style.ref_line(ax, quota, f'共用 Claim 配額 {style.fmt(quota)} GiB')
    ax.set_xlim(6.5, 8.5)
    ax.set_xticks([6.5, 7, 7.5, 8, 8.5])
    style.thousands(ax, 'x', decimals=1)
    style.unit(ax, 'GiB')
    return _freeze_layout(fig)


@register('04-isolation', 'hami-runtime-physical')
def hami_runtime_physical(data):
    rows = hami_rounds(data, 'boundary') + hami_rounds(data, 'scope')
    quota = rows[0]['quota_gib'] * 1024
    fig, ax = style.figure('HAMi-DRA dual-process 有兩輪實體用量超過配額',
                           subtitle='共同保留期的 B process 實體用量（MiB，NVML 快照，不含 A）',
                           height=3.9)
    labels = [f'{"single-process" if r["case"] == "boundary" else "dual-process"} r{r["repeat"]}' for r in rows]
    ys = style.rows_axis(ax, labels, gaps_after=(2,))
    for y, row in zip(ys, rows):
        used = row['b_nvml_used_mib']
        over = used > quota
        style.row_dots(ax, y, [used], style.OOM if over else style.COLORS['hami'])
        style.value_label(ax, used, y, style.fmt(used) + ('，超過配額' if over else ''),
                          color=style.OOM if over else style.INK)
    style.ref_line(ax, quota, f'Claim 配額 {style.fmt(quota)}')
    ax.set_xlim(7800, 8700)
    ax.set_xticks([7800, 8000, 8200, 8400, 8600])
    style.thousands(ax, 'x')
    style.unit(ax, 'MiB')
    return fig


@register('04-isolation', 'hami-scope')
def hami_scope(data):
    rows = hami_analysis.analyze_run(data / 'e2b/raw')
    if len(rows) != 9 or sum(r['complete'] for r in rows) != 8:
        raise ValueError('scope chart requires eight completed and one stalled original round')
    cases = [(case, [r for r in rows if r['case'] == case]) for case in ('case3', 'case4a', 'case4b')]
    if any(len(group) != 3 for _, group in cases):
        raise ValueError('scope chart requires three rounds per case')
    labels = [label for case, group in cases for label in [case] + [f'r{r["repeat"]}' for r in group]]
    peak = max(r['tensor_total_gib'] for r in rows if r['complete'] and r['case'] == 'case4a')
    fig, axes = style.figure(f'兩個 Pod 共用 Claim 時，成功 tensor 合計達 {_gib(peak)} GiB',
                             subtitle='每列一輪；短直線是該組配額；停滯輪次以空心點標示最後完整 barrier',
                             note='Case 3：同一 Pod 內兩個 container；Case 4a：兩個 Pod 共用 Claim；\nCase 4b：兩個 Pod 各自一份 Claim。',
                             height=5.4, ncols=2, sharey=True, value_axis='x')
    tensor_ax, physical_ax = axes
    ys = style.rows_axis(tensor_ax, labels, gaps_after=(3, 7))
    for tick in tensor_ax.get_yticklabels():
        if tick.get_text() in ('case3', 'case4a', 'case4b'):
            tick.set_fontweight(600)
    index = 0
    for case, group in cases:
        claims = 1 if group[0]['claims_shared'] else 2
        quota = group[0]['quota_gib'] * claims
        quota_mib = quota * 1024
        shared = claims == 1
        header_y = ys[index]
        style.value_label(tensor_ax, 0, header_y, f'共用 Claim 配額 {style.fmt(quota)} GiB' if shared
                          else f'兩份獨立 Claim 合計 {style.fmt(quota)} GiB', dx=6)
        style.value_label(physical_ax, 0, header_y, f'共用 Claim 配額 {style.fmt(quota_mib)} MiB' if shared
                          else f'兩份獨立 Claim 合計 {style.fmt(quota_mib)} MiB', dx=6)
        index += 1
        for row in group:
            y = ys[index]
            index += 1
            complete = row['complete']
            tensor = row['tensor_total_gib'] if complete else row['last_barrier_tensor_total_gib']
            physical = row['nvml_process_total_mib'] if complete else row['last_barrier_nvml_process_total_mib']
            # A stalled round only has its last complete barrier, not a final result, so it is never flagged red.
            over_tensor, over_physical = complete and tensor > quota, complete and physical > quota_mib
            style.row_ref(tensor_ax, y, quota)
            style.row_ref(physical_ax, y, quota_mib)
            style.row_dots(tensor_ax, y, [tensor], style.OOM if over_tensor else style.COLORS['hami'], hollow=not complete)
            style.row_dots(physical_ax, y, [physical], style.OOM if over_physical else style.COLORS['hami'], hollow=not complete)
            # Values under the quota sit left of the dot, over-quota values right of it, so no label meets a quota tick.
            if over_tensor:
                style.value_label(tensor_ax, tensor, y, _gib(tensor) + '，超過配額', color=style.OOM, dx=10)
            else:
                style.value_label(tensor_ax, tensor, y, _gib(tensor), dx=-10, ha='right')
            if not complete:
                style.value_label(tensor_ax, quota, y, '停滯：最後完整 barrier', dx=10)
            if over_physical:
                style.value_label(physical_ax, physical, y, style.fmt(physical) + '，超過配額', color=style.OOM, dx=10)
            elif not complete:
                style.value_label(physical_ax, physical, y, style.fmt(physical) + '（停滯）', dx=10)
            else:
                style.value_label(physical_ax, physical, y, style.fmt(physical), dx=-10, ha='right')
    tensor_ax.set_xlim(0, 20)
    tensor_ax.set_xticks([0, 4, 8, 12, 16, 20])
    tensor_ax.set_title('成功 tensor 合計')
    style.unit(tensor_ax, 'GiB')
    physical_ax.set_xlim(0, 22000)
    physical_ax.set_xticks([0, 4000, 8000, 12000, 16000, 20000])
    style.thousands(physical_ax, 'x')
    physical_ax.set_title('兩個 process 的實體用量合計')
    style.unit(physical_ax, 'MiB')
    return _freeze_layout(fig)


@register('04-isolation', 'e2-tensor-comparison')
def e2_tensor_comparison(data):
    groups = comparison_cases(data)
    fig, ax = style.figure('排程記帳量與 CUDA 配額的範圍不同',
                           subtitle='B 成功保留的 tensor 合計（GiB）；每列的點是 r1–r3，短直線是該列的配額',
                           note='MPS 的配額套在每個 client；ConsumableShares 只做排程記帳，沒有 CUDA 配額。',
                           height=4.9, legend=True)
    ys = style.rows_axis(ax, [name for name, _, _ in groups], gaps_after=(0, 2))
    for y, (name, entity, rows) in zip(ys, groups):
        values = [r['b_max_tensor_gib'] for r in rows]
        quota = rows[0]['quota_gib']
        style.row_ref(ax, y, quota)
        style.row_dots(ax, y, values, style.COLORS[entity])
        low, high = min(values), max(values)
        text = style.fmt(high, 2).rstrip('0').rstrip('.') if low == high else \
            f"{style.fmt(low, 2).rstrip('0').rstrip('.')}–{style.fmt(high, 2).rstrip('0').rstrip('.')}"
        if entity == 'mps' and 'dual' in name:
            text += '（兩個 client 各 5）'
            color = style.INK
        elif high > quota:
            text += '，超過配額'
            color = style.OOM
        else:
            color = style.INK
        style.value_label(ax, max(high, quota), y, text, color=color, dx=10)
    ax.set_xlim(0, 12.5)
    ax.set_xticks(range(0, 13, 2))
    style.unit(ax, 'GiB')
    style.header_legend(fig, [style.dot(style.SECONDARY), style.ref_tick_handle()],
                        ['每輪結果（顏色代表方案）', '配額：Claim 或每個 client'])
    return fig


@register('04-isolation', 'e2-physical-comparison')
def e2_physical_comparison(data):
    groups = comparison_cases(data)
    fig, axes = style.figure('整卡 NVML 含 A 與其他開銷，不能直接判定 B 的配額',
                             subtitle='每列一個條件，三點為 r1–r3；同列的點上下錯開只為了分開重疊的點',
                             note='ConsumableShares 超額後立即退出，取樣可能漏掉瞬間峰值。\n右欄軸從 24,000 MiB 開始，不從 0 起算。',
                             height=5.6, ncols=2, sharey=True, value_axis='x')
    ys = style.rows_axis(axes[0], [E2_TWO_LINE[name] for name, _, _ in groups], gaps_after=(0, 2))
    panels = [(axes[0], 'nvml_peak_used_mib', 'B 活躍期間整卡峰值（MiB）', (0, 18000), [0, 4000, 8000, 12000, 16000]),
              (axes[1], 'nvml_min_free_mib', '同一窗口整卡最少可用量（MiB）', (24000, 46000),
               [25000, 30000, 35000, 40000, 45000])]
    for ax, field, title, (low, high), ticks in panels:
        ax.set_title(title)
        ax.set_xlim(low, high)
        ax.set_xticks(ticks)
        style.thousands(ax, 'x')
        for y, (_, entity, rows) in zip(ys, groups):
            values = [r[field] for r in rows]
            for offset, value in zip((0.22, 0, -0.22), values):
                style.row_dots(ax, y + offset, [value], style.COLORS[entity], size=46)
            style.value_label(ax, max(values), y, _range_text(values), dx=9)
        style.unit(ax, 'MiB')
    return _freeze_layout(fig)


@register('04-isolation', 'e2-stability')
def e2_stability(data):
    consumable = rounds(data, 'consumable-shares', 'boundary')
    hami = hami_rounds(data, 'boundary') + hami_rounds(data, 'scope')
    mps = rounds(data, 'mps')
    counts = [sum(r['a_probe_ok_pressure'] for r in consumable), sum(r['hold_probe_ok'] for r in hami),
              sum(r['hold_probe_ok'] for r in mps)]
    errors = [sum(r['a_probe_oom_pressure'] for r in consumable), sum(r['hold_probe_oom'] for r in hami),
              sum(r['hold_probe_oom'] for r in mps)]
    if any(errors):
        raise ValueError('stability title requires zero A allocation OOM in every window')
    fig, ax = style.figure('有限壓力下 A 都沒有配置 OOM，但統計窗口不同',
                           subtitle='A 的配置 probe 成功次數；三種方案在各自窗口內都沒有配置 OOM',
                           note='次數不構成強度排名；ConsumableShares 只統計有限壓力期，沒有共同 hold 窗口。',
                           height=3.6, value_axis='x')
    ys = style.rows_axis(ax, [f'ConsumableShares\n壓力期 {len(consumable)} 輪', f'HAMi-DRA\nhold {len(hami)} 輪',
                              f'MPS\nhold {len(mps)} 輪'])
    style.hbars(ax, ys, counts, [style.COLORS['consumable-shares'], style.COLORS['hami'], style.COLORS['mps']],
                labels=[f'{style.fmt(c)} 次成功，{style.fmt(e)} 次 OOM' for c, e in zip(counts, errors)])
    ax.set_xlim(0, 400)
    ax.set_xticks([0, 100, 200, 300, 400])
    style.unit(ax, '次')
    return _freeze_layout(fig)


@register('04-isolation', 'e2-accounting-comparison')
def e2_accounting_comparison(data):
    consumable = rounds(data, 'consumable-shares', 'accounting')[0]
    hami = hami_rounds(data, 'accounting')[0]
    runtime = hami_rounds(data, 'boundary')[0]
    if runtime['schedulable_remaining_cores'] != 0:
        raise ValueError('accounting title requires the runtime cores to be fully reserved')
    fig, axes = style.figure('記帳餘額不等於實體 free；cores 已全數記帳',
                             subtitle='上：memory（MiB）；第一列是整卡實體 free，第二列是第三份 Claim 需求，其餘為帳本餘額\n下：cores，發布總量 100 cores',
                             note='記帳測試每份 Claim 10 cores，runtime 每份 50 cores。\n實體 free 只在記帳釋放前取樣；三輪記帳數值相同。',
                             height=6.0, nrows=2, height_ratios=[8, 3.2], value_axis='x')
    memory = [
        ('整卡實體 free（記帳時）', style.COLORS['NVIDIA A40'], consumable['physical_free_mib_before']),
        ('第三份 Claim 需求', style.NEUTRAL, 8192),
        ('ConsumableShares 釋放前', style.COLORS['consumable-shares'], consumable['schedulable_remaining_mib_before']),
        ('ConsumableShares 釋放後', style.COLORS['consumable-shares'], consumable['schedulable_remaining_mib_after']),
        ('HAMi-DRA 釋放前', style.COLORS['hami'], hami['schedulable_remaining_mib_before']),
        ('HAMi-DRA 釋放後', style.COLORS['hami'], hami['schedulable_remaining_mib_after']),
        ('HAMi-DRA runtime A+B', style.COLORS['hami'], runtime['schedulable_remaining_mib']),
    ]
    ax = axes[0]
    ys = style.rows_axis(ax, [name for name, _, _ in memory], gaps_after=(1, 3))
    style.hbars(ax, ys, [value for _, _, value in memory], [color for _, color, _ in memory])
    ax.set_title('memory（MiB）')
    ax.set_xlim(0, 56000)
    ax.set_xticks([0, 10000, 20000, 30000, 40000, 50000])
    style.thousands(ax, 'x')
    style.unit(ax, 'MiB')
    cores = [('HAMi-DRA 記帳釋放前', hami['reserved_cores_before'], hami['schedulable_remaining_cores_before']),
             ('HAMi-DRA 記帳釋放後', hami['reserved_cores_after'], hami['schedulable_remaining_cores_after']),
             ('HAMi-DRA runtime A+B', runtime['reserved_cores'], runtime['schedulable_remaining_cores'])]
    ax = axes[1]
    ys = style.rows_axis(ax, [name for name, _, _ in cores])
    for y, (_, reserved, remaining) in zip(ys, cores):
        ax.barh(y, reserved, height=0.6, color=style.COLORS['hami'], zorder=3)
        ax.barh(y, remaining, left=reserved, height=0.6, color=style.NEUTRAL, zorder=3)
        style.value_label(ax, reserved + remaining, y,
                          f'已記帳 {style.fmt(reserved)}，餘額 {style.fmt(remaining)}', dx=8)
    ax.set_title('cores')
    ax.set_xlim(0, 150)
    ax.set_xticks([0, 25, 50, 75, 100])
    style.unit(ax, 'cores')
    return _freeze_layout(fig)
