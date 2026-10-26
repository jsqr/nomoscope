from nomoscope.structure import grammar as gr
from nomoscope.structure.parse import parse, stacked
from tests.structure import GRAMMAR, RUN_IN, B, C, by_id, level, lines


def test_hierarchy():
    rows = parse(
        lines(
            ("Chapter 5", 280, C),
            ("FIRE PREVENTION", 240, C),
            ("ARTICLE I", 280, C),
            ("General Provisions", 260, C),
            ("§ 5-1. Purpose.", 72, B),
            ("The Town finds that", 72),
            ("A. Permits.", 72),
            ("(1) Fees.", 96),
            ("(a) Amount.", 120),
            ("(i) First.", 144),
            ("(ii) Second.", 144),
            ("(b) Waiver.", 120),
            ("(2) Terms.", 96),
            ("B. Inspections.", 72),
        ),
        GRAMMAR,
    )
    nodes = by_id(rows)
    assert nodes["5"]["heading"] == "FIRE PREVENTION"
    assert nodes["5/I"]["heading"] == "General Provisions"
    assert nodes["5/I/5-1"]["heading"] == "Purpose."
    assert nodes["5/I/5-1"]["text"] == "The Town finds that"
    assert nodes["5/I/5-1"]["refs"] == [{"level": "chapter", "value": "5"}]
    assert [r["id"] for r in rows if r["parent_id"] == "5/I/5-1/A/1/a"] == [
        "5/I/5-1/A/1/a/i",
        "5/I/5-1/A/1/a/ii",
    ]
    assert nodes["5/I/5-1/A/2"]["text"] == "Terms."
    assert nodes["5/I/5-1/B"]["parent_id"] == "5/I/5-1"


def test_i_after_h_is_a_letter():
    rows = parse(
        lines(("§ 5-1. Terms.", 72, B), *[(f"({c}) x", 120) for c in "abcdefghi"]),
        GRAMMAR,
    )
    assert by_id(rows)["5-1/i"]["level"] == "subparagraph"


def test_running_text_and_toc():
    rows = parse(
        lines(
            ("Town of Amenia, NY", 54, {"y": 39}),
            ("Chapter 5", 280, C),
            ("FIRE", 280, C),
            ("§ 5-1 . Purpose. § 5-2 . Permits.", 72, {"block_type": "table"}),
            ("§ 5-1. Purpose.", 72, B),
            ("Text.", 72),
            ("Downloaded from https://example.com/X", 54, {"y": 760}),
        ),
        GRAMMAR,
    )
    nodes = by_id(rows)
    assert set(nodes) == {"", "5", "5/5-1"}
    assert nodes["5"]["toc"] == [
        {"level": "section", "number": "5-1"},
        {"level": "section", "number": "5-2"},
    ]
    assert "Downloaded" not in nodes["5/5-1"]["text"]


def test_toc_without_ocr_label_folds():
    rows = parse(
        lines(
            ("Chapter 5", 280, C),
            ("FIRE", 280, C),
            ("§ 5-1. Purpose.", 72, B),
            ("§ 5-2. Permits.", 72, B),
            ("[HISTORY: Adopted 1990.]", 72),
            ("§ 5-1. Purpose.", 72, B),
            ("Text one.", 72),
            ("§ 5-2. Permits.", 72, B),
            ("Text two.", 72),
        ),
        GRAMMAR,
    )
    nodes = by_id(rows)
    assert [r["id"] for r in rows] == ["", "5", "5/5-1", "5/5-2"]
    assert nodes["5/5-1"]["text"] == "Text one."
    assert nodes["5"]["text"] == "[HISTORY: Adopted 1990.]"
    assert {e["number"] for e in nodes["5"]["toc"]} == {"5-1", "5-2"}


def test_paragraph_continues_across_pages():
    body = [(f"line {i}", 72) for i in range(18)]
    wrapped = ("more", 96, {"gap_before": 13.0})
    rows = parse(lines(("§ 5-1. Purpose.", 72, B), *body, ("A. Permits.", 72), wrapped), GRAMMAR)
    nodes = by_id(rows)
    assert nodes["5-1/A"]["text"] == "Permits. more"
    assert nodes["5-1/A"]["page_first"] == 0 and nodes["5-1/A"]["page_last"] == 1
    assert nodes["5-1"]["line_last"] == 20


