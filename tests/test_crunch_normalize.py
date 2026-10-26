from nomoscope.crunch import schemas


def test_normalize_keeps_symbols_and_folds_quotes_and_spaces():
    text = "“City”\xa0 means\tthe city.  \r\nSee § 1-1 — ½ acre."
    assert schemas.normalize(text) == '"City" means the city.\nSee § 1-1 — ½ acre.'


def test_normalize_removes_soft_hyphens():
    soft = "con­struction"
    assert schemas.normalize(soft) == "construction"
    assert schemas.text_sha256(schemas.normalize(soft)) == schemas.text_sha256("construction")
