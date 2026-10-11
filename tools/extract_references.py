#!/usr/bin/env python3
"""List external book citations, or check that the reference appendix covers them."""

import argparse
import json
from pathlib import Path
import re
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.markdown import headings, links, markdown_lines, slugify

ROOT = Path(__file__).resolve().parents[1]


def external_links(text):
    """Include Markdown citations, autolinks and URLs in runnable examples."""
    found = set()
    for number, destination in links(text):
        if destination.startswith(('https://', 'http://')):
            found.add((number, destination))
    # Code URLs (for example SDK source archives) are also useful references.
    for number, line, language, delimiter in markdown_lines(text):
        if delimiter or language is None:
            continue
        for match in re.finditer(r'https?://[^\s<>"\'`]+', line):
            found.add((number, match[0].rstrip('.,;)')))
    for match in re.finditer(r'<(https?://[^<>\s]+)>', text):
        found.add((text.count('\n', 0, match.start()) + 1, match[1]))
    return sorted(found)


def inventory(root):
    appendix = root / 'book/appendix/references.md'
    result = {}
    for path in sorted((root / 'book').rglob('*.md')):
        if path == appendix:
            continue
        text = path.read_text()
        sections = headings(text)
        for number, url in external_links(text):
            previous = [title for _, title, line in sections if line <= number]
            anchor = slugify(previous[-1]) if previous else ''
            result.setdefault(url, []).append({
                'path': path.relative_to(root).as_posix(),
                'line': number,
                'anchor': anchor,
            })
    return dict(sorted(result.items()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--check', action='store_true', help='check appendix URL coverage')
    args = parser.parse_args(argv)
    citations = inventory(args.root)
    if not args.check:
        print(json.dumps(citations, ensure_ascii=False, indent=2))
        return 0
    appendix = args.root / 'book/appendix/references.md'
    covered = {url for _, url in external_links(appendix.read_text())}
    missing = sorted(citations.keys() - covered)
    for url in missing:
        print(f'Missing reference: {url}')
    print(f'References: {len(citations)} cited URLs, {len(missing)} missing')
    return int(bool(missing))


if __name__ == '__main__':
    raise SystemExit(main())
