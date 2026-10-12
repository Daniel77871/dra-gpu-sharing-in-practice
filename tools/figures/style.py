"""Book chart style and layout.

A builder calls ``figure()`` to get a fixed-width canvas with a left-aligned title (the finding), an optional
subtitle (context and units), an optional one-row header legend and an optional note line under the plot. It draws
with the helpers below and returns the Figure. ``save_svg`` lays the figure out, runs the guards and writes a
deterministic SVG with text as paths.

Rules the guards enforce: one fixed width, no rotated text (no rotated axis titles or tick labels), no text outside
the canvas, no two texts overlapping, no dual axes, 3D or pie charts. Entity colours are fixed across chapters and
never depend on ranking; visible labels use reader-facing names from ``LABELS``, never internal keys.
"""

import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[2] / ".work/matplotlib"))

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Wedge
from matplotlib.ticker import FuncFormatter

WIDTH = 7.6            # inches; every chart has this width (about 730 CSS px on GitHub)
PAD = 0.16             # outer padding in inches
TITLE_SIZE = 15.5
SUBTITLE_SIZE = 11.5
BASE_SIZE = 11
NOTE_SIZE = 10
LABEL_SIZE = 10.5      # direct labels and value labels

SURFACE = "#ffffff"
INK = "#1d1d1b"
SECONDARY = "#55554f"
TICK = "#4b4b47"
GRID = "#e8e7e2"
BASELINE = "#8f8e88"
REFERENCE = "#2f2f2c"
NEUTRAL = "#c4c3bc"
OK = "#2a7f4f"
OOM = "#c0392b"
PENDING = "#d99a00"
COLORS = {
    "timeslicing": "#3a6ea5", "mps": "#d9622b", "hami": "#2a9d8f", "consumable-shares": "#7b59ae",
    "exclusive": "#9a9a9a", "sequential": "#5f5f5f",
    "NVIDIA A40": "#4c55b8", "RTX 4090": "#b4457f",
    "ok": OK, "oom": OOM, "pending": PENDING, "survivor": OK, "pressure": OOM, "neutral": NEUTRAL,
}
LABELS = {
    "timeslicing": "TimeSlicing", "mps": "MPS", "hami": "HAMi-DRA", "consumable-shares": "ConsumableShares",
    "exclusive": "獨占", "sequential": "逐個執行", "NVIDIA A40": "A40", "RTX 4090": "RTX 4090",
}
FONT_CANDIDATES = ("PingFang TC", "Heiti TC", "Noto Sans CJK TC", "Noto Sans TC",
                   "Microsoft JhengHei", "Arial Unicode MS")
_LEGEND_ROW = 0.28      # inches reserved for the header legend row
_GAP = 0.07             # inches between header blocks


def find_cjk_font():
    for family in FONT_CANDIDATES:
        try:
            font_manager.findfont(font_manager.FontProperties(family=[family]), fallback_to_default=False)
            return family
        except ValueError:
            continue
    raise RuntimeError("CJK font missing. Install one of: " + ", ".join(FONT_CANDIDATES))


def configure():
    family = find_cjk_font()
    matplotlib.rcParams.update({
        "font.family": [family], "font.size": BASE_SIZE,
        "axes.titlesize": 12, "axes.titleweight": 600, "axes.titlelocation": "left", "axes.titlecolor": INK,
        "axes.titlepad": 8, "axes.labelsize": BASE_SIZE, "axes.labelcolor": SECONDARY,
        "xtick.labelsize": BASE_SIZE, "ytick.labelsize": BASE_SIZE, "xtick.color": TICK, "ytick.color": TICK,
        "xtick.major.size": 0, "ytick.major.size": 0, "xtick.minor.size": 0, "ytick.minor.size": 0,
        "xtick.major.pad": 6, "ytick.major.pad": 6,
        "legend.fontsize": BASE_SIZE - 0.5, "legend.frameon": False,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "savefig.transparent": False, "text.color": INK, "axes.edgecolor": BASELINE, "axes.linewidth": 0.8,
        "grid.color": GRID, "grid.linewidth": 0.7, "axes.axisbelow": True,
        "axes.spines.top": False, "axes.spines.right": False,
        "lines.linewidth": 2.0, "lines.markersize": 7, "lines.solid_capstyle": "round",
        "patch.linewidth": 0.0, "svg.fonttype": "path", "svg.hashsalt": "dra-book-v2",
        "axes.unicode_minus": False,
    })
    return family


def _line(size):
    return size * 1.35 / 72


def _lines(text):
    return len(text.split("\n")) if text else 0