def test_nested_toc_folds_until_stable():
    # A charter's table of contents lists chapters with their sections. A
    # TOC chapter has children, so one fold pass did not remove it, and its
    # sections are numbered under a different node.
    charter = GRAMMAR.model_copy(
        update={
            "levels": [
                level(name="chapter", kind="division", label="CHAPTER {n}", style="upper_roman",
                      heading="next_line", sequence="increasing"),
                level(name="section", kind="division", label="Section {n}.", heading="same_line",
                      signals=gr.Signals(bold=True)),
            ]
        }
    )  # fmt: skip
    rows = parse(
        lines(
            ("CHAPTER I", 280, C), ("NAME", 280, C),
            ("Section 1. Title.", 72, B), ("Section 2. Name.", 72, B),
            ("CHAPTER II", 280, C), ("COUNCIL", 280, C),
            ("Section 3. Meetings.", 72, B),
            ("CHAPTER I", 280, C), ("NAME", 280, C),
            ("Section 1. Title.", 72, B), ("Text.", 72),
            ("Section 2. Name.", 72, B), ("Text.", 72),
            ("CHAPTER II", 280, C), ("COUNCIL", 280, C),
            ("Section 3. Meetings.", 72, B), ("Text.", 72),
        ),
        charter,
    )  # fmt: skip
    assert [r["id"] for r in rows] == ["", "I", "I/1", "I/2", "II", "II/3"]
    assert {(e["level"], e["number"]) for e in rows[0]["toc"]} >= {
        ("chapter", "I"),
        ("chapter", "II"),
    }


def test_toc_entries_that_name_their_chapter_fold_anywhere():
    # A title's table of contents lists sections such as "3.26.010" before
    # any chapter line the grammar matches. The entries parse as children of
    # the title; the real sections are children of chapter 3.26.
    rows = parse(
        lines(
            ("§ 5-1. Purpose.", 72, B), ("§ 5-2. Fees.", 72, B),
            ("Chapter 5", 280, C), ("FIRE", 280, C),
            ("§ 5-1. Purpose.", 72, B), ("Text.", 72),
            ("§ 5-2. Fees.", 72, B), ("Text.", 72),
        ),
        GRAMMAR,
    )  # fmt: skip
    assert [r["id"] for r in rows] == ["", "5", "5/5-1", "5/5-2"]


def test_divisions_from_bookmarks_keep_charter_and_code_apart():
    # A charter's CHAPTER I / Section 1 and the code's Title 1 / Chapter
    # 1.01 nest by bookmark depth, with no grammar parts.
    def mark(ordinal, depth, title, page, top):
        return {"ordinal": ordinal, "depth": depth, "title": title, "page": page, "top": top}

    marks = [
        mark(0, 0, "Title CHA: Charter", 0, 60.0),
        mark(1, 1, "CHAPTER I NAME", 0, 114.0),
        mark(2, 2, "Section\xa01 Title.", 0, 158.0),
        mark(3, 0, "Title 1: General", 0, 246.0),
        mark(4, 1, "Chapter 5: Fire", 0, 290.0),
        mark(5, 2, "§\xa05-1 Purpose.", 0, 334.0),
    ]
    rows = parse(
        lines(
            ("Title CHA", 280, C), ("CHARTER", 280, C),
            ("CHAPTER I", 280, C), ("NAME", 280, C),
            ("Section 1. Title.", 72, B), ("The City is named.", 72),
            ("A. First.", 72), ("(1) Item.", 96),
            ("Title 1", 280, C), ("GENERAL", 280, C),
            ("Chapter 5", 280, C), ("FIRE", 280, C),
            ("§ 5-1. Purpose.", 72, B), ("A. Permits.", 72),
        ),
        GRAMMAR,
        marks,
    )  # fmt: skip
    nodes = by_id(rows)
    assert [r["id"] for r in rows] == [
        "", "CHA", "CHA/I", "CHA/I/1", "CHA/I/1/A", "CHA/I/1/A/1",
        "1", "1/5", "1/5/5-1", "1/5/5-1/A",
    ]  # fmt: skip
    assert nodes["CHA/I/1"]["text"] == "The City is named."
    assert nodes["CHA/I"]["heading"] == "NAME"
    assert nodes["1/5/5-1"]["refs"] == [{"level": "chapter", "value": "5"}]
    assert nodes["CHA"]["origin"] == "bookmark" and nodes["CHA"]["style"] is None
    assert nodes["1/5/5-1/A"]["origin"] == "subdivision"

    labels_only = parse(
        lines(("Chapter 5", 280, C), ("FIRE", 280, C), ("§ 5-1. Purpose.", 72, B)),
        GRAMMAR.model_copy(update={"bookmarks": False}),
        marks,
    )
    assert [r["origin"] for r in labels_only] == ["root", "label", "label"]


