import polars as pl

from nomoscope.pdf import lines, tables


def span(page, x, y, text, size=11.0, bold=False):
    return {"source": "a.pdf", "sha256": "s", "page": page, "ordinal": 0, "page_width": 612.0,
            "page_height": 792.0, "x": x, "y": y, "size": size, "font": "Times",
            "bold": bold, "italic": False, "text": text}  # fmt: skip


SPANS = pl.DataFrame(
    [
        span(0, 280.0, 80.0, "Chapter 5", size=12.0, bold=True),
        span(0, 72.0, 120.0, "§"),
        span(0, 80.0, 120.0, "5-1"),
        span(0, 100.0, 120.0, "."),
        span(0, 150.0, 120.0, "Purpose."),
        span(0, 400.0, 120.0, "§ 5-9 . Fees."),
        span(0, 160.0, 116.5, "1", size=6.6),
        span(0, 96.0, 142.0, "Body text runs on."),
    ]
)
BLOCKS = pl.DataFrame(
    {"source": ["a.pdf"], "sha256": ["s"], "page": [0], "ordinal": [0], "type": ["table"],
     "x0": [380], "y0": [140], "x1": [560], "y1": [170], "width": [791], "height": [1023],
     "dpi": [93], "content": ["x"], "segment": ["s"]}
)  # fmt: skip


def test_from_spans():
    df = lines.build(SPANS, BLOCKS)
    assert df["text"].to_list() == [
        "Chapter 5",
        "§ 5-1 . Purpose. 1",
        "§ 5-9 . Fees.",
        "Body text runs on.",
    ]
    assert df["centered"].to_list() == [True, False, False, False]
    assert df["gap_before"].to_list()[:2] == [None, 40.0]
    assert df["block_type"].to_list() == [None, None, "table", None]
    assert df["y"].to_list()[1] == 120.0


def test_without_blocks():
    df = lines.build(SPANS)
    assert df.height == 4
    assert df["block_type"].to_list() == [None] * 4
    assert lines.build(SPANS.clear()).height == 0


def test_from_blocks_without_text_layer():
    blocks = pl.DataFrame(
        {"source": ["b.pdf"] * 2, "sha256": ["t"] * 2, "page": [0, 0], "ordinal": [0, 1],
         "type": ["title", "text"], "x0": [93, 93], "y0": [93, 186], "x1": [500, 500],
         "y1": [120, 220], "width": [791] * 2, "height": [1023] * 2, "dpi": [93] * 2,
         "content": ["# **§ 5-1. Purpose.**", "Line one\nLine two"], "segment": ["s"] * 2}
    )  # fmt: skip
    df = lines.build(SPANS.clear(), blocks)
    assert df["text"].to_list() == ["§ 5-1. Purpose.", "Line one", "Line two"]
    assert df["bold"].to_list() == [1.0, None, None]
    assert df["origin"].unique().to_list() == ["ocr"]
    assert df["x0"].to_list()[0] == 72.0


def test_superscript_joins_the_line_it_marks():
    # A footnote mark 4.9 points above its section heading formed a line of
    # its own, so the heading's gap above was 4.9 and it failed the section
    # level's gap_min.
    spans = pl.DataFrame(
        [
            span(0, 72.0, 100.0, "Body line."),
            span(0, 72.0, 130.0, "Heading."),
            span(0, 120.0, 125.1, "43", size=6.6),
            span(0, 72.0, 152.0, "Next."),
        ]
    )
    df = lines.build(spans, BLOCKS.clear())
    assert df["text"].to_list() == ["Body line.", "Heading. 43", "Next."]
    assert df["gap_before"].to_list() == [None, 30.0, 22.0]


def test_bold_spans_from_fragments():
    # A defined term is a bold fragment at the start of its line.
    spans = pl.DataFrame(
        [
            span(0, 72.0, 100.0, "Access,", bold=True),
            span(0, 110.0, 100.0, "Private.", bold=True),
            span(0, 160.0, 100.0, "Private access is", bold=False),
            span(0, 72.0, 122.0, "No bold here."),
        ]
    )
    df = lines.build(spans, BLOCKS.clear())
    assert df["text"].to_list()[0] == "Access, Private. Private access is"
    assert df["bold_spans"].to_list() == [[{"start": 0, "end": 16}], None]


