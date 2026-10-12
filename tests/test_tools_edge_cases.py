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


def test_style_keeps_entity_colors_distinct_and_rejects_dual_axes(tmp_path):
    import pytest
    from tools.figures import style
    shared = ["timeslicing", "mps", "hami", "consumable-shares"]
    assert len({style.COLORS[name] for name in shared}) == len(shared)
    assert all(name in style.LABELS for name in shared)
    fig, ax = style.figure("合成測試", subtitle="耗時（秒）")
    style.bars(ax, [0, 1], [1, 2], [style.COLORS["consumable-shares"], style.COLORS["mps"]])
    ax.twinx()
    with pytest.raises(ValueError, match="one 2D axis"):
        style.save_svg(fig, tmp_path / "bad.svg")
    style.plt.close(fig)
