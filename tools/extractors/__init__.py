"""Later units add e1/e2/e2b/e3/e4/e4b.py and @register(exp, help=...).

Callbacks receive (SourceReader, output_dir). All lab reads must go through the
reader so SOURCES.md records the actual lab-relative inputs. No recursive scan.
"""

import importlib
from pathlib import Path
import pkgutil
import re

EXPERIMENTS = ("e1", "e2", "e2-hami", "e2b", "e3", "e4", "e4b")
REGISTRY = {}


def register(exp, *, help=""):
    if exp not in EXPERIMENTS:
        raise ValueError(f"unknown experiment: {exp}")
    def decorator(extractor):
        if exp in REGISTRY:
            raise ValueError(f"duplicate extractor: {exp}")
        REGISTRY[exp] = (extractor, help)
        return extractor
    return decorator


def discover():
    for module in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if module.name.replace('_', '-') in EXPERIMENTS:
            importlib.import_module(f"{__name__}.{module.name}")


class SourceReader:
    def __init__(self, lab):
        self.lab = Path(lab).resolve()
        self.sources = set()

    def read_bytes(self, relative):
        path = (self.lab / relative).resolve()
        if not path.is_relative_to(self.lab):
            raise ValueError(f"source outside lab: {relative}")
        content = path.read_bytes()
        self.sources.add(path.relative_to(self.lab).as_posix())
        return content

    def read_text(self, relative):
        return self.read_bytes(relative).decode("utf-8")

    def write_sources(self, output_dir):
        # Source paths are code spans; escape backticks from unusual filenames.
        rows = ["# 資料來源", "", "以下為擷取器實際讀取的實驗室相對路徑。", ""]
        line_ranges = getattr(self, 'line_ranges', {})
        rows.extend(f"- `{source.replace('`', '&#96;')}`" +
                    (f" L1–L{line_ranges[source]}" if source in line_ranges else '')
                    for source in sorted(self.sources))
        (output_dir / "SOURCES.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