def test_sections_from_labels_below_chapter_bookmarks():
    # The bookmarks name chapters only. Sections come from labels; the
    # chapter's list of its sections folds into its toc, and a label naming
    # another chapter (a zone code) is no section.
    g = gr.Grammar(
        skip=gr.Skip(top=70, bottom=752),
        levels=[
            level(name="chapter", kind="division", label="CHAPTER {n}", heading="next_line"),
            level(name="section", kind="division", label="{chapter}-{n}:", heading="same_line",
                  sequence="increasing"),
            level(name="subsection", label="{n}.", style="upper_alpha"),
        ],
    )  # fmt: skip
    marks = [
        {"ordinal": 0, "depth": 0, "title": " Preface", "page": 0, "top": 20.0},
        {"ordinal": 1, "depth": 0, "title": "Ch.  1. General", "page": 0, "top": 20.0},
        {"ordinal": 2, "depth": 0, "title": "Ch.  8. Zones", "page": 1, "top": 20.0},
    ]
    rows = parse(
        lines(
            ("PREFACE", 280),
            ("CHAPTER 1", 280), ("GENERAL", 280),
            ("01-010: Title.", 92), ("01-020: Purpose.", 92),
            ("01-010:  TITLE", 92), ("This is the code.", 92),
            ("01-020:  PURPOSE", 92), ("A. Order.", 116), ("B. Health.", 116),
            *[("", 92)] * 10,
            ("CHAPTER 8", 280), ("ZONES", 280),
            ("08-010:  ZONES", 92), ("AG-20: Agricultural Zone", 92),
        ),
        g,
        marks,
    )  # fmt: skip
    nodes = by_id(rows)
    assert [r["id"] for r in rows] == [
        "", "?", "1", "1/01-010", "1/01-020", "1/01-020/A", "1/01-020/B", "8", "8/08-010",
    ]  # fmt: skip
    assert [r["origin"] for r in rows[1:4]] == ["bookmark", "bookmark", "label"]
    assert nodes["1"]["level"] == "chapter" and nodes["1"]["number"] == "1"
    assert nodes["1"]["toc"] == [
        {"level": "section", "number": "01-010"}, {"level": "section", "number": "01-020"},
    ]  # fmt: skip
    assert nodes["1/01-010"]["text"] == "This is the code."
    assert nodes["8/08-010"]["text"] == "AG-20: Agricultural Zone"


def test_run_in():
    # Lettered items restart under each defined term.
    def bold(n):
        return {"bold_spans": [{"start": 0, "end": n}]}

    rows = parse(
        lines(
            ("§ 1. Definitions.", 72, B), ("Terms have these meanings:", 72),
            ("Access. The way in", 72, bold(7)), ("to a lot.", 72, {"gap_before": 13.0}),
            ("A. Access, Private. Not public.", 96, {"bold_spans": [{"start": 3, "end": 19}]}),
            ("B. Access, Public. Public.", 96),
            ("BUILDING — A structure.", 72),
            ("A. It has a roof.", 96),
            ("§ 2. Permits.", 72, B),
            ("Notice. Give notice.", 72, bold(7)),
            ("A. Fees.", 96),
        ),
        RUN_IN,
    )  # fmt: skip
    nodes = by_id(rows)
    assert [r["id"] for r in rows] == [
        "", "1", "1/access", "1/access/A", "1/access/B", "1/building", "1/building/A",
        "2", "2/A",
    ]  # fmt: skip
    access = nodes["1/access"]
    assert (access["kind"], access["level"], access["origin"]) == (
        "subdivision",
        "run_in",
        "run_in",
    )
    assert (access["label"], access["text"]) == ("Access.", "The way in to a lot.")
    assert nodes["1/access/A"]["text"] == "Access, Private. Not public."
    assert nodes["1/building"]["label"] == "BUILDING —"
    # Outside the divisions that `within` names, a bold run-in heading is text.
    assert nodes["2"]["text"] == "Notice. Give notice."


def test_run_in_nests_in_an_item_further_left():
    # The terms are indented under item A., and B. follows the last term.
    rows = parse(
        lines(
            ("§ 1. Definitions.", 72, B),
            ("A. As used in this chapter:", 72),
            ("PUBLIC PLACE — Any open place.", 96),
            ("STREET — Any road.", 96),
            ("B. The word shall is mandatory.", 72),
        ),
        RUN_IN,
    )  # fmt: skip
    assert [r["id"] for r in rows] == ["", "1", "1/A", "1/A/public-place", "1/A/street", "1/B"]


