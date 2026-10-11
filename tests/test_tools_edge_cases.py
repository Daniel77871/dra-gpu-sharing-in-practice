from pathlib import Path

from conftest import ROOT
from test_build_nav import run_nav


def test_duplicate_nav_markers_and_fallback_title(tiny_book):
    assert run_nav(tiny_book).returncode == 0
    page = tiny_book / "book/ch/detail.md"
    page.write_text(page.read_text().replace("# 細節", "## 尚無 H1"))
    assert run_nav(tiny_book).returncode == 0
    assert "下一頁：細節" in (tiny_book / "book/ch/README.md").read_text()
    page.write_text(page.read_text() + "\n<!-- nav:start -->\n<!-- nav:end -->\n")
    assert run_nav(tiny_book, "--check").returncode == 1


def test_kubeconfig_json_keys_are_flagged():
    from tools.scan_sensitive import scan_text
    value = '"client-' + 'certificate-data": "' + 'example"'
    assert any(f.kind == "kubeconfig" for f in scan_text("example.json", value))


def test_kubeconfig_key_does_not_match_suffix_of_ordinary_key():
    from tools.scan_sensitive import scan_text
    assert scan_text("example.json", '"session-' + 'to' + 'ken": 12') == []


def test_style_rejects_prohibited_adjacent_entities_and_dual_axes(tmp_path):
    import pytest
    from tools.figures import style
    fig, ax = style.new_chart("合成測試", "批次（次）", "耗時（秒）")
    with pytest.raises(ValueError, match="adjacent"):
        style.bars(ax, ["a", "b"], [1, 2], ["consumable-shares", "mps"])
    ax.twinx()
    with pytest.raises(ValueError, match="one 2D axis"):
        style.save_svg(fig, tmp_path / "bad.svg")
    style.plt.close(fig)
