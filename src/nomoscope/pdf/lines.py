"""Build text lines with layout features from a PDF's text layer.

Fragments near one baseline form a row, split at wide horizontal gaps so
that most two-column tables of contents stay apart. Fragment widths are
estimated. Optional OCR blocks supply lines for pages without a text
layer, and give each line its block type. A text-layer table, whose
printed rows mix its columns, is replaced by the rows of its OCR `table`
block, one line per row in markdown.

On a page whose text the blocks cover, the blocks also give the reading
order: each fragment goes to its block before rows are formed, so a row
never joins two blocks, and lines are read block by block in the OCR's
order. This is how a two-column page is read column by column. On such a
page, the right column's x is moved onto the left column's, so a line's x is its
place within its column, and `centered` is measured within the column. Other pages
are read top to bottom.

A page whose text layer does not hold the words its OCR read, as when a
font has no map to Unicode, is read from its OCR blocks like a page
without a text layer.

Coordinates are PDF points from the top-left corner of the page.
"""

import re

import polars as pl

from nomoscope.pdf import tables
from nomoscope.rows import Row

# pypdf gives a fragment's origin but not its width; Times averages about
# half an em per character.
CHAR_WIDTH = 0.5
BASELINE_TOLERANCE = 4.5
# A fragment smaller than SUPERSCRIPT_SIZE times the page's body size is a
# superscript. It sits about half an em above its line's baseline, outside
# BASELINE_TOLERANCE.
SUPERSCRIPT_SIZE = 0.8
SUPERSCRIPT_RISE = 0.5
# In ems; section headings put their title at a tab stop about 4 em out.
COLUMN_GAP = 6.0
CENTER_TOLERANCE = 0.04
CENTER_MIN_INDENT = 0.2
BLOCK_SLACK = 4.0
# A page is read in block order when its blocks hold this share of its
# text-layer characters.
BLOCK_COVERAGE = 0.9
# Block types that run across the top and bottom of a page, outside the columns.
RUNNING = ("header", "footer")
# A right column's blocks start past this share of the page's width.
RIGHT_START = 0.45

SCHEMA = pl.Schema(
    {
        "source": pl.String,
        "sha256": pl.String,
        "page": pl.Int32,
        "ordinal": pl.Int32,
        "origin": pl.String,
        "page_width": pl.Float64,
        "page_height": pl.Float64,
        "x0": pl.Float64,
        "x1": pl.Float64,
        "y": pl.Float64,
        "size": pl.Float64,
        "bold": pl.Float64,
        "italic": pl.Float64,
        "caps": pl.Boolean,
        "centered": pl.Boolean,
        "gap_before": pl.Float64,
        "block_type": pl.String,
        "block_ordinal": pl.Int32,
        "text": pl.String,
        "bold_spans": pl.List(pl.Struct({"start": pl.Int32, "end": pl.Int32})),
    }
)
_BOLD = re.compile(r"\*\*(.+?)\*\*")


BLOCK_COLUMNS = ("group", "block_type", "block_ordinal")