def test_unbold():
    assert lines.unbold("**Access.** The provision") == (
        "Access. The provision",
        [{"start": 0, "end": 7}],
    )
    assert lines.unbold("A. **Access, Private.** Text") == (
        "A. Access, Private. Text",
        [{"start": 3, "end": 19}],
    )
    assert lines.unbold("**§ 5-1. Purpose.**") == ("§ 5-1. Purpose.", [{"start": 0, "end": 15}])
    assert lines.unbold("*Italic*") == ("Italic", [])


def test_an_ocr_table_replaces_its_text_layer_lines():
    spans = pl.DataFrame(
        [
            span(0, 72.0, 100.0, "Before the table."),
            span(0, 72.0, 130.0, "Use Spaces Home 2"),
            span(0, 72.0, 144.0, "per unit Shop 4"),
            span(0, 72.0, 180.0, "After the table."),
        ]
    )
    blocks = pl.DataFrame(
        {"source": ["a.pdf"] * 2, "sha256": ["s"] * 2, "page": [0, 0], "ordinal": [0, 1],
         "type": ["text", "table"], "x0": [93, 93], "y0": [120, 160], "x1": [500, 500],
         "y1": [135, 190], "width": [791] * 2, "height": [1023] * 2, "dpi": [93] * 2,
         "content": ["Before the table.",
                     "| Use | Spaces |\n| --- | --- |\n| Home per unit | 2 |\n| Shop | 4 |"]}
    )  # fmt: skip
    df = lines.build(spans, blocks)
    assert df["text"].to_list() == [
        "Before the table.",
        "| Use | Spaces |",
        "| Home per unit | 2 |",
        "| Shop | 4 |",
        "After the table.",
    ]
    assert df["block_type"].to_list()[1:4] == ["table"] * 3
    assert df["ordinal"].to_list() == [0, 1, 2, 3, 4]
    assert all(g >= 22 for g in df["gap_before"].to_list()[1:4])


def test_a_table_continued_on_the_next_page_is_one_table():
    blocks = [
        {"page": 0, "ordinal": 0, "type": "text", "content": "x"},
        {"page": 0, "ordinal": 1, "type": "table", "content": "| A | B |\n|---|---|\n| 1 | 2 |"},
        {"page": 0, "ordinal": 2, "type": "footer", "content": "p1"},
        {"page": 1, "ordinal": 0, "type": "header", "content": "h"},
        {"page": 1, "ordinal": 1, "type": "table", "content": "| A | B |\n|---|---|\n| 3 | 4 |"},
    ]
    [table] = tables.group(blocks)
    assert tables.rows(table) == [["A", "B"], ["1", "2"], ["3", "4"]]


def block(ordinal, kind, x0, y0, x1, y1, content="x"):
    """A block measured at 72 dpi, so its box is in points."""
    return {"source": "a.pdf", "sha256": "s", "page": 0, "ordinal": ordinal, "type": kind,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1, "width": 612, "height": 792, "dpi": 72,
            "content": content}  # fmt: skip


# A two-column page: each column's line starts at the same baseline as the other's.
TWO_COLUMN = pl.DataFrame(
    [
        span(0, 99.0, 100.0, "(c) Duties. The planning and"),
        span(0, 350.0, 100.0, "capital project investment"),
        span(0, 99.0, 112.0, "infrastructure committee"),
        span(0, 350.0, 112.0, "treatment and distribution"),
        span(0, 417.0, 140.0, "DIVISION 1. GENERALLY"),
    ]
)
# The left column's box runs past the page's middle, as with a wide inner margin.
TWO_COLUMN_BLOCKS = pl.DataFrame(
    [
        block(0, "text", 99, 90, 328, 120),
        block(1, "text", 350, 90, 600, 120),
        block(2, "title", 400, 130, 560, 145),
    ]
)


