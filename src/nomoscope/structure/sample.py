"""Render a sample of a document's lines for drafting its grammar.

`pages` chooses the first pages, pages for each heading style, and
seeded random pages. `render` prints each line with its layout
features. `INSTRUCTIONS` tells the drafter what a grammar needs.
"""

import random

import polars as pl

from nomoscope.structure import Row

PAGES = 24
FIRST_PAGES = 3
HEADING_PAGES = 8
MARKS = 60
CHARS_PER_TOKEN = 3.5

INSTRUCTIONS = """\
These are lines from a published municipal legal code. Each row is one
line of text with its layout, in points from the top-left of the page:

    y<baseline> x<left edge> s<font size> [B bold] [I italic] [C centered]
    [U all capitals] g<gap from the previous line> {<block type>} | text

Write the grammar of the code's hierarchy: every level, from the top
division down to the deepest enumerated subdivision, in that order.

- A division is a heading line: chapter, article, part, title, section.
  A subdivision is an enumerated paragraph inside a section: A., (1), (a).
- `label` is a template. `{n}` is the level's own number; `{<level>}`
  names the number of an enclosing level when the label repeats it, as in
  a section numbered within its chapter. Copy literal text and
  punctuation exactly as printed, and write a space wherever the printed
  label has one.
- `style` is how `{n}` is written. Use `decimal` when sections like 52.1
  appear between 52 and 53.
- `sequence`: "consecutive" when siblings count up by one, "increasing"
  when numbers rise with gaps (chapters often skip numbers), "none" when
  there is no order.
- `heading`: where a division's title is: on the label's line, on the
  next line, or none. Subdivisions have no heading; leave it out.
- `signals`: the layout features that set the level's label lines apart
  from body lines that happen to start the same way. Give `x` for
  subdivisions, measured from the sample, and `gap_min` when paragraphs
  are spaced wider than wrapped lines. Leave out what does not
  distinguish.
- `skip.top` and `skip.bottom`: y values, measured as in the rows. Lines
  with y below `top` are the running header, lines with y above `bottom`
  the running footer. Running text is never a level. `skip.note_size_max`:
  the largest font size of footnotes and other small print, which is
  kept as notes, not text.
- `sections.history`: regexes for the paragraphs that end a section and
  record the ordinances that made it. `sections.drop`: regexes for the
  paragraphs that are the publisher's notes, such as editor's notes and
  cross references, which are left out. `sections.levels`: division levels
  above the section, such as a definitions chapter with no sections, whose
  text is law and is carved as a section.
- Use the code's own words for level names, lowercase and singular.
  Two levels may share a label form when they differ in style or indent.
"""


def pages(lines: pl.DataFrame, count: int = PAGES, seed: int = 0) -> list[int]:
    """Choose `count` pages, sorted."""
    every = sorted(lines["page"].unique().to_list())
    first = every[:FIRST_PAGES]
    chosen = set(first) | set(heading_pages(lines))
    rest = [p for p in every if p not in chosen]
    random.Random(seed).shuffle(rest)
    chosen |= set(rest[: max(0, count - len(chosen))])
    return sorted(chosen)


def heading_pages(lines: pl.DataFrame, limit: int = HEADING_PAGES) -> list[int]:
    """Return pages that show each heading style, largest first.

    A heading line is larger than body text, or centered. Each style adds
    its first page and one from the middle of its range, so rare levels
    are shown.
    """
    sizes = lines["size"].drop_nulls()
    body = min(sizes.mode().to_list()) if sizes.len() else 0.0
    heads = (
        lines.filter((pl.col("size") > body + 0.5) | pl.col("centered"))
        .with_columns(
            style_size=pl.col("size").round(0),
            style_bold=pl.col("bold") >= 0.5,
        )
        .group_by("style_size", "style_bold", "centered", "caps")
        .agg(pages=pl.col("page").unique().sort(), n=pl.len())
        .sort("style_size", "n", descending=True)
    )
    out: list[int] = []
    for found in heads["pages"]:
        for p in (found[0], found[len(found) // 2]):
            if p not in out and len(out) < limit:
                out.append(p)
    return out


def _row(line: Row) -> str:
    def num(key: str, prefix: str) -> str:
        v = line.get(key)
        return f"{prefix}{v:.0f}" if v is not None else ""

    flags = [
        flag
        for flag, on in (
            ("B", (line.get("bold") or 0) >= 0.5),
            ("I", (line.get("italic") or 0) >= 0.5),
            ("C", line.get("centered")),
            ("U", line.get("caps")),
        )
        if on
    ]
    size = f"s{line['size']:g}" if line.get("size") is not None else ""
    block = f"{{{line['block_type']}}}" if line.get("block_type") else ""
    parts = [num("y", "y"), num("x0", "x"), size, *flags, num("gap_before", "g"), block]
    return " ".join(p for p in parts if p) + " | " + (line.get("text") or "")


def render(lines: pl.DataFrame, chosen: list[int]) -> str:
    out = []
    shown = lines.filter(pl.col("page").is_in(chosen)).sort("page", "ordinal")
    for (page,), group in shown.group_by("page", maintain_order=True):
        first = group.row(0, named=True)
        size = ""
        if first.get("page_width") is not None and first.get("page_height") is not None:
            size = f" ({first['page_width']:.0f} x {first['page_height']:.0f})"
        out.append(f"== page {page}{size}")
        out.extend(_row(line) for line in group.iter_rows(named=True))
    return "\n".join(out)


def render_marks(marks: list[Row], limit: int = MARKS) -> str:
    """Render the outline, one bookmark per row, indented by depth."""
    if not marks:
        return "== outline: none"
    rows = [f"== outline: {len(marks)} bookmarks"]
    rows.extend(f"{'  ' * m['depth']}{m['title']} (p{m['page']})" for m in marks[:limit])
    if len(marks) > limit:
        rows.append(f"... {len(marks) - limit} more")
    return "\n".join(rows)


def tokens(text: str) -> int:
    return round(len(text) / CHARS_PER_TOKEN)
