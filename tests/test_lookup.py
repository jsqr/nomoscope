import json

import pytest

from nomoscope import chat
from nomoscope.lookup import LookupsError, cache, excerpt
from nomoscope.lookup import run as looking
from nomoscope.lookup.tavily import Page
from nomoscope.providers import ProviderError
from tests import fake

PAGE = """\
Town Law > Article 9 > Section 130
\u00a7 130. Building code; zoning and planning. 1. The town board may adopt
ordinances regulating the construction of buildings.
  (a) Such ordinances may provide for permits.
  (b) Such ordinances may provide for inspections.
2. Penalties for violations shall be set by the town board.
Disclaimer: this page is provided for information only and is not legal advice.
Disclaimer: this page is provided for information only and is not legal advice.
Disclaimer: this page is provided for information only and is not legal advice.
Disclaimer: this page is provided for information only and is not legal advice.
Disclaimer: this page is provided for information only and is not legal advice.
Disclaimer: this page is provided for information only and is not legal advice.
Disclaimer: this page is provided for information only and is not legal advice.
Disclaimer: this page is provided for information only and is not legal advice.
Disclaimer: this page is provided for information only and is not legal advice.
Disclaimer: this page is provided for information only and is not legal advice.
Disclaimer: this page is provided for information only and is not legal advice.
Disclaimer: this page is provided for information only and is not legal advice.
"""


def NO_EXTRACT(urls):
    raise AssertionError(f"unexpected extraction of {urls}")


SETTINGS = looking.Settings(fake.CHAT, domains=("law.justia.com",), results=2)
TARGET = "us-ny/law/TWN/130"
REQUEST = looking.Request(TARGET, "\u00a7 130 of the Town Law", "N.Y. Town Law \u00a7 130")


def answering(text: str | None, scope: str = "section"):
    sent = []

    def complete(messages):
        sent.append(messages)
        body = {"found": text is not None, "scope": scope, "start": text, "end": text}
        return chat.Completion(json.dumps(body), 1000, 100)

    return complete, sent


def searching(*results: list[Page]):
    calls = []
    replies = iter(results)

    def search(query, domains, n):
        calls.append((query, domains, n))
        return next(replies, [])

    return search, calls


def test_an_excerpt_is_the_pages_own_text_where_the_copy_is_found():
    copy = "\u00a7 130. Building code; zoning and planning. 1. The town board may adopt"
    kept = excerpt.checked(
        excerpt.Reply(found=True, scope="section", start=copy, end=copy), PAGE, "130", []
    )
    assert kept is not None and kept[0].startswith("\u00a7 130.") and kept[1] == "section"


def test_an_invented_or_misplaced_excerpt_is_rejected():
    invented = excerpt.Reply(
        found=True,
        scope="section",
        start="\u00a7 130. Towns may levy any tax.",
        end="\u00a7 130. Towns may levy any tax.",
    )
    assert excerpt.checked(invented, PAGE, "130", []) is None
    wrong_start = excerpt.Reply(
        found=True,
        scope="section",
        start="The town board may adopt",
        end="The town board may adopt",
    )
    assert excerpt.checked(wrong_start, PAGE, "130", []) is None
    sub = excerpt.Reply(
        found=True,
        scope="subdivision",
        start="(b) Such ordinances may provide",
        end="(b) Such ordinances may provide",
    )
    assert excerpt.checked(sub, PAGE, "130", ["b"]) is not None
    assert excerpt.checked(sub, PAGE, "130", ["a"]) is None


def test_a_long_page_is_cut_around_the_section():
    page = "x " * 40_000 + PAGE
    assert "\u00a7 130." in excerpt.window(page, "130")
    assert len(excerpt.window(page, "130")) == excerpt.PAGE_CHARS


def test_a_found_lookup_is_cached_and_reused(cfg):
    search, calls = searching([Page("https://law.justia.com/x", "Town Law 130", PAGE)])
    complete, _ = answering("\u00a7 130. Building code; zoning and planning.")
    first = looking.lookup(cfg, SETTINGS, search, NO_EXTRACT, complete, "test", REQUEST)
    assert first.searched and first.found is not None
    assert first.found.url == "https://law.justia.com/x"
    assert first.dollars == pytest.approx(
        SETTINGS.dollars_per_search + fake.CHAT.dollars(1000, 100)
    )
    assert calls == [("N.Y. Town Law \u00a7 130", ("law.justia.com",), 2)]
    again = looking.lookup(cfg, SETTINGS, search, NO_EXTRACT, complete, "test", REQUEST)
    assert not again.searched and again.found == first.found and len(calls) == 1
    [row] = cache.held(cfg, "us-ny", excerpt.VERSION).values()
    assert row["found"] and row["searches"] == 1


def test_a_miss_tries_any_domain_then_is_cached(cfg):
    search, calls = searching([], [Page("https://example.org/t", "T", PAGE)])
    complete, sent = answering(None)
    miss = looking.lookup(cfg, SETTINGS, search, NO_EXTRACT, complete, "test", REQUEST)
    assert miss.found is None and [c[1] for c in calls] == [("law.justia.com",), None]
    assert len(sent) == 1
    looking.lookup(cfg, SETTINGS, search, NO_EXTRACT, complete, "test", REQUEST)
    assert len(calls) == 2
    assert not cache.held(cfg, "us-ny", excerpt.VERSION)[TARGET]["found"]


