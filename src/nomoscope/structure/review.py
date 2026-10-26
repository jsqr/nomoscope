"""Render a parsed code as one HTML page, for comparing a parse with its source.

Every division is a heading with a badge: its level, its offset from the
section level (section 0, chapter -1, subsection +1), and its pages.
Sections show their carved text, with each subdivision marked, so the page
shows what derive receives. Text that is not carved (a chapter's notes, a
table of contents) is shown muted, and findings appear at the element they
concern.
"""

import html
from collections.abc import Mapping, Sequence

from nomoscope.structure import Row, documents, sections
from nomoscope.structure import grammar as gr
from nomoscope.structure.parse import RUN_IN

STYLE = """
body { font: 15px/1.45 Georgia, serif; max-width: 60rem; margin: 2rem auto; padding: 0 1rem; }
header table { border-collapse: collapse; font: 13px sans-serif; }
header td { padding: 1px 8px 1px 0; vertical-align: top; }
.badge { font: 11px sans-serif; color: #fff; background: #556; border-radius: 3px;
  padding: 1px 5px; margin-right: 4px; white-space: nowrap; text-decoration: none; }
.badge.b-section { background: #275; }
.badge.b-sub { background: #57a; }
.badge.b-term { background: #a75; }
.badge.b-other { background: #999; }
.division { margin: 1.2rem 0 0.3rem; font-weight: bold; }
.text { white-space: pre-wrap; margin: 0.3rem 0 0.6rem; }
.sub { border-left: 2px solid #ccd; padding-left: 6px; display: block; margin: 2px 0; }
.history { white-space: pre-wrap; color: #364; font-size: 13px; }
.dropped { white-space: pre-wrap; color: #a33; text-decoration: line-through; font-size: 13px; }
.muted { white-space: pre-wrap; color: #888; font-size: 13px; }
.finding { font: 12px sans-serif; color: #b00; }
details { font-size: 13px; color: #666; }
"""


# A longer findings list starts collapsed.
FINDINGS_OPEN = 20


def offsets(grammar: gr.Grammar) -> dict[str, int]:
    """Each grammar level's offset from the section level."""
    names = [lv.name for lv in grammar.levels]
    level = sections.section_level(grammar)
    base = names.index(level) if level in names else 0
    return {name: i - base for i, name in enumerate(names)}


def badge(node: Row, by_level: Mapping[str, int], href: str | None) -> str:
    level = node["level"]
    if level == RUN_IN:
        kind, label = "b-term", "term"
    elif level in by_level:
        n = by_level[level]
        kind = "b-section" if n == 0 else ("b-sub" if n > 0 else "")
        label = f"{level} {n:+d}" if n else f"{level} 0"
    else:
        kind, label = "b-other", f"{level} depth {node['depth']}"
    first, last = node["page_first"] + 1, node["page_last"] + 1
    pages = f" p{first}" if first == last else f" p{first}-{last}"
    text = html.escape(label + pages)
    if href:
        return f'<a class="badge {kind}" href="{html.escape(href)}#page={first}">{text}</a>'
    return f'<span class="badge {kind}">{text}</span>'


def said(finding: Row) -> str:
    return f"{html.escape(finding['check'])}: {html.escape(finding['detail'])}"


def _spans(text: str, subs: Sequence[Row], badges: Mapping[str, str]) -> str:
    """The section text, with each subdivision wrapped in a badged span."""
    children: dict[str, list[Row]] = {}
    for s in subs:
        parent = s["path"].rpartition("/")[0]
        children.setdefault(parent, []).append(s)

    def inside(path: str, start: int, end: int) -> str:
        out, at = [], start
        for s in sorted(children.get(path, []), key=lambda r: r["start_char"]):
            # Subdivisions are blocks, so the line break before one is not printed.
            out.append(html.escape(text[at : s["start_char"]].rstrip("\n")))
            body = inside(s["path"], s["start_char"], s["end_char"])
            out.append(f'<span class="sub" id="{html.escape(s["node_id"])}">')
            out.append(badges.get(s["path"], "") + body + "</span>")
            at = s["end_char"]
        out.append(html.escape(text[at:end].lstrip("\n") if at > start else text[at:end]))
        return "".join(out)

    return inside("", 0, len(text))


