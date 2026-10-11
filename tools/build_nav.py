#!/usr/bin/env python3
"""Generate navigation and nested TOC from book_order.yml. --check is read-only."""

import argparse
import os
from pathlib import Path
import re
from urllib.parse import quote

import yaml

if __package__:
    from .markdown import headings
else:
    from markdown import headings

ROOT = Path(__file__).resolve().parents[1]
INDEX = "book/README.md"


def load_order(root):
    order = yaml.safe_load((root / "tools/book_order.yml").read_text(encoding="utf-8"))
    pages = order["pages"]
    known = set()
    for page in pages:
        path = page["path"]
        if path in known or not path.startswith("book/") or ".." in Path(path).parts:
            raise ValueError(f"invalid or duplicate page path: {path}")
        if page.get("parent") and page["parent"] not in known:
            raise ValueError(f"parent must precede child: {path}")
        known.add(path)
    if not pages or pages[0]["path"] != INDEX:
        raise ValueError(f"first page must be {INDEX}")
    return pages


def relative(source, target):
    return quote(os.path.relpath(target, Path(source).parent).replace(os.sep, "/"), safe="/._-")


def label(title):
    return title.replace("[", r"\[").replace("]", r"\]")


def replace_block(text, kind, body, *, at_end=False):
    start, end = f"<!-- {kind}:start -->", f"<!-- {kind}:end -->"
    if text.count(start) != text.count(end) or text.count(start) > 1:
        raise ValueError(f"invalid {kind} markers")
    block = f"{start}\n{body}\n{end}"
    if start in text:
        pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
        if not pattern.search(text):
            raise ValueError(f"reversed {kind} markers")
        if not at_end:
            return pattern.sub(lambda _: block, text)
        text = pattern.sub("", text).rstrip()
    return text.rstrip() + "\n\n" + block + "\n"


def expected_updates(root):
    pages = load_order(root)
    contents = {}
    titles = {}
    for page in pages:
        path = root / page["path"]
        if not path.is_file():
            raise ValueError(f"missing page: {page['path']}")
        contents[page["path"]] = path.read_text(encoding="utf-8")
        h1 = [title for level, title, _ in headings(contents[page["path"]]) if level == 1]
        titles[page["path"]] = h1[0] if h1 else page["title"]
    depths = {INDEX: -1}
    toc = []
    for page in pages[1:]:
        path = page["path"]
        depth = depths.get(page.get("parent"), -1) + 1
        depths[path] = depth
        toc.append(f"{'  ' * depth}- [{label(titles[path])}]({relative(INDEX, path)})")
    updates = {}
    for i, page in enumerate(pages):
        path = page["path"]
        text = contents[path]
        if path == INDEX:
            text = replace_block(text, "toc", "\n".join(toc))
        nav = []
        if i:
            prev = pages[i - 1]["path"]
            nav.append(f"[← 上一頁：{label(titles[prev])}]({relative(path, prev)})")
        nav.append(f"[目錄]({relative(path, INDEX)})")
        if i + 1 < len(pages):
            nxt = pages[i + 1]["path"]
            nav.append(f"[下一頁：{label(titles[nxt])} →]({relative(path, nxt)})")
        text = replace_block(text, "nav", " ｜ ".join(nav), at_end=True)
        if text != contents[path]:
            updates[root / path] = text
    return updates


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        updates = expected_updates(args.root)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        print(f"tools/book_order.yml:1: [nav] {error}")
        return 1
    for path, text in updates.items():
        if args.check:
            print(f"{path.relative_to(args.root)}:1: [nav] stale nav or TOC")
        else:
            path.write_text(text, encoding="utf-8")
    print(f"Navigation: {len(updates)} {'stale' if args.check else 'updated'} page(s)")
    return int(args.check and bool(updates))


if __name__ == "__main__":
    raise SystemExit(main())