def test_a_failed_search_is_not_cached(cfg):
    def failing(query, domains, n):
        raise ProviderError("busy", 503)

    complete, _ = answering(None)
    out = looking.lookup(cfg, SETTINGS, failing, NO_EXTRACT, complete, "test", REQUEST)
    assert out.found is None and out.searched
    assert cache.held(cfg, "us-ny", excerpt.VERSION) == {}


def test_without_leave_to_search_only_the_cache_is_read(cfg):
    search, calls = searching([Page("https://law.justia.com/x", "T", PAGE)])
    complete, _ = answering("\u00a7 130. Building code")
    out = looking.lookup(
        cfg, SETTINGS, search, NO_EXTRACT, complete, "test", REQUEST, may_search=False
    )
    assert out == looking.Looked(None) and calls == []


def test_a_query_without_a_bluebook_form_names_the_place():
    r = looking.Request(TARGET, "\u00a7 130 of the Town Law", None, "New York")
    assert looking.query(r) == "New York \u00a7 130 of the Town Law"


def test_lookup_settings_need_a_model():
    with pytest.raises(chat.ChatError, match=r"\[lookups\] model is not set"):
        looking.settings({}, {fake.CHAT.slug: fake.CHAT})
    with pytest.raises(LookupsError, match="domain: unknown"):
        looking.settings({"model": fake.CHAT.slug, "domain": []}, {fake.CHAT.slug: fake.CHAT})
    found = looking.settings({"model": fake.CHAT.slug, "results": 5}, {fake.CHAT.slug: fake.CHAT})
    assert found.results == 5 and found.domains == looking.DOMAINS
    assert looking.estimate(found) > 2 * found.dollars_per_search


def test_a_section_number_in_a_title_line_above_the_copy_counts():
    page = (
        "# New York Consolidated Laws, Town Law - TWN \u00a7 267. Zoning board of appeals\n\n"
        "Current as of January 01, 2026\n\n1. Definitions. As used in this section:\n"
        "(a) Use variance shall mean the authorization of a use.\n"
    )
    body = "1. Definitions. As used in this section:\n(a) Use variance shall mean"
    reply = excerpt.Reply(found=True, scope="section", start=body, end=body)
    kept = excerpt.checked(reply, page, "267", [])
    assert kept is not None and kept[0].startswith("1. Definitions.")
    far = "\u00a7 267. Title\n" + "x " * 400 + body
    assert excerpt.checked(reply, far, "267", []) is None


def test_a_row_under_an_older_check_is_looked_up_again(cfg, monkeypatch):
    search, calls = searching([], [], [Page("https://law.justia.com/x", "T", PAGE)])
    complete, _ = answering(None)
    looking.lookup(cfg, SETTINGS, search, NO_EXTRACT, complete, "test", REQUEST)
    assert len(calls) == 2
    monkeypatch.setattr(excerpt, "VERSION", "next")
    found, _ = answering("\u00a7 130. Building code; zoning and planning.")
    again = looking.lookup(cfg, SETTINGS, search, NO_EXTRACT, found, "test", REQUEST)
    assert again.searched and again.found is not None


def test_a_short_page_is_read_whole_before_its_excerpt(cfg):
    search, _ = searching([Page("https://codes.findlaw.com/ny/twn-130", "T", "Town Law 130")])
    extracted = []

    def extract(urls):
        extracted.append(list(urls))
        return {"https://codes.findlaw.com/ny/twn-130": PAGE}

    complete, sent = answering("\u00a7 130. Building code; zoning and planning.")
    out = looking.lookup(cfg, SETTINGS, search, extract, complete, "test", REQUEST)
    assert out.found is not None and extracted == [["https://codes.findlaw.com/ny/twn-130"]]
    assert "\u00a7 130. Building code" in sent[0][-1]["content"]
    [row] = cache.held(cfg, "us-ny", excerpt.VERSION).values()
    assert row["extracts"] == 1
    assert out.dollars == pytest.approx(
        SETTINGS.dollars_per_search + SETTINGS.dollars_per_extract + fake.CHAT.dollars(1000, 100)
    )


def test_an_enumerator_is_not_the_section_number():
    page = "\u00a7 10. Powers.\n1. In general.\n(10) The wages or salaries of employees.\n"
    clause = excerpt.Reply(
        found=True,
        scope="section",
        start="(10) The wages or salaries",
        end="(10) The wages or salaries",
    )
    assert excerpt.checked(clause, page, "10", []) is None


def test_a_long_section_is_the_page_text_from_its_first_to_its_last_words():
    body = "\n".join(f"({n}) Clause {n} of the section." for n in range(1, 400))
    page = f"\u00a7 10. Powers.\n{body}\nHistory: L.1963, c. 843.\n"
    reply = excerpt.Reply(
        found=True,
        scope="section",
        start="\u00a7 10. Powers.",
        end="(399) Clause 399 of the section.",
    )
    kept = excerpt.checked(reply, page, "10", [])
    assert kept is not None
    assert kept[0].startswith("\u00a7 10. Powers.") and kept[0].endswith(
        "Clause 399 of the section."
    )
    backwards = excerpt.Reply(
        found=True, scope="section", start="(5) Clause 5 of the section.", end="\u00a7 10. Powers."
    )
    assert excerpt.checked(backwards, page, "10", []) is None