def test_a_two_column_page_reads_by_block():
    df = lines.build(TWO_COLUMN, TWO_COLUMN_BLOCKS)
    assert df["text"].to_list() == [
        "(c) Duties. The planning and",
        "infrastructure committee",
        "capital project investment",
        "treatment and distribution",
        "DIVISION 1. GENERALLY",
    ]
    assert df["gap_before"].to_list()[1:3] == [12.0, None]


def test_the_right_column_takes_the_left_columns_x():
    df = lines.build(TWO_COLUMN, TWO_COLUMN_BLOCKS)
    x0 = dict(zip(df["text"], df["x0"], strict=True))
    assert x0["capital project investment"] == x0["infrastructure committee"] == 99.0
    assert df.filter(pl.col("text") == "DIVISION 1. GENERALLY")["centered"].item()


def test_a_page_the_blocks_do_not_cover_reads_top_to_bottom():
    sparse = TWO_COLUMN_BLOCKS.filter(pl.col("ordinal") == 2)
    df = lines.build(TWO_COLUMN, sparse)
    assert df["text"].to_list()[:3] == [
        "(c) Duties. The planning and",
        "capital project investment",
        "infrastructure committee",
    ]


def test_a_table_in_a_column_keeps_its_place():
    spans = pl.DataFrame(
        [
            span(0, 99.0, 100.0, "Before the table."),
            span(0, 99.0, 130.0, "Use Spaces Home 2"),
            span(0, 350.0, 100.0, "Right column."),
        ]
    )
    blocks = pl.DataFrame(
        [
            block(0, "text", 99, 90, 300, 105),
            block(1, "table", 99, 120, 300, 140, "| Use | Spaces |\n| --- | --- |\n| Home | 2 |"),
            block(2, "text", 350, 90, 600, 105),
        ]
    )
    df = lines.build(spans, blocks)
    assert df["text"].to_list() == [
        "Before the table.",
        "| Use | Spaces |",
        "| Home | 2 |",
        "Right column.",
    ]


def test_a_page_whose_text_layer_is_glyph_numbers_reads_from_ocr():
    words = (
        "the community shall maintain records of every permit issued under this chapter and "
        "make them available for public inspection during regular business hours without charge"
    )
    glyphs = "".join(chr(ord(c) - 28) if c.isalpha() else c for c in words)
    spans = pl.DataFrame([span(0, 72.0, 100.0 + 12 * i, glyphs) for i in range(3)])
    blocks = pl.DataFrame([block(0, "text", 72, 90, 540, 140, f"{words} {words} again")])
    assert lines.build(spans, blocks)["text"].to_list() == [f"{words} {words} again"]
    readable = spans.with_columns(text=pl.lit(words))
    assert lines.build(readable, blocks)["text"].to_list() == [words] * 3


def test_a_centered_title_is_not_a_right_column():
    spans = pl.DataFrame(
        [span(0, 280.0, 80.0, "Chapter 3"), span(0, 72.0, 120.0, "Body text runs on.")]
    )
    blocks = pl.DataFrame(
        [block(0, "title", 280, 70, 335, 85), block(1, "text", 72, 110, 540, 130)]
    )
    assert lines.build(spans, blocks)["x0"].to_list() == [280.0, 72.0]


def test_lines_from_blocks_know_their_capitals():
    blocks = pl.DataFrame(
        [block(0, "title", 72, 90, 540, 100, "ARTICLE I. HEALTH NUISANCES"),
         block(1, "text", 72, 110, 540, 120, "Article I. Health Nuisances")]
    )  # fmt: skip
    df = lines.build(SPANS.clear(), blocks)
    assert df["caps"].to_list() == [True, False]


def test_a_title_page_whose_few_words_are_all_garbled_reads_from_ocr():
    words = "chapter twenty nine general assistance"
    glyphs = "".join(chr(ord(c) - 28) if c.isalpha() else c for c in words)
    spans = pl.DataFrame([span(0, 72.0, 100.0, glyphs)])
    blocks = pl.DataFrame([block(0, "title", 72, 90, 540, 110, words)])
    assert lines.build(spans, blocks)["text"].to_list() == [words]
