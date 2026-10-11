import subprocess
import sys

import pytest

from conftest import ROOT


def test_chart_render_is_byte_identical_and_text_is_paths(tmp_path):
    from tools.figures import style
    outputs = []
    for name in ("first.svg", "second.svg"):
        fig, ax = style.new_chart("共用機制的合成測試", "時間（秒）", "GPU 記憶體（MiB）")
        style.line(ax, [0, 1, 2], [1, 2, 3], "steady", label="穩定行程")
        style.line(ax, [0, 1, 2], [3, 2, 1], "pressure", label="壓力行程")
        path = tmp_path / name
        style.save_svg(fig, path)
        outputs.append(path.read_bytes())
    assert outputs[0] == outputs[1]
    assert b"<text" not in outputs[0]
    assert b"dc:date" not in outputs[0]
    assert b"#fcfcfb" in outputs[0]


def test_missing_cjk_font_is_an_actionable_error(monkeypatch):
    from tools.figures import style
    def missing(*args, **kwargs):
        raise ValueError("not found")
    monkeypatch.setattr(style.font_manager, "findfont", missing)
    with pytest.raises(RuntimeError, match="CJK.*PingFang TC"):
        style.find_cjk_font()


def test_figure_registry_routes_data_and_output(tmp_path, monkeypatch):
    from tools import make_figures
    from tools.figures import REGISTRY, register, style
    monkeypatch.setattr(make_figures, "discover", lambda: None)
    monkeypatch.setitem(REGISTRY, "synthetic", {})
    @register("synthetic", "finding")
    def finding(data_dir):
        assert data_dir == tmp_path / "data"
        fig, ax = style.new_chart("合成資料", "批次（次）", "時間（秒）")
        ax.bar([0, 1], [2, 3])
        return fig
    assert make_figures.main(["--root", str(tmp_path), "--only", "synthetic"]) == 0
    output = tmp_path / "book/images/synthetic/finding.svg"
    assert output.is_file()
    before = output.read_bytes()
    assert make_figures.main(["--root", str(tmp_path), "--only", "synthetic"]) == 0
    assert output.read_bytes() == before


def test_empty_registry_cli_and_unknown_chapter():
    command = [sys.executable, str(ROOT / "tools/make_figures.py")]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0 and "0 figure" in result.stdout
    assert subprocess.run(command + ["--only", "missing"], capture_output=True).returncode == 1
