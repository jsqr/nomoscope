"""Build text lines with layout features from a PDF's text layer.

Fragments near one baseline form a row, split at wide horizontal gaps so
that most two-column tables of contents stay apart. Fragment widths are
estimated. Optional OCR blocks supply lines for pages without a text
layer, and give each line its block type.

Coordinates are PDF points from the top-left corner of the page.
"""

import re

import polars as pl

from nomoscope.structure import Row

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


def from_spans(spans: pl.DataFrame) -> pl.DataFrame:
    """Build lines from text-layer fragments."""
    body = pl.col("size").mode().first().over("source", "page")
    small = pl.col("size") < SUPERSCRIPT_SIZE * body
    s = (
        spans.with_columns(
            n=pl.col("text").str.len_chars(),
            end=pl.col("x") + pl.col("text").str.len_chars() * pl.col("size") * CHAR_WIDTH,
            baseline=pl.when(small).then(pl.col("y") + SUPERSCRIPT_RISE * body).otherwise("y"),
        )
        .sort("source", "page", "baseline", "x")
        .with_columns(
            row=(
                (pl.col("baseline").diff().fill_null(BASELINE_TOLERANCE + 1) > BASELINE_TOLERANCE)
                | (pl.col("page") != pl.col("page").shift(1))
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
            pl.col("source", "sha256", "page", "page_width", "page_height").first(),
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
                    "block_type": b["type"],
                    "block_ordinal": b["ordinal"],
                    "text": text,
                    "bold_spans": spans,
                }
            )
    return pl.DataFrame(rows, schema={k: SCHEMA[k] for k in rows[0]} if rows else None)


def attach_blocks(lines: pl.DataFrame, blocks: pl.DataFrame) -> pl.DataFrame:
    """Give each line the type and ordinal of the smallest OCR block containing it."""
    keyed = lines.with_row_index("key")
    hits = (
        keyed.select("key", "source", "page", "x0", "y")
        .join(
            blocks.select(
                "source",
                "page",
                "dpi",
                "x0",
                "y0",
                "x1",
                "y1",
                block_type="type",
                block_ordinal="ordinal",
            ),
            on=["source", "page"],
            suffix="_b",
        )
        .with_columns(scale=pl.col("dpi") / 72)
        .filter(
            (pl.col("y") * pl.col("scale")).is_between(
                pl.col("y0") - BLOCK_SLACK, pl.col("y1") + BLOCK_SLACK
            )
            & (pl.col("x0") * pl.col("scale")).is_between(
                pl.col("x0_b") - BLOCK_SLACK, pl.col("x1") + BLOCK_SLACK
            )
        )
        .with_columns(area=(pl.col("x1") - pl.col("x0_b")) * (pl.col("y1") - pl.col("y0")))
        .sort("area")
        .unique("key", keep="first")
        .select("key", "block_type", "block_ordinal")
    )
    return keyed.join(hits, on="key", how="left").drop("key")


def build(spans: pl.DataFrame, blocks: pl.DataFrame | None = None) -> pl.DataFrame:
    """Return every line in reading order, with block types where blocks are given."""
    if blocks is None or not blocks.height:
        pdf = from_spans(spans) if spans.height else None
        ocr = None
    else:
        pdf = attach_blocks(from_spans(spans), blocks) if spans.height else None
        covered = spans.select("source", "page").unique()
        ocr = from_blocks(blocks.join(covered, on=["source", "page"], how="anti"))
    parts = [df for df in (pdf, ocr) if df is not None and df.height]
    if not parts:
        return pl.DataFrame(schema=SCHEMA)
    lines = pl.concat(parts, how="diagonal_relaxed").sort("source", "page", "y", "x0")
    lines = lines.with_columns(
        ordinal=pl.int_range(pl.len()).over("source", "page"),
        gap_before=pl.when(pl.col("origin") == "pdf").then(
            pl.col("y").diff().over("source", "page")
        ),
    )
    missing = [pl.lit(None).alias(c) for c in SCHEMA if c not in lines.columns]
    return lines.with_columns(missing).select(SCHEMA.keys()).cast(SCHEMA)