def from_spans(spans: pl.DataFrame) -> pl.DataFrame:
    """Build lines from text-layer fragments.

    Fragments with a `group` (from `in_blocks`) form rows only with their group.
    """
    if "group" not in spans.columns:
        spans = spans.with_columns(group=pl.lit(None, pl.Int64))
    carried = [c for c in BLOCK_COLUMNS if c in spans.columns]
    body = pl.col("size").mode().first().over("source", "page")
    small = pl.col("size") < SUPERSCRIPT_SIZE * body
    s = (
        spans.with_columns(
            n=pl.col("text").str.len_chars(),
            end=pl.col("x") + pl.col("text").str.len_chars() * pl.col("size") * CHAR_WIDTH,
            baseline=pl.when(small).then(pl.col("y") + SUPERSCRIPT_RISE * body).otherwise("y"),
        )
        .sort("source", "page", "group", "baseline", "x", nulls_last=False)
        .with_columns(
            row=(
                (pl.col("baseline").diff().fill_null(BASELINE_TOLERANCE + 1) > BASELINE_TOLERANCE)
                | (pl.col("page") != pl.col("page").shift(1))
                | pl.col("group").ne_missing(pl.col("group").shift(1))
            ).cum_sum()
        )
        .sort("row", "x")
        .with_columns(
            seg=(
                (pl.col("row") != pl.col("row").shift(1))
                | (pl.col("x") - pl.col("end").shift(1) > COLUMN_GAP * pl.col("size"))
            )
            .fill_null(True)
            .cum_sum()
        )
        .with_columns(
            # Where each fragment starts in the line's text, which joins them with spaces.
            offset=(pl.col("n") + 1).cum_sum().over("seg") - pl.col("n") - 1,
            run=(pl.col("bold") != pl.col("bold").shift(1).over("seg")).fill_null(True).cum_sum(),
        )
    )
    runs = (
        s.filter("bold")
        .group_by("seg", "run")
        .agg(start=pl.col("offset").min(), end=(pl.col("offset") + pl.col("n")).max())
        .sort("seg", "start")
        .group_by("seg", maintain_order=True)
        .agg(bold_spans=pl.struct("start", "end"))
    )
    return (
        s.group_by("seg", maintain_order=True)
        .agg(
            pl.col("source", "sha256", "page", "page_width", "page_height", *carried).first(),
            x0=pl.col("x").min(),
            x1=pl.col("end").max(),
            y=pl.col("y").sort_by("size", descending=True).first(),
            size=pl.col("size").max(),
            bold=(pl.col("n") * pl.col("bold")).sum() / pl.col("n").sum(),
            italic=(pl.col("n") * pl.col("italic")).sum() / pl.col("n").sum(),
            text=pl.col("text").str.join(" "),
        )
        .join(runs, on="seg", how="left", maintain_order="left")
        .with_columns(
            origin=pl.lit("pdf"),
            caps=pl.col("text").str.contains(r"\p{L}")
            & (pl.col("text") == pl.col("text").str.to_uppercase()),
            centered=(
                ((pl.col("x0") + pl.col("x1")) / 2 - pl.col("page_width") / 2).abs()
                < CENTER_TOLERANCE * pl.col("page_width")
            )
            & (pl.col("x0") > CENTER_MIN_INDENT * pl.col("page_width")),
        )
        .drop("seg")
    )


def unbold(text: str) -> tuple[str, list[Row]]:
    """Remove `**` markup from `text`, and return the character ranges it made bold."""
    out, spans, last = "", [], 0
    for m in _BOLD.finditer(text):
        out += text[last : m.start()]
        spans.append({"start": len(out), "end": len(out) + len(m.group(1))})
        out += m.group(1)
        last = m.end()
    out += text[last:]
    stripped = out.lstrip("* ")
    cut, end = len(out) - len(stripped), len(stripped.rstrip("* "))
    spans = [
        {"start": max(sp["start"] - cut, 0), "end": min(sp["end"] - cut, end)}
        for sp in spans
        if sp["end"] - cut > 0 and sp["start"] - cut < end
    ]
    return stripped[:end], spans


def from_blocks(blocks: pl.DataFrame) -> pl.DataFrame:
    """Build lines from OCR blocks, for pages without a text layer."""
    rows = []
    for b in blocks.filter(pl.col("type") != "image").iter_rows(named=True):
        texts = [t for t in b["content"].split("\n") if t.strip()]
        scale = 72 / b["dpi"]
        for i, raw in enumerate(texts):
            text, spans = unbold(raw.strip().lstrip("#").strip())
            bold = sum(sp["end"] - sp["start"] for sp in spans) / len(text) if text else 0
            rows.append(
                {
                    "source": b["source"],
                    "sha256": b["sha256"],
                    "page": b["page"],
                    "origin": "ocr",
                    "page_width": b["width"] * scale,
                    "page_height": b["height"] * scale,
                    "x0": b["x0"] * scale,
                    "x1": b["x1"] * scale,
                    "y": (b["y0"] + (i + 0.5) * (b["y1"] - b["y0"]) / len(texts)) * scale,
                    "bold": bold if spans else None,
                    "caps": any(c.isalpha() for c in text) and text == text.upper(),
                    "block_type": b["type"],
                    "block_ordinal": b["ordinal"],
                    "text": text,
                    "bold_spans": spans,
                }
            )
    return pl.DataFrame(rows, schema={k: SCHEMA[k] for k in rows[0]} if rows else None)


