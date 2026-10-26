from nomoscope.derive import subdivide
from nomoscope.derive.subdivide import Enumerator, candidates, parse, succeeds
from tests.conventions import CONVENTIONS


def level(drafting, division):
    return subdivide.level(CONVENTIONS.levels.get(drafting), division)


# Penal Code 187, trimmed. Paragraphs under (b) have the same indent as (a).
PEN_187 = (
    "(a) Murder is the unlawful killing of a human being.\n"
    "(b) This section shall not apply if any of the following apply:\n"
    "(1) The act complied with the former Therapeutic Abortion Act.\n"
    "(2) The act was committed by a holder of a certificate.\n"
    "(3) It was an act or omission by the person pregnant with the fetus.\n"
    "(c) Subdivision (b) shall not be construed to prohibit prosecution."
)

# Business and Professions Code 17537.15, trimmed. One line starts two levels.
BPC_17537 = (
    '(a) For purposes of this section, "floral products" means cut flowers.\n'
    '(b) For the purposes of this section, "local telephone number" means a number.\n'
    "(c) (1) It is an infraction to misrepresent the location by doing either:\n"
    "(A) Listing a local telephone number in any advertisement.\n"
    "(B) Listing a fictitious business name if both of the following are met:\n"
    "(i) The name of the business misrepresents the location.\n"
    "(ii) The advertisement does not identify the true physical address.\n"
    "(2) Notwithstanding Sections 17534 and 17534.5, a violation is a misdemeanor."
)

# Elections Code 6000.2, trimmed. The lines of a form continue the subdivision.
ELEC_6000 = (
    "(a) A candidate shall provide proof in substantially the following form:\n"
    "GENERALLY ADVOCATED FOR OR RECOGNIZED CANDIDATE\n"
    "______, a candidate for the office of the President, has met one of the following:\n"
    "◻\tThe candidate is qualified for funding under the Federal Election Campaign Act.\n"
    "(b) The Secretary of State shall post the proof."
)

TX = (
    "Sec. 1.002.  PURPOSE.  (a) The purpose of this code is clarity.\n"
    "(1) First.\n(2) Second.\n(b) Next."
)
TX_INDENTS = [7, 13, 13, 7]


def paths(divisions):
    return ["/".join(d.path) for d in divisions]


def spans(text, divisions):
    return [text[d.start : d.end] for d in divisions]


def test_paragraphs_nest_under_their_subdivision_by_style():
    found = parse(PEN_187)
    assert paths(found) == ["a", "b", "b/1", "b/2", "b/3", "c"]
    assert [d.depth for d in found] == [1, 1, 2, 2, 2, 1]
    assert [d.position for d in found] == [1, 2, 1, 2, 3, 3]
    assert [d.sequence for d in found] == [1, 2, 3, 4, 5, 6]
    assert [d.parent for d in found] == [None, None, 1, 1, 1, None]


def test_a_span_runs_to_the_end_of_the_last_descendant():
    found = parse(PEN_187)
    by_path = dict(zip(paths(found), spans(PEN_187, found), strict=True))
    assert by_path["a"] == "(a) Murder is the unlawful killing of a human being."
    assert by_path["b"].startswith("(b) This section") and by_path["b"].endswith("the fetus.")
    assert by_path["b/2"] == "(2) The act was committed by a holder of a certificate."
    assert by_path["c"] == "(c) Subdivision (b) shall not be construed to prohibit prosecution."


def test_one_line_may_open_several_levels():
    found = parse(BPC_17537)
    assert paths(found) == [
        "a",
        "b",
        "c",
        "c/1",
        "c/1/A",
        "c/1/B",
        "c/1/B/i",
        "c/1/B/ii",
        "c/2",
    ]
    assert [d.style for d in found][2:] == [
        "lower_alpha",
        "digit",
        "upper_alpha",
        "upper_alpha",
        "lower_roman",
        "lower_roman",
        "digit",
    ]
    c, c1 = found[2], found[3]
    assert c.start < c1.start and c.end == found[-1].end and c1.end < found[-1].start
    assert BPC_17537[c1.start :].startswith("(1) It is an infraction")


