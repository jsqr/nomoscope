"""nomoscope: an archive of published law, and the history of each section.

A project builds its command with cli.app(Registry(...), name).
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nomoscope.crunch.registry import Registry
    from nomoscope.crunch.runner import Carver

__all__ = ["Carver", "Registry"]

# Loaded on first use, so importing one module does not import every stage.
LAZY = {
    "Registry": ("nomoscope.crunch.registry", "Registry"),
    "Carver": ("nomoscope.crunch.runner", "Carver"),
}


def __getattr__(name: str) -> object:
    if name in LAZY:
        module, attr = LAZY[name]
        return getattr(importlib.import_module(module), attr)
    raise AttributeError(f"module 'nomoscope' has no attribute {name!r}")