def points(blocks: pl.DataFrame) -> pl.DataFrame:
    """Blocks with their box in points, as `bx0`, `by0`, `bx1`, `by1`."""
    scale = 72 / pl.col("dpi")
    return blocks.with_columns(
        bx0=pl.col("x0") * scale,
        by0=pl.col("y0") * scale,
        bx1=pl.col("x1") * scale,
        by1=pl.col("y1") * scale,
    )


def in_blocks(spans: pl.DataFrame, blocks: pl.DataFrame) -> pl.DataFrame:
    """Give each fragment its block, and a `group` on pages read in block order.

    A fragment's block is the smallest one containing its start; it gives
    `block_type` and `block_ordinal`. On a page whose blocks hold
    BLOCK_COVERAGE of its characters, `group` is the block, or for a
    fragment outside every block the nearest block that is not a table.
    """
    keyed = spans.with_row_index("key")
    boxes = points(blocks).select(
        "source", "page", "type", "ordinal", "dpi", "bx0", "by0", "bx1", "by1"
    )
    slack = BLOCK_SLACK * 72 / pl.col("dpi")
    pairs = keyed.select("key", "source", "page", "x", "y", "text").join(
        boxes, on=["source", "page"]
    )
    inside = (
        pairs.filter(
            pl.col("y").is_between(pl.col("by0") - slack, pl.col("by1") + slack)
            & pl.col("x").is_between(pl.col("bx0") - slack, pl.col("bx1") + slack)
        )
        .with_columns(area=(pl.col("bx1") - pl.col("bx0")) * (pl.col("by1") - pl.col("by0")))
        .sort("area")
        .unique("key", keep="first")
        .select("key", block_type="type", block_ordinal="ordinal")
    )
    nearest = (
        pairs.filter(pl.col("type") != "table")
        .with_columns(
            distance=(
                pl.max_horizontal(pl.col("bx0") - pl.col("x"), 0, pl.col("x") - pl.col("bx1"))
            )
            ** 2
            + (pl.max_horizontal(pl.col("by0") - pl.col("y"), 0, pl.col("y") - pl.col("by1"))) ** 2
        )
        .sort("distance")
        .unique("key", keep="first")
        .select("key", nearest="ordinal")
    )
    found = keyed.join(inside, on="key", how="left").join(nearest, on="key", how="left")
    chars = pl.col("text").str.len_chars()
    covered = ((chars * pl.col("block_ordinal").is_not_null()).sum() / chars.sum()).over(
        "source", "page"
    ) >= BLOCK_COVERAGE
    return found.with_columns(
        group=pl.when(covered).then(pl.coalesce("block_ordinal", "nearest"))
    ).drop("key", "nearest")


# A word, for comparing a page's text layer with its OCR.
WORD = r"[a-z]{3,}"
# A page's text layer is unreadable when it holds less than this share of
# the words the OCR read there, on a page with at least OCR_WORDS_MIN of them,
# or none of them on a page with at least OCR_WORDS_FEW, such as a title page.
TEXT_LAYER_SHARED = 0.5
OCR_WORDS_MIN = 20
OCR_WORDS_FEW = 5