def test_unenumerated_lines_continue_the_open_subdivision():
    found = parse(ELEC_6000)
    assert paths(found) == ["a", "b"]
    assert spans(ELEC_6000, found)[0].endswith("Federal Election Campaign Act.")


def test_indents_decide_depth_where_the_source_states_them():
    found = parse(TX, TX_INDENTS)
    assert paths(found) == ["a", "a/1", "a/2", "b"]
    assert spans(TX, found)[0].startswith("(a) The purpose") and found[0].start == 23
    # Without indents the parse is the same. The run-in heading is skipped either way.
    assert paths(parse(TX)) == paths(found)


def test_a_lettered_i_follows_h_and_a_roman_i_starts_a_sequence():
    text = "(h) Eight.\n(i) Nine.\n(1) One.\n(A) Cap.\n(i) Roman.\n(ii) Roman two.\n(j) Ten."
    found = parse(text)
    assert paths(found) == ["h", "i", "i/1", "i/1/A", "i/1/A/i", "i/1/A/ii", "j"]
    assert found[1].style == "lower_alpha" and found[4].style == "lower_roman"
    doubled = parse("(hh) One.\n(ii) Two.")
    assert [d.style for d in doubled] == ["lower_alpha", "lower_alpha"]


def test_doubled_letters_follow_z_and_a_suffix_continues_its_base():
    assert paths(parse("(y) Y.\n(z) Z.\n(aa) AA.\n(bb) BB.")) == ["y", "z", "aa", "bb"]
    assert paths(parse("(a) A.\n(a-1) A1.\n(a-2) A2.\n(b) B.")) == [
        "a",
        "a-1",
        "a-2",
        "b",
    ]
    assert succeeds(Enumerator("lower_alpha", 1, 2), Enumerator("lower_alpha", 2))
    assert not succeeds(Enumerator("lower_alpha", 1), Enumerator("digit", 2))


def test_a_repeated_enumerator_still_gets_a_unique_path():
    text = "(a) One.\n(b) Two.\n(c) Three.\n(b) Again.\n(c) Again."
    found = parse(text)
    assert paths(found) == ["a", "b", "c", "b~2", "c~2"]
    assert len(set(paths(found))) == len(found)


def test_a_restarted_sequence_is_a_sibling_not_a_child():
    text = "(a) A list:\n(1) One.\n(2) Two.\nAnother list:\n(1) One again.\n(2) Two again.\n(b) B."
    assert paths(parse(text)) == ["a", "a/1", "a/2", "a/1~2", "a/2~2", "b"]


def test_text_before_the_first_enumerator_belongs_to_no_subdivision():
    assert parse("A single unsubdivided line.") == []
    assert parse("Sec. 1-1. Short title. This Act may be cited as the Act.") == []
    il = "(5 ILCS 100/1-1) (from Ch. 127, par. 1001-1)\nSec. 1-5. Whenever.\n(a) A rule."
    found = parse(il)
    assert paths(found) == ["a"] and spans(il, found) == ["(a) A rule."]


def test_candidates_read_an_enumerator_every_way_it_could_be_meant():
    assert candidates("1") == [Enumerator("digit", 1)]
    assert candidates("a") == [Enumerator("lower_alpha", 1)]
    assert candidates("i") == [
        Enumerator("lower_alpha", 9),
        Enumerator("lower_roman", 1),
    ]
    assert candidates("iv") == [Enumerator("lower_roman", 4)]
    assert candidates("I") == [
        Enumerator("upper_alpha", 9),
        Enumerator("upper_roman", 1),
    ]
    assert candidates("aa") == [Enumerator("lower_alpha", 27)]
    assert candidates("a-5") == [Enumerator("lower_alpha", 1, 5)]
    assert candidates("ab") == []