def render(
    nodes: list[Row],
    lines: list[Row],
    grammar: gr.Grammar,
    findings: list[Row],
    header: Sequence[tuple[str, str]],
    source: str | None = None,
) -> str:
    """The HTML page. `source` is the source file's href, for page links."""
    by_level = offsets(grammar)
    by_id = {n["id"]: n for n in nodes}
    carved = sections.carve(nodes, lines, grammar)
    by_section = {s["node_id"]: s for s in carved.sections}
    docs = {d["node_id"]: d for d in documents.build(nodes, lines)[0]}
    found: dict[str | None, list[Row]] = {}
    for f in findings:
        found.setdefault(f["node_id"], []).append(f)

    out = ["<!doctype html><html><head><meta charset='utf-8'>"]
    title = next((v for k, v in header if k == "record"), "parse")
    out.append(f"<title>{html.escape(title)}</title><style>{STYLE}</style></head><body>")
    out.append("<header><table>")
    out.extend(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>" for k, v in header)
    out.append("</table>")
    if findings:
        shown = " open" if len(findings) <= FINDINGS_OPEN else ""
        out.append(f"<details{shown}><summary>{len(findings)} findings</summary><ul>")
        for f in findings:
            target = f["node_id"]
            where = f'<a href="#{html.escape(target)}">{html.escape(target)}</a>' if target else "-"
            out.append(f"<li>{html.escape(f['check'])} {where}: {html.escape(f['detail'])}</li>")
        out.append("</ul></details>")
    out.append("</header><main>")

    for n in nodes:
        if n["kind"] == "subdivision":
            continue
        indent = max(n["depth"] - 1, 0) * 1.2
        doc = docs.get(n["id"])
        out.append(f'<div id="{html.escape(n["id"])}" style="margin-left:{indent}rem">')
        if n["kind"] != "root":
            label = " ".join(p for p in (n["label"], n["heading"]) if p)
            mark = badge(n, by_level, source)
            if n["id"] in by_section and by_level.get(n["level"]) != 0:
                mark += '<span class="badge b-section">carved as section</span>'
            out.append(f'<div class="division">{mark}{html.escape(label)}</div>')
        for f in found.get(n["id"], []):
            out.append(f'<div class="finding">{said(f)}</div>')
        section = by_section.get(n["id"])
        if section is not None:
            subs = []
            badges: dict[str, str] = {}
            for s in section["subdivisions"]:
                node_id = f"{n['id']}/{s['path']}"
                subs.append({**s, "node_id": node_id})
                sub = by_id.get(node_id)
                if sub is not None:
                    flags = "".join(
                        f'<span class="finding"> [{said(f)}]</span>' for f in found.get(node_id, [])
                    )
                    badges[s["path"]] = badge(sub, by_level, source) + flags
            text = _spans(section["text"] or "", subs, badges)
            out.append(f'<div class="text">{text}</div>')
            if section["history"]:
                out.append(f'<div class="history">{html.escape(section["history"])}</div>')
            for para in section["dropped"]:
                out.append(f'<div class="dropped">{html.escape(para)}</div>')
        elif doc is not None and doc["text"]:
            out.append(f'<div class="muted">not carved: {html.escape(doc["text"])}</div>')
        if doc is not None and doc["notes"]:
            notes = "\n".join(doc["notes"])
            muted = f"<div class='muted'>{html.escape(notes)}</div>"
            out.append(f"<details><summary>notes</summary>{muted}</details>")
        if n["toc"]:
            listed = ", ".join(f"{e['level']} {e['number']}" for e in n["toc"])
            out.append(
                f"<details><summary>table of contents</summary>{html.escape(listed)}</details>"
            )
        out.append("</div>")
    out.append("</main></body></html>")
    return "\n".join(out)