def unreadable(spans: pl.DataFrame, blocks: pl.DataFrame) -> pl.DataFrame:
    """The pages, as `source` and `page`, whose text layer does not hold the OCR's words.

    Some PDFs embed fonts without a map from character codes to Unicode,
    so the text layer holds glyph numbers: "community" reads "GSQQYRMX]".
    """

    def words(frame: pl.DataFrame, text: str) -> pl.DataFrame:
        return (
            frame.select(
                "source", "page", word=pl.col(text).str.to_lowercase().str.extract_all(WORD)
            )
            .explode("word")
            .drop_nulls("word")
            .unique()
        )

    ocr = words(blocks, "content")
    shared = ocr.join(words(spans, "text"), on=["source", "page", "word"], how="semi")
    counts = ocr.group_by("source", "page").agg(n=pl.len())
    hits = shared.group_by("source", "page").agg(hit=pl.len())
    return (
        counts.join(hits, on=["source", "page"], how="left")
        .filter(
            (
                (pl.col("n") >= OCR_WORDS_MIN)
                & (pl.col("hit").fill_null(0) < TEXT_LAYER_SHARED * pl.col("n"))
            )
            | ((pl.col("n") >= OCR_WORDS_FEW) & (pl.col("hit").fill_null(0) == 0))
        )
        .select("source", "page")
    )


def columns(lines: pl.DataFrame, blocks: pl.DataFrame) -> pl.DataFrame:
    """Move right-column lines onto the left column's x, and center within columns.

    Apart from the running header and footer, a right-column block starts
    past RIGHT_START of the page's width and has a block beside it: one
    that ends before it starts and overlaps it in height. A centered title
    has none. The left column's blocks end before the least right-column
    start. Margins differ between odd and even pages, so the columns need
    not meet at the page's middle. A block across both columns, such as a
    chapter title, keeps its x.
    """
    found = (
        points(blocks)
        .filter(~pl.col("type").is_in(RUNNING))
        .join(lines.select("source", "page", "page_width").unique(), on=["source", "page"])
    )
    beside = found.select("source", "page", lx1="bx1", ly0="by0", ly1="by1")
    starts = (
        found.filter(pl.col("bx0") >= RIGHT_START * pl.col("page_width"))
        .join(beside, on=["source", "page"])
        .filter(
            (pl.col("lx1") <= pl.col("bx0") + BLOCK_SLACK)
            & (pl.col("ly0") < pl.col("by1"))
            & (pl.col("ly1") > pl.col("by0"))
        )
        .group_by("source", "page")
        .agg(edge=pl.col("bx0").min())
    )
    sides = found.join(starts, on=["source", "page"], how="left").with_columns(
        side=pl.when(pl.col("bx0") >= pl.col("edge"))
        .then(pl.lit("right"))
        .when(pl.col("bx1") <= pl.col("edge") + BLOCK_SLACK)
        .then(pl.lit("left"))
    )
    pages = (
        sides.group_by("source", "page")
        .agg(
            left=pl.col("bx0").filter(pl.col("side") == "left").min(),
            right=pl.col("bx0").filter(pl.col("side") == "right").min(),
            end=pl.col("bx1").filter(pl.col("side") == "right").max(),
        )
        .filter(pl.col("left").is_not_null() & pl.col("right").is_not_null())
    )
    if not pages.height:
        return lines
    side = sides.select("source", "page", "side", group=pl.col("ordinal").cast(pl.Int64))
    width = pl.col("end") - pl.col("right")
    in_column = pl.col("side").is_not_null() & pl.col("right").is_not_null()
    shift = (
        pl.when(in_column & (pl.col("side") == "right"))
        .then(pl.col("right") - pl.col("left"))
        .otherwise(0.0)
    )
    return (
        lines.join(pages, on=["source", "page"], how="left")
        .join(side, on=["source", "page", "group"], how="left")
        .with_columns(x0=pl.col("x0") - shift, x1=pl.col("x1") - shift)
        .with_columns(
            centered=pl.when(in_column)
            .then(
                (
                    ((pl.col("x0") + pl.col("x1")) / 2 - (pl.col("left") + width / 2)).abs()
                    < CENTER_TOLERANCE * pl.col("page_width")
                )
                & (pl.col("x0") > pl.col("left") + CENTER_MIN_INDENT * width)
            )
            .otherwise("centered")
        )
        .drop("left", "right", "end", "side")
    )