def test_stacked_labels_open_a_first_child():
    rows = parse(
        lines(
            ("Chapter 5", 280, C),
            ("FIRE", 280, C),
            ("§ 5-1. Purpose.", 72, B),
            ("A. (1) Fees are due.", 72),
            ("(2) Fees are paid.", 96),
            ("B. (1.5) acres.", 72),
        ),
        GRAMMAR,
    )
    nodes = by_id(rows)
    assert nodes["5/5-1/A"]["text"] == ""
    assert nodes["5/5-1/A/1"]["text"] == "Fees are due."
    assert nodes["5/5-1/A/2"]["parent_id"] == "5/5-1/A"
    assert nodes["5/5-1/B"]["text"] == "(1.5) acres."
    assert [p["start"] for p in nodes["5/5-1/A/1"]["pieces"]] == [3]


def test_stacked_label_needs_a_space():
    g = gr.Grammar(
        levels=[level(name="subsection", label="{n}.", style="upper_alpha"),
                level(name="paragraph", label="{n}.")],
    )  # fmt: skip
    ms = gr.matchers(g)
    ranks = {"subsection": 0, "paragraph": 1}
    outer = gr.match(ms[0], "A. 1. Fees.")
    assert outer is not None
    found = stacked(ms, ranks, outer, "1. Fees.")
    assert found is not None and found.number == "1"
    assert stacked(ms, ranks, outer, "1.5 acres.") is None
    assert stacked(ms, ranks, outer, "2. Fees.") is None


def test_small_print_is_notes():
    g = GRAMMAR.model_copy(update={"skip": gr.Skip(top=70, bottom=752, note_size_max=8.5)})
    rows = parse(
        lines(
            ("§ 5-1. Purpose.", 72, B),
            ("The Town finds that", 72),
            ("1. Editor's Note: Amended 2020.", 72, {"size": 8.0}),
            ("fires happen.", 72, {"gap_before": 13.0}),
        ),
        g,
    )
    section = by_id(rows)["5-1"]
    assert section["text"] == "The Town finds that fires happen."
    assert section["notes"] == ["1. Editor's Note: Amended 2020."]


def test_levels_named_by_depth():
    g = GRAMMAR.model_copy(update={"sections": gr.Sections(level_names="depth")})
    rows = parse(
        lines(
            ("§ 5-1. Usual.", 72, B), ("A. First.", 72), ("(1) Item.", 96),
            ("§ 5-2. Numbers first.", 72, B), ("(1) Item.", 96), ("(a) Sub.", 120),
            ("§ 5-3. Letters first.", 72, B), ("(a) Sub.", 120), ("(1) Item.", 96),
        ),
        g,
    )  # fmt: skip
    nodes = by_id(rows)
    assert (nodes["5-1/A"]["level"], nodes["5-1/A/1"]["level"]) == ("subsection", "paragraph")
    assert (nodes["5-2/1"]["level"], nodes["5-2/1/a"]["level"]) == ("subsection", "paragraph")
    assert nodes["5-2/1"]["style"] == "arabic"
    assert nodes["5-3/a"]["level"] == "subsection"


def test_followed_by_keeps_the_word_in_the_definition():
    g = RUN_IN.model_copy(
        update={
            "run_in": gr.RunIn(
                forms=[gr.Form(form="{lead}.", followed_by="Means")],
            )
        }
    )
    rows = parse(
        lines(
            ("§ 1. Definitions.", 72, B),
            ("In this chapter the following terms apply.", 72),
            ("City Official. Means a person who holds office.", 72),
            ("Organization. Means any business.", 72),
        ),
        g,
    )
    nodes = by_id(rows)
    assert [r["id"] for r in rows] == ["", "1", "1/city-official", "1/organization"]
    assert nodes["1/city-official"]["label"] == "City Official."
    assert nodes["1/city-official"]["text"] == "Means a person who holds office."
    assert nodes["1"]["text"] == "In this chapter the following terms apply."


def test_a_correction_reads_a_misprinted_label():
    g = GRAMMAR.model_copy(
        update={"corrections": [gr.Correction(page=1, printed="b. Second.", reads="B. Second.")]}
    )
    rows = parse(
        lines(("§ 5-1. Terms.", 72, B), ("A. First.", 72), ("b. Second.", 72), ("C. Third.", 72)),
        g,
    )
    nodes = by_id(rows)
    assert [r["id"] for r in rows if r["parent_id"] == "5-1"] == ["5-1/A", "5-1/B", "5-1/C"]
    assert (nodes["5-1/B"]["label"], nodes["5-1/B"]["text"]) == ("B.", "Second.")
