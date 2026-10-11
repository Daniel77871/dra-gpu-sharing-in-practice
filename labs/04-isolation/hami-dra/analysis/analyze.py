#!/usr/bin/env python3
"""Stdlib entry point shared by HAMi accounting, runtime and scope outputs."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'common/analysis'))
from hami import main

if __name__ == '__main__':
    raise SystemExit(main())