def figure(title, *, subtitle=None, note=None, height=4.2, nrows=1, ncols=1, sharex=False, sharey=False,
           legend=False, width_ratios=None, height_ratios=None, value_axis="y"):
    """Return (fig, ax) or (fig, axes) on the fixed-width canvas.

    title: the finding, zh-TW, one line if possible. subtitle: context and units (may contain one newline).
    note: source or caveat printed under the plot. legend: reserve the header row for ``header_legend``.
    value_axis: "y" for vertical bars/lines, "x" for horizontal bars and dot rows (sets grid and spines).
    """
    configure()
    header = PAD + _line(TITLE_SIZE) * _lines(title)
    if subtitle:
        header += _GAP * 0.6 + _line(SUBTITLE_SIZE) * _lines(subtitle)
    legend_top = header + _GAP
    if legend:
        header = legend_top + _LEGEND_ROW
    header += _GAP * 1.2
    footer = PAD + (_line(NOTE_SIZE) * _lines(note) + _GAP if note else 0)
    if header + footer > height - 1.2:
        raise ValueError(f"figure height {height} in leaves no room for the plot; raise the height")
    fig = plt.figure(figsize=(WIDTH, height), layout="constrained")
    fig.get_layout_engine().set(rect=(PAD / WIDTH, footer / height, 1 - 2 * PAD / WIDTH,
                                      1 - (header + footer) / height),
                                w_pad=0.02, h_pad=0.02, wspace=0.06, hspace=0.08)
    gridspec = {}
    if width_ratios:
        gridspec["width_ratios"] = width_ratios
    if height_ratios:
        gridspec["height_ratios"] = height_ratios
    axes = fig.subplots(nrows, ncols, sharex=sharex, sharey=sharey, gridspec_kw=gridspec or None, squeeze=True)
    fig.text(PAD / WIDTH, 1 - PAD / height, title, ha="left", va="top", fontsize=TITLE_SIZE,
             fontweight=600, color=INK)
    if subtitle:
        y = 1 - (PAD + _line(TITLE_SIZE) * _lines(title) + _GAP * 0.6) / height
        fig.text(PAD / WIDTH, y, subtitle, ha="left", va="top", fontsize=SUBTITLE_SIZE, color=SECONDARY,
                 linespacing=1.3)
    if note:
        fig.text(PAD / WIDTH, PAD / height, note, ha="left", va="bottom", fontsize=NOTE_SIZE, color=SECONDARY,
                 linespacing=1.3)
    fig._dra_legend_top = (1 - legend_top / height) if legend else None
    for ax in (axes.flat if hasattr(axes, "flat") else [axes]):
        set_value_axis(ax, value_axis)
    return fig, axes


def set_value_axis(ax, which):
    """Gridlines on the value axis only; keep the baseline spine, hide the category spine."""
    if which not in ("x", "y"):
        raise ValueError("value axis must be 'x' or 'y'")
    ax.grid(axis=which, visible=True)
    ax.grid(axis="y" if which == "x" else "x", visible=False)
    ax.spines["left"].set_visible(which == "x")
    ax.spines["bottom"].set_visible(which == "y")
    ax._dra_value_axis = which


def header_legend(fig, handles, labels):
    """One legend row under the subtitle; requires figure(..., legend=True)."""
    if getattr(fig, "_dra_legend_top", None) is None:
        raise ValueError("call figure(..., legend=True) before header_legend")
    legend = fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(PAD / WIDTH, fig._dra_legend_top),
                        bbox_transform=fig.transFigure, ncols=len(labels), frameon=False, borderaxespad=0,
                        borderpad=0, handlelength=1.5, handletextpad=0.5, columnspacing=1.6, labelcolor=INK)
    legend.set_in_layout(False)
    return legend


def swatch(color):
    return Patch(facecolor=color, edgecolor="none")


def dot(color, *, hollow=False, marker="o", size=7):
    return Line2D([], [], linestyle="none", marker=marker, markersize=size, markeredgewidth=1.6,
                  markerfacecolor=SURFACE if hollow else color, markeredgecolor=color)


def line_handle(color, *, dashed=False):
    return Line2D([], [], color=color, linewidth=2, linestyle="--" if dashed else "-")


def ref_tick_handle(color=REFERENCE):
    """Legend handle that looks like ``row_ref`` (a short vertical tick)."""
    return Line2D([], [], color=color, marker="|", markersize=13, markeredgewidth=2.2, linestyle="none")


