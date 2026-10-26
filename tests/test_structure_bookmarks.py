from nomoscope.structure import bookmarks as bm


def test_split():
    cases = {
        "chapter 1: General Provisions": ("chapter", "1", "General Provisions"),
        "§\xa01-1 Legislative intent.": ("section", "1-1", "Legislative intent."),
        "§\xa0121-52.1 Solar.": ("section", "121-52.1", "Solar."),
        "§\xa03.26.010 Franchise granted.": ("section", "3.26.010", "Franchise granted."),
        "Title CHA: Charter": ("title", "CHA", "Charter"),
        "CHAPTER IV POWERS AND DUTIES": ("chapter", "IV", "POWERS AND DUTIES"),
        "chapter A216: Vehicle Use Policy": ("chapter", "A216", "Vehicle Use Policy"),
        "§\xa0SR.010 General Provisions.": ("section", "SR.010", "General Provisions."),
        "Ch.  1. General Provisions": ("ch", "1", "General Provisions"),
        "GENERAL REFERENCES": ("heading", None, "GENERAL REFERENCES"),
        "Legislation Adopted During Codification": (
            "heading", None, "Legislation Adopted During Codification"
        ),
    }  # fmt: skip
    for title, want in cases.items():
        assert bm.split(title) == want, title


def mark(ordinal, depth, title, page, top):
    return {"ordinal": ordinal, "depth": depth, "title": title, "page": page, "top": top}


MARKS = [
    mark(0, 0, "chapter 5: Fire", 0, 72.0),
    mark(1, 0, "chapter 5: Fire", 0, 72.0),
    mark(2, 1, "§\xa05-1 Purpose.", 0, 120.0),
    mark(3, 1, "§\xa05-2 Permits required for open burning.", 0, 700.0),
    mark(4, 1, "§\xa05-3 Missing.", 0, 300.0),
]


def line(page, y, text, **kw):
    return {"page": page, "y": y, "text": text, **kw}


LINES = [
    line(0, 40.0, "Town of X, NY"),
    line(0, 82.0, "Chapter 5"),
    line(0, 108.0, "FIRE"),
    line(0, 131.0, "§ 5-1. Purpose. The Town finds"),
    line(0, 144.0, "that fires happen."),
    line(1, 40.0, "§ 5-2"),
    line(1, 82.0, "§ 5-2. Permits required for open"),
    line(1, 95.0, "burning."),
    line(1, 120.0, "No person shall burn."),
]


def test_entries_drop_repeats():
    found = bm.entries(MARKS)
    assert [(e.ordinal, e.level, e.number) for e in found] == [
        (0, "chapter", "5"), (2, "section", "5-1"), (3, "section", "5-2"), (4, "section", "5-3"),
    ]  # fmt: skip
    assert found[0].label == "chapter 5"


def test_anchor():
    def skip(ln):
        return ln["y"] < 60

    anchored = {a.entry.number: a for a in bm.anchor(bm.entries(MARKS), LINES, skip)}
    # On the target page; the heading line FIRE follows the label line.
    assert (anchored["5"].line, anchored["5"].heading_lines) == (1, 2)
    # Run-in text after the heading stays as text.
    assert (anchored["5-1"].line, anchored["5-1"].rest) == (3, "The Town finds")
    # Target at the foot of page 0; the heading begins page 1, past the running header.
    assert (anchored["5-2"].line, anchored["5-2"].heading_lines) == (6, 2)
    # No line names 5-3 near its target.
    assert "5-3" not in anchored


def test_anchor_at_top_of_page():
    # Every bookmark targets y=20, above the page's first line, and
    # abbreviates its level word.
    marks = [mark(0, 0, "Ch.  1. General Provisions", 0, 20.0)]
    lines = [
        line(0, 114.0, "CHAPTER 1"),
        line(0, 140.0, "GENERAL PROVISIONS"),
        line(0, 171.0, "01-010: Short Title."),
    ]
    (a,) = bm.anchor(bm.entries(marks), lines, lambda ln: False)
    assert (a.line, a.heading_lines, a.rest) == (0, 2, "")
