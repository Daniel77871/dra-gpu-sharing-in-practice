import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".work" / "matplotlib"))


@pytest.fixture
def tiny_book(tmp_path):
    pages = [
        {"path": "book/README.md", "title": "書名", "unit": "U7", "parent": None},
        {"path": "book/ch/README.md", "title": "章", "unit": "U1", "parent": None},
        {"path": "book/ch/detail.md", "title": "細節", "unit": "U1", "parent": "book/ch/README.md"},
    ]
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/book_order.yml").write_text(
        yaml.safe_dump({"pages": pages}, allow_unicode=True), encoding="utf-8"
    )
    for page in pages:
        path = tmp_path / page["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'# {page["title"]}\n\n正文。\n', encoding="utf-8")
    return tmp_path
