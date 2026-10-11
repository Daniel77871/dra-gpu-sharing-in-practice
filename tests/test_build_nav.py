import subprocess
import sys

from conftest import ROOT


def run_nav(root, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools/build_nav.py"), "--root", str(root), *args],
        text=True, capture_output=True,
    )


def test_generation_is_idempotent_and_detects_stale_without_writing(tiny_book):
    assert run_nav(tiny_book, "--check").returncode == 1
    assert run_nav(tiny_book).returncode == 0
    index = tiny_book / "book/README.md"
    content = index.read_text()
    assert "- [章](ch/README.md)\n  - [細節](ch/detail.md)" in content
    assert "[目錄](README.md) ｜ [下一頁：章 →](ch/README.md)" in content
    before = {p: p.read_bytes() for p in (tiny_book / "book").rglob("*.md")}
    assert run_nav(tiny_book).returncode == 0
    assert {p: p.read_bytes() for p in before} == before
    assert run_nav(tiny_book, "--check").returncode == 0
    index.write_text(content.replace("下一頁：章", "下一頁：舊章"))
    stale = index.read_bytes()
    assert run_nav(tiny_book, "--check").returncode == 1
    assert index.read_bytes() == stale


def test_actual_h1_wins_and_nav_moves_to_end(tiny_book):
    assert run_nav(tiny_book).returncode == 0
    page = tiny_book / "book/ch/README.md"
    page.write_text(page.read_text().replace("# 章", "# 新章") + "\n後加正文。\n")
    assert run_nav(tiny_book).returncode == 0
    assert "[新章](ch/README.md)" in (tiny_book / "book/README.md").read_text()
    assert page.read_text().rstrip().endswith("<!-- nav:end -->")
    assert "後加正文。" in page.read_text()
    assert "[← 上一頁：新章](README.md) ｜ [目錄](../README.md)" in (
        tiny_book / "book/ch/detail.md"
    ).read_text()


def test_missing_page_and_duplicate_markers_are_reported(tiny_book):
    missing = tiny_book / "book/ch/detail.md"
    missing.unlink()
    result = run_nav(tiny_book, "--check")
    assert result.returncode == 1
    assert "detail.md" in result.stdout + result.stderr
