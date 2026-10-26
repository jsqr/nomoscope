import polars as pl

from nomoscope.structure import checks, review
from nomoscope.structure import grammar as gr
from nomoscope.structure.parse import parse
from tests.structure import GRAMMAR, B, C, lines

RULES = gr.Sections(history=[r"^\(Ord\. "], drop=[r"^Editor's note"])
FOUND = lines(
    ("Chapter 5", 280, C),
    ("FIRE", 280, C),
    ("ARTICLE I", 280, C),
    ("General", 280, C),
    ("§ 5-1. Purpose.", 72, B),
    ("The Town finds that", 72),
    ("A. Permits <are> required.", 72),
    ("(1) Fees.", 96),
    ("Editor's note: Amended 2020.", 72),
    ("(Ord. 3, 2019)", 72),
    ("§ 5-3. Fees.", 72, B),
    ("Fees are set.", 72),
)


def page(source: str | None = None) -> str:
    g = GRAMMAR.model_copy(update={"sections": RULES})
    nodes = parse(FOUND, g)
    found = checks.run(nodes, pl.DataFrame(), g, FOUND)
    return review.render(nodes, FOUND, g, found, [("record", "r1")], source)


def test_offsets_from_the_section_level():
    assert review.offsets(GRAMMAR) == {
        "chapter": -2, "article": -1, "section": 0, "subsection": 1,
        "paragraph": 2, "subparagraph": 3, "clause": 4,
    }  # fmt: skip


def test_badges_label_level_offset_and_page():
    html = page()
    assert '<span class="badge ">chapter -2 p1</span>' in html
    assert '<span class="badge ">article -1 p1</span>' in html
    assert '<span class="badge b-section">section 0 p1</span>' in html
    assert '<span class="badge b-sub">subsection +1 p1</span>' in html
    assert '<span class="badge b-sub">paragraph +2 p1</span>' in html


def test_subdivisions_wrap_their_text_and_text_is_escaped():
    html = page()
    start = html.index('<span class="sub" id="5/I/5-1/A">')
    assert "A. Permits &lt;are&gt; required." in html[start:]
    inner = html.index('<span class="sub" id="5/I/5-1/A/1">')
    assert start < inner and "(1) Fees." in html[inner:]


def test_history_dropped_and_findings_are_shown():
    html = page()
    assert '<div class="history">(Ord. 3, 2019)</div>' in html
    assert '<div class="dropped">Editor&#x27;s note: Amended 2020.</div>' in html
    assert '<a href="#5/I/5-3">5/I/5-3</a>: gap: 5-1 -&gt; 5-3' in html


def test_page_links_when_the_source_has_pages():
    assert 'href="code.pdf#page=1"' in page("code.pdf")


def test_a_chapter_carved_as_a_section_is_marked():
    g = GRAMMAR.model_copy(update={"sections": gr.Sections(levels=["chapter"])})
    found = lines(("Chapter 6", 280, C), ("DEFINITIONS", 280, C), ("A. Building means a roof.", 72))
    html = review.render(parse(found, g), found, g, [], [("record", "r1")])
    assert 'chapter -2 p1</span><span class="badge b-section">carved as section</span>' in html
