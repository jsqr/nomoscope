"""Packages depend in one direction. A module may import only the packages
in its own layer or below. The command modules on top may import anything."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "nomoscope"
LAYERS = [
    {"coverage", "errors", "parquet", "partition", "rows", "segments", "settings", "text"},
    {"graph"},
    {"snarf"},
    {"pdf"},
    {"crunch"},
    {"derive"},
    {"catalog"},
    {"cli", "status", "show"},
]
RANK = {name: rank for rank, layer in enumerate(LAYERS) for name in layer}


def package_of(path: Path) -> str:
    rel = path.relative_to(SRC)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("nomoscope."):
                found.add(node.module.split(".")[1])
            elif node.module == "nomoscope":
                found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("nomoscope."):
                    found.add(alias.name.split(".")[1])
    return found


def layered():
    for path in sorted(SRC.rglob("*.py")):
        own = package_of(path)
        if own != "__init__":
            yield path, own


def test_every_package_is_placed():
    packages = {own for _, own in layered()}
    assert packages <= set(RANK), sorted(packages - set(RANK))


def test_nothing_imports_upward():
    wrong = []
    for path, own in layered():
        for imported in sorted(imports_of(path)):
            if RANK.get(imported, -1) > RANK[own]:
                wrong.append(f"{path.relative_to(SRC)} -> {imported}")
    assert wrong == []
