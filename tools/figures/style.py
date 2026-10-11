"""Book chart style. Builders use new_chart, line, bars and save_svg.

Titles state a finding in zh-TW; callers supply axis labels with units. No dual
axes, 3D or pie charts. Entity colors never depend on ranking; consumable-shares
and mps must not be adjacent. At most four direct labels, always in ink colors.
"""

import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[2] / ".work/matplotlib"))

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager
from matplotlib import pyplot as plt
from matplotlib.patches import Wedge

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
COLORS = {
    "timeslicing": "#2a78d6", "mps": "#eb6834", "hami": "#1baf7a",
    "consumable-shares": "#eda100", "exclusive": MUTED, "sequential": SECONDARY,
    "steady": "#008300", "survivor": "#008300", "pressure": "#e34948",
    "NVIDIA A40": "#6250d6", "RTX 4090": "#e87ba4",
}
FONT_CANDIDATES = ("PingFang TC", "Heiti TC", "Noto Sans CJK TC", "Noto Sans TC",
                   "Microsoft JhengHei", "Arial Unicode MS")


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
        "font.family": [family], "font.size": 11, "axes.titlesize": 12, "axes.labelsize": 11,
        "xtick.labelsize": 10, "ytick.labelsize": 10, "legend.fontsize": 10,
        "figure.figsize": (7.5, 4.2), "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE, "savefig.transparent": False,
        "text.color": INK, "axes.labelcolor": SECONDARY, "axes.titlecolor": INK,
        "axes.edgecolor": BASELINE, "axes.linewidth": 0.6,
        "xtick.color": MUTED, "ytick.color": MUTED, "grid.color": GRID, "grid.linewidth": 0.4,
        "axes.axisbelow": True, "axes.spines.top": False, "axes.spines.right": False,
        "lines.linewidth": 1.5, "lines.markersize": 6,
        "patch.edgecolor": SURFACE, "patch.linewidth": 0.8,
        "svg.fonttype": "path", "svg.hashsalt": "dra-book-v1", "axes.unicode_minus": False,
    })
    return family


def new_chart(title, xlabel, ylabel):
    configure()
    fig, ax = plt.subplots()
    ax.set(title=title, xlabel=xlabel, ylabel=ylabel)
    ax.grid(axis="y")
    ax.grid(axis="x", visible=False)
    return fig, ax


def line(ax, x, y, entity, *, label=None, direct_label=False):
    label = label or entity
    artist, = ax.plot(x, y, color=COLORS[entity], label=label,
                      linestyle="--" if entity == "pressure" else "-", marker="o")
    if direct_label or entity == "pressure":
        count = getattr(ax, "_dra_direct_labels", 0)
        if count >= 4:
            raise ValueError("at most four direct-label series")
        ax.annotate(label, (x[-1], y[-1]), xytext=(6, 0), textcoords="offset points", color=INK)
        ax._dra_direct_labels = count + 1
    return artist


def bars(ax, labels, values, entities):
    if not len(labels) == len(values) == len(entities):
        raise ValueError("bar labels, values and entities must have equal length")
    if any({a, b} == {"consumable-shares", "mps"} for a, b in zip(entities, entities[1:])):
        raise ValueError("consumable-shares must not be adjacent to mps")
    artists = ax.bar(labels, values, width=0.8, color=[COLORS[e] for e in entities],
                     edgecolor=SURFACE, linewidth=0.8)
    for artist, entity in zip(artists, entities):
        artist.set_label(entity)
    ax.set_ylim(bottom=min(0, min(values, default=0)))
    ax.bar_label(artists, padding=3, color=INK, fontsize=10)
    return artists


def save_svg(fig, path):
    configure()
    if (not fig.axes or any(getattr(ax, "name", "") == "3d" for ax in fig.axes)
            or any(ax.get_position().overlaps(other.get_position())
                   for index, ax in enumerate(fig.axes) for other in fig.axes[index + 1:])):
        raise ValueError("each panel must have one 2D axis; dual axes and 3D are disallowed")
    fig.set_facecolor(SURFACE)
    for ax in fig.axes:
        if any(isinstance(patch, Wedge) for patch in ax.patches):
            raise ValueError("pie charts are disallowed")
        if not fig.legends:
            handles, labels = ax.get_legend_handles_labels()
            unique = dict(zip(labels, handles))
            if len(unique) >= 2:
                ax.legend(unique.values(), unique.keys(), frameon=False, labelcolor=INK)
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y")
        ax.grid(axis="x", visible=False)
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="svg", metadata={"Date": None}, facecolor=SURFACE, transparent=False,
                bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)
