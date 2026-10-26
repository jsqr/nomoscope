from nomoscope.crunch import reviews


def section(path: str, text: str, subdivisions: list | None = None, heading: str = "") -> dict:
    return {
        "node_treepath": path.rsplit("/", 1)[0], "section_num": path.rsplit("/", 1)[-1],
        "heading": heading, "history": None, "text": text, "subdivisions": subdivisions,
    }  # fmt: skip


A = {"path": "A", "level": "subsection", "style": "upper_alpha", "number": "A",
     "start_char": 0, "end_char": 5}  # fmt: skip


def test_changes_name_each_section_and_field():
    before = reviews.summary(
        [
            section("1/1-1", "Kept."),
            section("1/1-2", "Text."),
            section("1/1-3", "A. x", [A]),
            section("1/1-4", "Gone."),
        ]
    )
    after = reviews.summary(
        [
            section("1/1-1", "Kept."),
            section("1/1-2", "Text changed.", heading="New"),
            section("1/1-3", "A. x", [{**A, "number": "B"}]),
            section("1/1-5", "New."),
        ]
    )
    assert [(c["section_num"], c["change"]) for c in reviews.changes(before, after)] == [
        ("1-2", "heading, text"),
        ("1-3", "subdivisions"),
        ("1-5", "added"),
        ("1-4", "removed"),
    ]
    assert reviews.changes(before, before) == []


def test_a_repeated_section_number_is_compared_in_order():
    before = reviews.summary([section("5/5-1", "First."), section("5/5-1", "Second.")])
    after = reviews.summary([section("5/5-1", "First."), section("5/5-1", "Changed.")])
    assert [c["change"] for c in reviews.changes(before, after)] == ["text"]
