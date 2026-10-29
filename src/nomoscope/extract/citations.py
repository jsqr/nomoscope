"""Citation targets as nomoscope ids, whether or not the target is loaded.

Relative citations ("subsection (a)", "this section") and citations naming
no code use the passage's own code and section. Named codes map to id
prefixes through the name tables. Enactments get
`<jurisdiction>/enactment/<kind>/<year>/<number>`, with `-` for a missing
year.

Statuses:
- resolved: the target is in this jurisdiction's rows.
- unloaded: the target's code is not loaded here.
- unresolved: the citation could not be placed.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from nomoscope.derive import ids
from nomoscope.extract import scopes
from nomoscope.extract.names import Names
from nomoscope.extract.scopes import Index

SECTION = "section"
SUBDIVISION = "subdivision"
NODE = "node"
CODE = "code"
ENACTMENT = "enactment"
RESOLVED = "resolved"
UNLOADED = "unloaded"
UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Target:
    kind: str | None = None
    id: str | None = None
    status: str = UNRESOLVED


NONE = Target()


@dataclass(frozen=True)
class Cited:
    text: str
    kind: str
    code: str | None
    section: str | None
    path: Sequence[str]
    levels: Sequence[tuple[str, str | None]]
    commencing_section: str | None
    enactment: str | None
    enactment_number: str | None
    enactment_year: int | None
    relative: bool


SECTION_WORDS = re.compile(r"(?i)^(?:\u00a7+|sections?|sec\.|ss?\.)\s*")
ENUMERATOR = re.compile(r"\(([A-Za-z]{1,4}|\d{1,3})\)")
SLUG = re.compile(r"[^a-z0-9]+")


def section_number(written: str) -> str:
    return SECTION_WORDS.sub("", written.strip()).strip()


def code_prefix(idx: Index, names: Names, block_id: str, name: str | None) -> str | None:
    if name:
        found = names.code(idx.jurisdiction, name)
        if found is not None:
            return found
        if scopes.word(name) != CODE:
            return None
    own = idx.code_of(block_id)
    return scopes.code_id(idx, own) if own else None


def own_prefix(idx: Index, prefix: str) -> str | None:
    """The code, if the prefix is one of this jurisdiction's loaded codes."""
    head, _, code = prefix.rpartition(ids.SEP + "law" + ids.SEP)
    return code if head == idx.jurisdiction and code in idx.codes else None


def written_path(text: str) -> list[str]:
    """Enumerators, outermost first. "paragraph (2) of subsection (b)" names
    the inner one first, so the "of" parts are reversed."""
    parts = re.split(r"\s+of\s+", text)
    found = [ENUMERATOR.findall(part) for part in parts]
    return [e for part in reversed(found) for e in part]


def relative_path(idx: Index, block_id: str, at: int, path: Sequence[str]) -> list[str]:
    """A relative path made absolute under the innermost enclosing
    subdivision that has it."""
    known = idx.paths_of(block_id)
    for prefix in scopes.enclosing_paths(idx, block_id, at):
        candidate = f"{prefix}/{'/'.join(path)}"
        if candidate in known:
            return candidate.split("/")
    return list(path)


def section_target(idx: Index, prefix: str, number: str, path: Sequence[str]) -> Target:
    code = own_prefix(idx, prefix)
    num = scopes.unnumbered(section_number(number)) or ""
    if not num or any(c.isspace() for c in num):
        # "art. IX, sec. 2" is not a section number an id can carry.
        return NONE
    if code is not None:
        found = idx.sections_by_key.get((code, num))
        if found is None:
            return Target(SECTION, ids.compose(prefix, num), UNRESOLVED)
        return with_path(found, path, RESOLVED)
    return with_path(ids.compose(prefix, num), path, UNLOADED)


def with_path(section: str, path: Sequence[str], status: str) -> Target:
    if path:
        return Target(SUBDIVISION, ids.compose(section, *path), status)
    return Target(SECTION, section, status)


