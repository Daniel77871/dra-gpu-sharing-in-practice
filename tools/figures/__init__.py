"""Chapter modules chXX.py register builders with @register(chapter, name).

A builder accepts the repository's data/ Path and returns a matplotlib Figure.
Import style only when rendering; the initial registry intentionally has no data.
"""

import importlib
import pkgutil
import re

REGISTRY = {}


def register(chapter, name):
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value) for value in (chapter, name)):
        raise ValueError("chapter and figure names must be safe path components")
    def decorator(builder):
        figures = REGISTRY.setdefault(chapter, {})
        if name in figures:
            raise ValueError(f"duplicate figure: {chapter}/{name}")
        figures[name] = builder
        return builder
    return decorator


def discover():
    for module in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if re.fullmatch(r"ch\d{2}", module.name):
            importlib.import_module(f"{__name__}.{module.name}")
