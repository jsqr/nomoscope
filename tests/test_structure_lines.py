import polars as pl

from nomoscope.structure import lines, tables


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