def node_by_chain(
    idx: Index, code: str, levels: Sequence[tuple[str, str | None]], near: str | None
) -> str | None:
    """Candidates for the innermost level are kept if every outer level is
    above them. Ties go to the candidate above `near`."""
    if not levels:
        return None
    level, number = levels[-1]
    candidates = list(idx.nodes_by_key.get((code, level.lower(), scopes.unnumbered(number)), ()))
    for outer_level, outer_number in levels[:-1]:
        candidates = [
            c
            for c in candidates
            if above(idx, idx.node_parent.get(c), outer_level.lower(), outer_number)
        ]
    if len(candidates) == 1:
        return candidates[0]
    chain = set(lineage(idx, near))
    return next((c for c in candidates if c in chain), None)


def above(idx: Index, node_id: str | None, level: str, number: str | None) -> bool:
    return any(
        idx.node_level.get(n) == level
        and (number is None or idx.node_number.get(n) == scopes.unnumbered(number))
        for n in lineage(idx, node_id)
    )


def lineage(idx: Index, node_id: str | None) -> list[str]:
    found: list[str] = []
    while node_id is not None and node_id not in found:
        found.append(node_id)
        node_id = idx.node_parent.get(node_id)
    return found


def enactment_id(idx: Index, names: Names, c: Cited) -> Target:
    if not c.enactment_number:
        return NONE
    found = names.enactment(idx.jurisdiction, c.enactment)
    if found is None:
        return NONE
    owner, kind = found
    number = SLUG.sub("-", c.enactment_number.lower()).strip("-")
    year = str(c.enactment_year) if c.enactment_year else "-"
    return Target(ENACTMENT, ids.compose(owner, "enactment", kind, year, number), UNLOADED)


def resolve_one(
    idx: Index,
    names: Names,
    block_id: str,
    at: int,
    c: Cited,
    section: str | None,
    path: Sequence[str],
) -> Target:
    if c.kind == ENACTMENT:
        return enactment_id(idx, names, c)
    if c.kind in (SECTION, SUBDIVISION) or section is not None:
        own_section = idx.block_section.get(block_id)
        if section is None:
            if own_section is None or not (c.relative or c.kind == SUBDIVISION):
                return NONE
            given = list(path) or written_path(c.text)
            full = relative_path(idx, block_id, at, given) if given else []
            known = not full or "/".join(full) in idx.paths_of(block_id)
            return with_path(own_section, full, RESOLVED if known else UNRESOLVED)
        prefix = code_prefix(idx, names, block_id, c.code)
        if prefix is None:
            return NONE
        return section_target(idx, prefix, section, path)
    if c.kind == "division":
        own = idx.node_of(block_id)
        level = c.levels[-1][0].lower() if c.levels else scopes.word(c.text)
        if c.relative and level:
            node = scopes.ancestor(idx, own, level)
            return Target(NODE, node, RESOLVED) if node else NONE
        prefix = code_prefix(idx, names, block_id, c.code)
        if prefix is None:
            return NONE
        code = own_prefix(idx, prefix)
        if code is None:
            return Target(CODE, prefix, UNLOADED)
        if c.commencing_section:
            start = idx.sections_by_key.get((code, scopes.unnumbered(c.commencing_section) or ""))
            node = idx.section_node.get(start) if start else None
            found = scopes.ancestor(idx, node, level) if level else node
            if found:
                return Target(NODE, found, RESOLVED)
        near = own if code == idx.code_of(block_id) else None
        node = node_by_chain(idx, code, c.levels, near)
        return Target(NODE, node, RESOLVED) if node else NONE
    prefix = code_prefix(idx, names, block_id, c.code)
    if prefix is None:
        return NONE
    return Target(CODE, prefix, RESOLVED if own_prefix(idx, prefix) else UNLOADED)


def resolve(
    idx: Index,
    names: Names,
    block_id: str,
    at: int,
    c: Cited,
    section_end: str | None,
    path_end: Sequence[str],
) -> tuple[Target, Target]:
    """The target, and the end of a range or NONE."""
    start = resolve_one(idx, names, block_id, at, c, c.section, c.path)
    if section_end is None and not path_end:
        return start, NONE
    end = resolve_one(idx, names, block_id, at, c, section_end or c.section, path_end or c.path)
    return start, end
