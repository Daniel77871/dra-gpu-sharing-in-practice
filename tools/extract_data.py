#!/usr/bin/env python3
"""Extract experiment data with an explicit source ledger; U0 has no extractors."""

import argparse
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.extractors import REGISTRY, SourceReader, discover

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAB = Path("/Users/daniel/dra-gpu-oom-lab")


def main(argv=None):
    discover()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", type=Path, default=DEFAULT_LAB, help=f"read-only source lab (default: {DEFAULT_LAB})")
    parser.add_argument("--root", type=Path, default=ROOT, help="destination repository")
    subcommands = parser.add_subparsers(dest="exp", required=True)
    for exp, (_, help_text) in sorted(REGISTRY.items()):
        subcommands.add_parser(exp, help=help_text)
    args = parser.parse_args(argv)
    output = args.root / "data" / args.exp
    if output.resolve().is_relative_to(args.lab.resolve()):
        parser.error("destination must be outside the read-only lab")
    try:
        output.mkdir(parents=True, exist_ok=True)
        reader = SourceReader(args.lab)
        REGISTRY[args.exp][0](reader, output)
        reader.write_sources(output)
    except (OSError, ValueError) as error:
        print(f"Extraction failed: {error}")
        return 1
    print(f"Extracted {args.exp}: {len(reader.sources)} source file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
