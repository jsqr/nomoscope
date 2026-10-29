"""Scope phrases ("this chapter") resolved against a jurisdiction's rows.

"this section" is the section and "this code" the code. "this <level>" is
the nearest heading of that level above the passage, or the enclosing
subdivision of that level. Level words come from the rows. A phrase naming
no known level keeps a null id.

Bases:
- stated: the passage names the scope.
- governing: the passage names none, and a passage under the same heading
  says its definitions apply to a larger division.
- default: a general provision that applies where no other one does, such
  as a general penalty section.
"""

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from nomoscope.derive import ids
from nomoscope.extract.rows import Rows

SECTION = "section"
NODE = "node"
CODE = "code"
SUBDIVISION = "subdivision"
OTHER = "other"
STATED = "stated"
GOVERNING = "governing"
DEFAULT = "default"

# "the chapter" is left out: it also opens "the chapter of the Laws of 2005".
THIS = re.compile(r"(?i)\b(?:this|that|such)\s+([a-z][a-z-]*)")


@dataclass(frozen=True)
class Scope:
    text: str | None = None
    kind: str | None = None
    id: str | None = None
    basis: str | None = None


NONE = Scope()


@dataclass(frozen=True)
class Subdivision:
    level: str | None
    start: int
    end: int
    depth: int
    # Section id plus path, which is stable across versions.
    id: str
    path: str


@dataclass(frozen=True)
class Index:
    """Section and heading numbers are keyed without a trailing period;
    citations omit it."""

    jurisdiction: str
    node_level: Mapping[str, str | None]
    node_parent: Mapping[str, str | None]
    node_number: Mapping[str, str | None]
    nodes_by_key: Mapping[tuple[str, str | None, str | None], tuple[str, ...]]
    section_node: Mapping[str, str | None]
    section_code: Mapping[str, str]
    sections_by_key: Mapping[tuple[str, str], str]
    # Version id -> section id.
    block_section: Mapping[str, str]
    subdivisions: Mapping[str, list[Subdivision]]
    subdivision_levels: frozenset[str]

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(self.section_code.values())

    def node_of(self, block_id: str) -> str | None:
        section = self.block_section.get(block_id)
        return self.section_node.get(section) if section else None

    def code_of(self, block_id: str) -> str | None:
        section = self.block_section.get(block_id)
        return self.section_code.get(section) if section else None

    def paths_of(self, block_id: str) -> frozenset[str]:
        return frozenset(d.path for d in self.subdivisions.get(block_id, ()))


def unnumbered(number: str | None) -> str | None:
    return number.strip().rstrip(".") if number is not None else None


def index(rows: Rows) -> Index:
    node_level: dict[str, str | None] = {}
    node_parent: dict[str, str | None] = {}
    node_number: dict[str, str | None] = {}
    by_key: dict[tuple[str, str | None, str | None], list[str]] = defaultdict(list)
    columns = ["id", "parent_id", "level", "number", "law_code"]
    for node_id, parent_id, level, number, code in rows.nodes.select(columns).iter_rows():
        node_level[node_id] = level.lower() if level else None
        node_parent[node_id] = parent_id
        node_number[node_id] = unnumbered(number)
        by_key[code, node_level[node_id], unnumbered(number)].append(node_id)
    section_node: dict[str, str | None] = {}
    section_code: dict[str, str] = {}
    sections_by_key: dict[tuple[str, str], str] = {}
    block_section: dict[str, str] = {}
    columns = ["id", "section_id", "node_id", "law_code", "section_num"]
    for block_id, section_id, node_id, code, num in rows.versions.select(columns).iter_rows():
        block_section[block_id] = section_id
        section_node.setdefault(section_id, node_id)
        section_code.setdefault(section_id, code)
        sections_by_key.setdefault((code, unnumbered(num) or ""), section_id)
    spans: dict[str, list[Subdivision]] = defaultdict(list)
    levels: set[str] = set()
    columns = ["law_version_id", "level", "start_char", "end_char", "depth", "subdivision_id"]
    for block_id, level, start, end, depth, row_id, path in rows.subdivisions.select(
        *columns, "path"
    ).iter_rows():
        word = level.lower() if level else None
        spans[block_id].append(Subdivision(word, start, end, depth, row_id, path))
        if word:
            levels.add(word)
    return Index(
        rows.jurisdiction,
        node_level,
        node_parent,
        node_number,
        {k: tuple(v) for k, v in by_key.items()},
        section_node,
        section_code,
        sections_by_key,
        block_section,
        dict(spans),
        frozenset(levels),
    )


def word(phrase: str | None) -> str | None:
    """ "this chapter" -> "chapter"."""
    if not phrase:
        return None
    m = THIS.search(phrase)
    return m.group(1).lower() if m else None


def ancestor(idx: Index, node_id: str | None, level: str) -> str | None:
    seen: set[str] = set()
    while node_id is not None and node_id not in seen:
        seen.add(node_id)
        if idx.node_level.get(node_id) == level:
            return node_id
        node_id = idx.node_parent.get(node_id)
    return None


def enclosing(idx: Index, block_id: str, level: str, at: int) -> str | None:
    found = [
        (d.depth, d.id)
        for d in idx.subdivisions.get(block_id, [])
        if d.level == level and d.start <= at < d.end
    ]
    return max(found)[1] if found else None


def enclosing_paths(idx: Index, block_id: str, at: int) -> list[str]:
    """Innermost first."""
    found = [(d.depth, d.path) for d in idx.subdivisions.get(block_id, []) if d.start <= at < d.end]
    return [path for _, path in sorted(found, reverse=True)]


def code_id(idx: Index, code: str) -> str:
    return ids.compose(idx.jurisdiction, "law", code)


def resolve(idx: Index, block_id: str, at: int, phrase: str | None, basis: str = STATED) -> Scope:
    if phrase is None:
        return NONE
    w = word(phrase)
    section = idx.block_section.get(block_id)
    if w is None:
        return Scope(phrase, OTHER, None, basis)
    if w == SECTION:
        return Scope(phrase, SECTION, section, basis)
    if w == CODE:
        code = idx.code_of(block_id)
        return Scope(phrase, CODE, code_id(idx, code) if code else None, basis)
    if w in idx.subdivision_levels:
        return Scope(phrase, SUBDIVISION, enclosing(idx, block_id, w, at), basis)
    node = ancestor(idx, idx.node_of(block_id), w)
    kind = NODE if node or w in set(idx.node_level.values()) else OTHER
    return Scope(phrase, kind, node, basis)


def governing(idx: Index, governs: Mapping[str, str]) -> dict[str, Scope]:
    """Scopes by heading id, for definitions that state none.

    `governs` maps a version id to the phrase naming the larger division
    that its heading's definitions apply to.
    """
    found: dict[str, Scope] = {}
    for block_id in sorted(governs):
        node = idx.node_of(block_id)
        if node is None or node in found:
            continue
        found[node] = resolve(idx, block_id, 0, governs[block_id], GOVERNING)
    return found


def scope_of(
    idx: Index, block_id: str, at: int, phrase: str | None, governed: Mapping[str, Scope]
) -> Scope:
    if phrase is not None:
        return resolve(idx, block_id, at, phrase)
    node = idx.node_of(block_id)
    return governed.get(node, NONE) if node else NONE