def text_on(color):
    """White or INK, whichever has at least 4.5:1 contrast on a filled colour (white preferred)."""
    def channel(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = matplotlib.colors.to_rgb(color)
    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return SURFACE if 1.05 / (luminance + 0.05) >= 4.5 else INK


def fmt(value, decimals=0):
    """Thousands separators and fixed decimals: fmt(45490) -> '45,490', fmt(7.25, 2) -> '7.25'."""
    return f"{value:,.{decimals}f}"


def thousands(ax, axis="y", decimals=0):
    formatter = FuncFormatter(lambda value, _: fmt(value, decimals))
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(formatter)


def unit(ax, text):
    """Short horizontal unit/measure label: above the y tick labels, or at the right end of the x axis."""
    if getattr(ax, "_dra_value_axis", "y") == "y":
        return ax.annotate(text, xy=(0, 1), xycoords="axes fraction", xytext=(-6, 9), textcoords="offset points",
                           ha="right", va="bottom", fontsize=LABEL_SIZE, color=SECONDARY)
    return ax.set_xlabel(text, loc="right", fontsize=LABEL_SIZE, color=SECONDARY, labelpad=6)


def ref_line(ax, value, label, *, axis=None, color=REFERENCE, dashed=True):
    """Full-span reference line labelled outside the plot area (right end for a horizontal line, top for a vertical)."""
    axis = axis or ("x" if getattr(ax, "_dra_value_axis", "y") == "x" else "y")
    style = "--" if dashed else "-"
    if axis == "y":
        ax.axhline(value, color=color, linestyle=style, linewidth=1.2, zorder=4)
        return ax.annotate(label, xy=(1, value), xycoords=("axes fraction", "data"), xytext=(6, 0),
                           textcoords="offset points", ha="left", va="center", fontsize=LABEL_SIZE, color=color)
    ax.axvline(value, color=color, linestyle=style, linewidth=1.2, zorder=4)
    return ax.annotate(label, xy=(value, 1), xycoords=("data", "axes fraction"), xytext=(0, 6),
                       textcoords="offset points", ha="center", va="bottom", fontsize=LABEL_SIZE, color=color)


def rows_axis(ax, labels, *, gaps_after=()):
    """Category rows from top to bottom for horizontal charts; returns the y position of each row.

    gaps_after: indexes after which an extra half-row gap separates groups.
    """
    positions, y = [], 0.0
    for index in range(len(labels)):
        positions.append(y)
        y -= 1.5 if index in gaps_after else 1.0
    ax.set_yticks(positions, labels)
    ax.set_ylim(min(positions) - 0.7, 0.7)
    set_value_axis(ax, "x")
    return positions


def row_dots(ax, y, values, color, *, hollow=False, size=70, zorder=5, marker="o"):
    """One row of a dot plot (for example r1-r3 of one condition)."""
    return ax.scatter(values, [y] * len(values), s=size, marker=marker, zorder=zorder, linewidths=1.6,
                      facecolors=SURFACE if hollow else color, edgecolors=color)


def row_ref(ax, y, value, *, color=REFERENCE, half=0.32):
    """Short vertical reference tick for one row (for example that row's quota)."""
    return ax.vlines(value, y - half, y + half, colors=color, linewidth=2.2, zorder=4)


def hbars(ax, ys, values, colors, *, height=0.6, decimals=0, suffix="", labels=None, color=INK):
    """Horizontal bars at row positions from ``rows_axis`` with value labels at the bar ends."""
    colors = colors if isinstance(colors, (list, tuple)) else [colors] * len(values)
    artists = ax.barh(ys, values, height=height, color=colors, zorder=3)
    texts = labels or [fmt(value, decimals) + suffix for value in values]
    for y, value, text in zip(ys, values, texts):
        value_label(ax, value, y, text, color=color, dx=6)
    return artists


def bars(ax, xs, values, colors, *, width=0.6, decimals=0, suffix="", labels=None, color=INK):
    """Vertical bars with value labels above them (use only for short category labels)."""
    colors = colors if isinstance(colors, (list, tuple)) else [colors] * len(values)
    artists = ax.bar(xs, values, width=width, color=colors, zorder=3)
    texts = labels or [fmt(value, decimals) + suffix for value in values]
    for x, value, text in zip(xs, values, texts):
        value_label(ax, x, value, text, color=color, dx=0, dy=5, ha="center", va="bottom")
    return artists


def value_label(ax, x, y, text, *, color=INK, dx=8, dy=0, ha="left", va="center", size=LABEL_SIZE, weight=None):
    """Direct value label offset in points from a data position."""
    return ax.annotate(text, xy=(x, y), xytext=(dx, dy), textcoords="offset points", ha=ha, va=va,
                       fontsize=size, color=color, fontweight=weight)


def end_labels(ax, items, *, min_gap=13, dx=8):
    """Direct labels at line ends without vertical overlap.

    items: list of (x, y, text, color) in data coordinates; labels keep their order and are pushed apart by at
    least min_gap points. Set the axis limits before calling.
    """
    if not items:
        return []
    fig = ax.figure
    height_pt = ax.get_position().height * fig.get_size_inches()[1] * 72
    low, high = ax.get_ylim()
    to_pt = lambda v: (v - low) / (high - low) * height_pt
    placed = sorted(((to_pt(y), x, y, text, color) for x, y, text, color in items), key=lambda item: item[0])
    positions = []
    for want, *_ in placed:
        positions.append(max(want, positions[-1] + min_gap) if positions else want)
    overflow = positions[-1] - height_pt if positions[-1] > height_pt else 0
    positions = [p - overflow for p in positions]
    artists = []
    for (want, x, y, text, color), got in zip(placed, positions):
        artists.append(ax.annotate(text, xy=(x, y), xytext=(dx, got - want), textcoords="offset points",
                                   ha="left", va="center", fontsize=LABEL_SIZE, color=color, fontweight=600))
    return artists


def _texts(fig):
    found = []

    def add(text, where):
        if text is not None and text.get_visible() and text.get_text().strip():
            found.append((text, where))

    for text in fig.texts:
        add(text, "figure")
    for legend in fig.legends:
        for text in legend.get_texts():
            add(text, "legend")
    for index, ax in enumerate(fig.axes):
        where = f"axes {index}"
        for title in (ax.title, getattr(ax, "_left_title", None), getattr(ax, "_right_title", None)):
            add(title, where)
        add(ax.xaxis.label, where)
        add(ax.yaxis.label, where)
        for text in ax.texts:
            add(text, where)
        if ax.get_legend():
            for text in ax.get_legend().get_texts():
                add(text, where)
        for axis in (ax.xaxis, ax.yaxis):
            low, high = sorted(axis.get_view_interval())
            span = (high - low) * 1e-6
            for tick in axis.get_major_ticks():
                if low - span <= tick.get_loc() <= high + span:
                    add(tick.label1 if tick.label1.get_visible() else None, where)
                    add(tick.label2 if tick.label2.get_visible() else None, where)
            add(axis.get_offset_text(), where)
    return found


def check_layout(fig):
    """Raise ValueError when the laid-out figure breaks a layout rule."""
    if abs(fig.get_size_inches()[0] - WIDTH) > 1e-6:
        raise ValueError(f"figure width must be {WIDTH} in, got {fig.get_size_inches()[0]:.2f} in")
    renderer = fig.canvas.get_renderer()
    canvas = fig.bbox
    boxes = []
    for text, where in _texts(fig):
        content = text.get_text().replace("\n", " ")
        if abs(text.get_rotation() % 360) > 0.5:
            raise ValueError(f"rotated text is not allowed ({where}): {content!r}")
        box = text.get_window_extent(renderer)
        if (box.x0 < canvas.x0 - 0.5 or box.y0 < canvas.y0 - 0.5
                or box.x1 > canvas.x1 + 0.5 or box.y1 > canvas.y1 + 0.5):
            raise ValueError(f"text outside the canvas ({where}): {content!r}")
        boxes.append((box, content, where))
    for index, (first, first_text, first_where) in enumerate(boxes):
        for second, second_text, second_where in boxes[index + 1:]:
            if (first.x0 < second.x1 - 1 and second.x0 < first.x1 - 1
                    and first.y0 < second.y1 - 1 and second.y0 < first.y1 - 1):
                raise ValueError(f"overlapping text: {first_text!r} ({first_where}) and "
                                 f"{second_text!r} ({second_where})")


def _freeze_layout(fig):
    """Pin the solved layout: constrained layout can differ in the last float bits between processes, which
    changes clip-path ids in the SVG. Rounded positions (1e-4 of the canvas, about 0.05 pt) keep output stable."""
    positions = [(ax, ax.get_position()) for ax in fig.axes]
    if fig.get_layout_engine() is not None:
        fig.set_layout_engine("none")
    for ax, box in positions:
        ax.set_position([round(value, 4) for value in (box.x0, box.y0, box.width, box.height)])


def save_svg(fig, path):
    configure()
    if (not fig.axes or any(getattr(ax, "name", "") == "3d" for ax in fig.axes)
            or any(ax.get_position().overlaps(other.get_position())
                   for index, ax in enumerate(fig.axes) for other in fig.axes[index + 1:])):
        raise ValueError("each panel must have one 2D axis; dual axes and 3D are disallowed")
    for ax in fig.axes:
        if any(isinstance(patch, Wedge) for patch in ax.patches):
            raise ValueError("pie charts are disallowed")
    fig.set_facecolor(SURFACE)
    fig.canvas.draw()
    _freeze_layout(fig)
    fig.canvas.draw()
    check_layout(fig)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="svg", metadata={"Date": None}, facecolor=SURFACE, transparent=False)
    plt.close(fig)
