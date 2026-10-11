import subprocess
import sys

import pytest

from conftest import ROOT
from test_build_nav import run_nav


@pytest.mark.parametrize("title,want", [
    ("Hello, GPU!", "hello-gpu"),
    ("第 1 章　為什麼需要 DRA", "第-1-章-為什麼需要-dra"),
    ("CUDA_context-test（說明）", "cuda_context-test說明"),
    ("2.1 GPU 節點準備", "21-gpu-節點準備"),
])
def test_heading_slug(title, want):
    from tools.markdown import slugify
    assert slugify(title) == want


def test_duplicate_slugs_and_fenced_headings():
    from tools.markdown import heading_slugs
    assert heading_slugs("# 測試\n## 測試\n## 測試\n```sh\n# 假標題\n```\n") == {
        "測試", "測試-1", "測試-2"
    }


def test_lint_prose_mermaid_inline_code_and_opt_out():
    from tools.check_book import lint_text, SIMPLIFIED_ONLY
    text = (
        "# 測試\n顯存與内存。\n`顯存`\n```yaml\n顯存: 内存\n```\n"
        "```mermaid\nflowchart LR\n A[顯存]\n```\n"
        "顯存 <!-- zh-lint: allow 顯存 -->\n程序與文件。\n"
    )
    issues = lint_text(text, "book/test.md")
    errors = [i for i in issues if i.severity == "error"]
    assert {i.line for i in errors} == {2, 9}
    assert any("顯存" in i.message for i in errors)
    assert any("内" in i.message for i in errors)
    assert any(i.line == 12 and i.severity == "warning" for i in issues)
    assert len(SIMPLIFIED_ONLY) >= 300
    assert not set("干后于里云台面斗余制范系松征朴只器組件据存").intersection(SIMPLIFIED_ONLY)


def test_quote_warnings_do_not_fail_and_allowed_terms_suppress_simplified():
    from tools.check_book import lint_text
    issues = lint_text('「性能」與「支持」。\n显存 <!-- zh-lint: allow 显存 -->\n', "page.md")
    assert len(issues) == 2
    assert all(i.severity == "warning" for i in issues)


def test_valid_mermaid_closing_fence_does_not_lint_next_paragraph():
    from tools.check_book import check_mermaid, lint_text
    assert check_mermaid("```mermaid\nflowchart LR\n A --> B\n```\n正文。\n", "page.md") == []
    assert lint_text("咸、岳、夸、妒、厥、噪、匀、游、强、涂、叶、筑。", "page.md") == []


def test_escaped_ticks_are_prose_and_multiline_equal_delimiters_are_code():
    from tools.check_book import lint_text
    from tools.markdown import links
    escaped = lint_text(r"\`顯存\`", "page.md")
    assert any(i.rule == "zh-term" for i in escaped)
    text = "正文 `code\n顯存 [忽略](missing.md)\nend` 結束。\n"
    assert lint_text(text, "page.md") == []
    assert list(links(text)) == []
    assert lint_text("``one ` 顯存\ntwo``\n", "page.md") == []


def test_commented_headings_do_not_count_or_create_anchors():
    from tools.markdown import headings, heading_slugs
    text = "# Actual\n<!--\n# Hidden\n-->\n## Visible\n"
    assert headings(text) == [(1, "Actual", 1), (2, "Visible", 5)]
    assert heading_slugs(text) == {"actual", "visible"}


def run_check(root):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools/check_book.py"), "--root", str(root)],
        capture_output=True, text=True,
    )


def test_relative_links_images_anchors_and_reference_links(tiny_book):
    page = tiny_book / "book/ch/detail.md"
    page.write_text(
        '# 細節\n\n[正確](README.md#章)\n[壞檔](missing.md)\n'
        '[壞錨](README.md#不存在)\n![圖](missing.svg)\n'
        '[參照][src]\n\n[src]: README.md#不存在\n'
        '`[忽略](missing-code.md)`\n```sh\n[忽略](missing-fence.md)\n```\n'
    )
    assert run_nav(tiny_book).returncode == 0
    result = run_check(tiny_book)
    assert result.returncode == 1
    assert "detail.md:4: [link]" in result.stdout
    assert "detail.md:5: [anchor]" in result.stdout
    assert "detail.md:6: [link]" in result.stdout
    assert "missing-code" not in result.stdout and "missing-fence" not in result.stdout
    assert "errors" in result.stdout


def test_h1_mermaid_inventory_and_stale_errors(tiny_book):
    assert run_nav(tiny_book).returncode == 0
    page = tiny_book / "book/ch/detail.md"
    page.write_text(page.read_text() + "\n# 多餘\n\n```mermaid\nnotADiagram\n```\n")
    (tiny_book / "book/stray.md").write_text("# 未登錄\n")
    result = run_check(tiny_book)
    assert result.returncode == 1
    for rule in ("h1", "mermaid", "order", "nav"):
        assert f"[{rule}]" in result.stdout