def build(spans: pl.DataFrame, blocks: pl.DataFrame | None = None) -> pl.DataFrame:
    """Return every line in reading order, with block types where blocks are given."""
    if blocks is None or not blocks.height:
        pdf = from_spans(spans) if spans.height else None
        ocr = None
    else:
        if spans.height:
            spans = spans.join(unreadable(spans, blocks), on=["source", "page"], how="anti")
        pdf = columns(from_spans(in_blocks(spans, blocks)), blocks) if spans.height else None
        covered = spans.select("source", "page").unique()
        ocr = from_blocks(blocks.join(covered, on=["source", "page"], how="anti"))
        if ocr.height:
            ocr = ocr.with_columns(group=pl.col("block_ordinal").cast(pl.Int64))
    parts = [df for df in (pdf, ocr) if df is not None and df.height]
    if not parts:
        return pl.DataFrame(schema=SCHEMA)
    lines = pl.concat(parts, how="diagonal_relaxed")
    if "group" not in lines.columns:
        lines = lines.with_columns(group=pl.lit(None, pl.Int64))
    lines = lines.sort("source", "page", "group", "y", "x0", nulls_last=False)
    gap = pl.col("y").diff().over("source", "page")
    lines = lines.with_columns(
        ordinal=pl.int_range(pl.len()).over("source", "page"),
        # The top of the next column is not a paragraph gap.
        gap_before=pl.when((pl.col("origin") == "pdf") & (gap >= 0)).then(gap),
    )
    missing = [pl.lit(None).alias(c) for c in SCHEMA if c not in lines.columns]
    built = lines.with_columns(missing).select(SCHEMA.keys()).cast(SCHEMA)
    return tabled(built, blocks) if blocks is not None and blocks.height else built


# A table row starts a paragraph: its gap is this many line heights.
ROW_GAP = 2.0
# Rows of one table keep their order between the lines around it.
ROW_STEP = 0.001


def tabled(lines: pl.DataFrame, blocks: pl.DataFrame) -> pl.DataFrame:
    """Replace the text-layer lines of each OCR table with the table's rows.

    A table continued on the next page is one table; its rows go where its
    first line was. A table with no text-layer lines (a scanned page) keeps
    the lines read from its block.
    """
    rows = lines.to_dicts()
    order = blocks.sort("source", "page", "ordinal").to_dicts()
    drop: set[int] = set()
    added: list[Row] = []
    for table in tables.group(order):
        keys = {(b["source"], b["page"], b["ordinal"]) for b in table}
        hits = [
            i
            for i, r in enumerate(rows)
            if r["origin"] == "pdf" and (r["source"], r["page"], r["block_ordinal"]) in keys
        ]
        found = tables.rows(table)
        if not hits or not found:
            continue
        first = rows[hits[0]]
        size = first["size"] or 0.0
        for k, cells in enumerate(found):
            added.append(
                {
                    **{c: None for c in SCHEMA},
                    "source": first["source"],
                    "sha256": first["sha256"],
                    "page": first["page"],
                    "origin": "table",
                    "page_width": first["page_width"],
                    "page_height": first["page_height"],
                    "x0": first["x0"],
                    "x1": first["x1"],
                    "y": first["y"],
                    "ordinal": first["ordinal"] + k * ROW_STEP,
                    "size": size,
                    "gap_before": ROW_GAP * size,
                    "block_type": "table",
                    "block_ordinal": first["block_ordinal"],
                    "text": tables.markdown(cells),
                    "bold_spans": [],
                }
            )
        drop.update(hits)
    if not added:
        return lines
    kept = [r for i, r in enumerate(rows) if i not in drop]
    schema = SCHEMA | {"ordinal": pl.Float64}
    out = pl.DataFrame(kept + added, schema=schema).sort("source", "page", "ordinal")
    return out.with_columns(ordinal=pl.int_range(pl.len()).over("source", "page").cast(pl.Int32))
