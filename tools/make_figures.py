#!/usr/bin/env python3
"""Render registered chapter figures from data/ into book/images/<chapter>/."""

import argparse
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.figures import REGISTRY, discover

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", metavar="CHAPTER")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    discover()
    if args.only and args.only not in REGISTRY:
        print(f"Unknown chapter: {args.only}")
        return 1
    if not REGISTRY:
        print("Rendered 0 figures (empty registry; later units add chXX.py modules)")
        return 0
    from tools.figures import style
    count = 0
    try:
        style.configure()
        for chapter in sorted(REGISTRY):
            if args.only and chapter != args.only:
                continue
            for name, builder in sorted(REGISTRY[chapter].items()):
                fig = builder(args.root / "data")
                path = args.root / "book/images" / chapter / f"{name}.svg"
                style.save_svg(fig, path)
                print(path.relative_to(args.root))
                count += 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Figure generation failed: {error}")
        return 1
    print(f"Rendered {count} figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
