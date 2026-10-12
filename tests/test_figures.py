import re
import subprocess
import sys

import pytest

from conftest import ROOT


def synthetic(title="共用機制的合成測試", **kwargs):
    from tools.figures import style
    fig, ax = style.figure(title, subtitle="合成資料（MiB）", note="來源：測試", **kwargs)
    ys = style.rows_axis(ax, ["第一列", "第二列"])
    style.row_dots(ax, ys[0], [1200, 1300], style.COLORS["mps"])
    style.row_ref(ax, ys[0], 1500)
    style.hbars(ax, [ys[1]], [45490], style.COLORS["hami"])
    style.thousands(ax, "x")
    return fig, ax


def test_chart_render_is_byte_identical_fixed_width_and_text_is_paths(tmp_path):
    from tools.figures import style
    outputs = []
    for name in ("first.svg", "second.svg"):
        fig, _ = synthetic()
        path = tmp_path / name
        style.save_svg(fig, path)
        outputs.append(path.read_bytes())
    assert outputs[0] == outputs[1]
    assert b"<text" not in outputs[0] and b"dc:date" not in outputs[0]
    assert b'width="547.2pt"' in outputs[0]
    assert b"<!-- 45,490 -->" in outputs[0]


def test_layout_guards_reject_overlap_rotation_and_clipping(tmp_path):
    from tools.figures import style
    fig, ax = synthetic()
    ax.annotate("重疊", xy=(0, 1), xycoords="axes fraction")
    ax.annotate("重疊", xy=(0, 1), xycoords="axes fraction")
    with pytest.raises(ValueError, match="overlapping text"):
        style.save_svg(fig, tmp_path / "overlap.svg")
    fig, ax = synthetic()
    ax.set_ylabel("直式標題")
    with pytest.raises(ValueError, match="rotated text"):
        style.save_svg(fig, tmp_path / "rotated.svg")
    fig, ax = synthetic()
    fig.text(1.2, 0.5, "畫布外")
    with pytest.raises(ValueError, match="outside the canvas"):
        style.save_svg(fig, tmp_path / "outside.svg")
    fig, _ = synthetic()
    fig.set_size_inches(9, 4)
    with pytest.raises(ValueError, match="width"):
        style.save_svg(fig, tmp_path / "wide.svg")


def test_header_legend_needs_reserved_row_and_dual_axes_are_rejected(tmp_path):
    from tools.figures import style
    fig, _ = synthetic()
    with pytest.raises(ValueError, match="legend=True"):
        style.header_legend(fig, [style.dot(style.OK)], ["OK"])
    fig, ax = synthetic(legend=True)
    style.header_legend(fig, [style.dot(style.OK), style.ref_tick_handle()], ["OK", "配額"])
    ax.twiny()
    with pytest.raises(ValueError, match="dual axes"):
        style.save_svg(fig, tmp_path / "twin.svg")


def test_number_format_and_end_labels_do_not_collide(tmp_path):
    from tools.figures import style
    assert style.fmt(45490) == "45,490" and style.fmt(7.25, 2) == "7.25"
    fig, ax = style.figure("線條直接標註", subtitle="合成資料（倍）", ncols=1)
    ax.plot([1, 2, 3], [1.0, 1.95, 2.3], color=style.COLORS["mps"])
    ax.plot([1, 2, 3], [1.0, 1.96, 2.29], color=style.COLORS["hami"])
    ax.set_xlim(0.8, 3.6)
    ax.set_ylim(0.8, 2.6)
    style.end_labels(ax, [(3, 2.3, "MPS", style.COLORS["mps"]), (3, 2.29, "HAMi-DRA", style.COLORS["hami"])])
    style.save_svg(fig, tmp_path / "labels.svg")


def test_missing_cjk_font_is_an_actionable_error(monkeypatch):
    from tools.figures import style
    def missing(*args, **kwargs):
        raise ValueError("not found")
    monkeypatch.setattr(style.font_manager, "findfont", missing)
    with pytest.raises(RuntimeError, match="CJK.*PingFang TC"):
        style.find_cjk_font()


def test_figure_registry_routes_data_and_output(tmp_path, monkeypatch):
    from tools import make_figures
    from tools.figures import REGISTRY, register
    monkeypatch.setattr(make_figures, "discover", lambda: None)
    monkeypatch.setitem(REGISTRY, "synthetic", {})
    @register("synthetic", "finding")
    def finding(data_dir):
        assert data_dir == tmp_path / "data"
        return synthetic()[0]
    assert make_figures.main(["--root", str(tmp_path), "--only", "synthetic"]) == 0
    output = tmp_path / "book/images/synthetic/finding.svg"
    assert output.is_file()
    before = output.read_bytes()
    assert make_figures.main(["--root", str(tmp_path), "--only", "synthetic"]) == 0
    assert output.read_bytes() == before


def test_registry_cli_and_unknown_chapter():
    command = [sys.executable, str(ROOT / "tools/make_figures.py")]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0 and re.search(r"Rendered \d+ figure", result.stdout)
    assert subprocess.run(command + ["--only", "missing"], capture_output=True).returncode == 1
