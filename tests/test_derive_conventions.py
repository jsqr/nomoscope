import pytest

from nomoscope.derive import conventions
from nomoscope.derive.conventions import ConventionsError
from tests import conventions as data


def history(**raw):
    return {"x": {"history": {"format": "numeric_mdy", "which": "last", **raw}}}


def test_the_files_load_from_the_conventions_directory(tmp_path):
    data.write(tmp_path / "conventions")
    found = conventions.load(tmp_path / "conventions")
    assert found == data.CONVENTIONS
    assert sorted(found.phrases) == ["ca", "fake", "il", "tx"]
    assert conventions.load(None) == conventions.NONE
    assert conventions.load(tmp_path / "missing") == conventions.NONE


def test_placeholders_stand_for_the_date_patterns():
    found = conventions.parse(history(effective=r"\beff\. {NUMERIC_DATE}"))
    assert "(?P<date>" in found.phrases["x"]["effective"]


def test_bad_conventions_are_refused():
    with pytest.raises(ConventionsError, match="captures no date"):
        conventions.parse(history(effective=r"\beff\."))
    with pytest.raises(ConventionsError, match="unknown history keys"):
        conventions.parse(history(effectve=r"{NUMERIC_DATE}"))
    with pytest.raises(ConventionsError, match="format must be"):
        conventions.parse({"x": {"history": {"format": "ymd", "which": "last"}}})
    with pytest.raises(ConventionsError, match=r"levels\.indent"):
        conventions.parse({"x": {"levels": {"words": ["a"], "indent": [1]}}})
    with pytest.raises(ConventionsError, match="unknown tables"):
        conventions.parse({"x": {"histry": {}}})


def test_the_fingerprint_follows_the_content():
    one = conventions.parse({"x": {"levels": {"words": ["a"]}}})
    two = conventions.parse({"x": {"levels": {"words": ["b"]}}})
    assert one.fingerprint != two.fingerprint
    assert one.fingerprint == conventions.parse({"x": {"levels": {"words": ["a"]}}}).fingerprint