def test_levels_are_the_conventions_words_by_rank():
    found = parse(BPC_17537)
    assert [level("ca", d) for d in found][2:6] == [
        "subdivision",
        "paragraph",
        "subparagraph",
        "subparagraph",
    ]
    assert level("ca", found[-3]) == "clause"
    assert level("il", found[-3]) == "item"
    assert level("us-xx", found[0]) is None
    # A California section can start with (1). By style, that is a paragraph.
    assert level("ca", parse("(1) One.\n(2) Two.")[0]) == "paragraph"


def test_texas_ranks_a_level_by_its_indent():
    # In a definitions section, (1) is at the second indent with no (a) above
    # it. A lower-case letter at the fifth indent is not a subsection.
    text = "Sec. 1.  DEFINITIONS.  In this chapter:\n(1) One.\n(A) A.\n(i) I.\n(a) Deep.\n(2) Two."
    found = parse(text, [7, 13, 19, 25, 31, 13])
    assert paths(found) == ["1", "1/A", "1/A/i", "1/A/i/a", "2"]
    assert [level("tx", d) for d in found] == [
        "subdivision",
        "paragraph",
        "subparagraph",
        "sub-subparagraph",
        "subdivision",
    ]
    # Without indents, the style gives the rank.
    assert [level("tx", d) for d in parse(text)][:2] == [
        "subdivision",
        "paragraph",
    ]


def test_a_parenthesized_word_is_not_an_enumerator():
    text = "(ab) Not one.\n(a) One."
    assert paths(parse(text)) == ["a"] and spans(text, parse(text)) == ["(a) One."]
    assert paths(parse("(a) One. (ab) Still one.\n(b) Two.")) == ["a", "b"]


CLOSING = (
    "(a) A person may appeal by doing both of the following:\n"
    "(1) Filing a notice within 30 days.\n"
    "(2) Paying the fee.\n"
    "If the person fails to do either, the order is final.\n"
    "(b) The board shall post the order."
)


def test_a_list_s_closing_lines_belong_to_the_parent():
    found = parse(CLOSING)
    by_path = dict(zip(paths(found), spans(CLOSING, found), strict=True))
    assert by_path["a/2"] == "(2) Paying the fee."
    assert by_path["a"].endswith("the order is final.")
    assert by_path["b"] == "(b) The board shall post the order."


def test_a_last_item_keeps_its_own_lines():
    """The last item keeps its extra lines when they start in lower case, when it
    has children, or when the list's other items have several lines.
    """
    lower = CLOSING.replace("If the person", "unless the person")
    assert spans(lower, parse(lower))[2].endswith("the order is final.")
    multi = CLOSING.replace(
        "(1) Filing a notice within 30 days.", "(1) Filing a notice.\nWithin 30 days."
    )
    assert spans(multi, parse(multi))[2].endswith("the order is final.")
    assert spans(ELEC_6000, parse(ELEC_6000))[0].endswith("Federal Election Campaign Act.")


def test_from_carved():
    rows = [
        {"path": "B", "level": "subsection", "style": "upper_alpha", "number": "B",
         "start_char": 30, "end_char": 40},
        {"path": "A", "level": "subsection", "style": "upper_alpha", "number": "A",
         "start_char": 0, "end_char": 29},
        {"path": "A/1", "level": "paragraph", "style": "arabic", "number": "1",
         "start_char": 10, "end_char": 29},
        {"path": "A/building", "level": "run_in", "style": None, "number": "BUILDING —",
         "start_char": 20, "end_char": 29},
    ]  # fmt: skip
    found = subdivide.from_carved(rows)
    assert [d.path for d in found] == [("A",), ("A", "1"), ("A", "building"), ("B",)]
    assert [d.parent for d in found] == [None, 0, 0, None]
    assert [d.position for d in found] == [1, 1, 2, 2]
    assert [d.sequence for d in found] == [1, 2, 3, 4]
    assert [d.depth for d in found] == [1, 2, 2, 1]
    assert [d.style for d in found] == ["upper_alpha", "digit", "run_in", "upper_alpha"]
    assert subdivide.level(None, found[1]) == "paragraph"
