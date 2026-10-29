"""One citation's text from the web: the cache, else search, excerpt, check, and store.

The query is the citation's Bluebook form, else the citation as written after
the cited jurisdiction's name. The first search is limited to the configured
domains; if no page yields the provision, a second search is open to any
domain. A result page whose text is short is read whole through Tavily's
extraction first. A result is cached whether or not it was found. A search
or request that failed is not cached, so a later question tries again.
"""

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import httpx
from pydantic import ValidationError

from nomoscope.chat import ChatError, ChatModel, Completion
from nomoscope.chat import lookup as model_lookup
from nomoscope.lookup import LookupsError, cache, excerpt
from nomoscope.lookup.tavily import Extract, Page, Search
from nomoscope.providers import ProviderError
from nomoscope.settings import Settings as Config

log = logging.getLogger(__name__)

DOMAINS = ("law.justia.com", "law.cornell.edu", "codes.findlaw.com")
RESULTS = 3
# Tavily's pay-as-you-go price for one basic search, and for one basic
# extraction of up to five pages.
DOLLARS_PER_SEARCH = 0.008
DOLLARS_PER_EXTRACT = 0.008
# A page shorter than this from a search is read whole by extraction.
SHORT_PAGE = 1000
# For estimates: tokens of one page window and of the reply.
PAGE_TOKENS = 10_000
REPLY_TOKENS = 300

Complete = Callable[[list[dict[str, str]]], Completion]


@dataclass(frozen=True)
class Settings:
    model: ChatModel
    domains: tuple[str, ...] = DOMAINS
    results: int = RESULTS
    dollars_per_search: float = DOLLARS_PER_SEARCH
    dollars_per_extract: float = DOLLARS_PER_EXTRACT


KEYS = ("model", "domains", "results", "dollars_per_search", "dollars_per_extract")


def settings(table: Mapping[str, Any], models: Mapping[str, ChatModel]) -> Settings:
    unknown = sorted(set(table) - set(KEYS))
    if unknown:
        raise LookupsError(f"[lookups] {', '.join(unknown)}: unknown; known: {', '.join(KEYS)}")
    return Settings(
        model_lookup(models, str(table.get("model", "")), "[lookups] model"),
        tuple(table.get("domains", DOMAINS)),
        int(table.get("results", RESULTS)),
        float(table.get("dollars_per_search", DOLLARS_PER_SEARCH)),
        float(table.get("dollars_per_extract", DOLLARS_PER_EXTRACT)),
    )


def estimate(s: Settings) -> float:
    """The most one lookup costs: two searches, two extractions, and an excerpt
    of every page."""
    pages = 2 * s.results
    fees = 2 * (s.dollars_per_search + s.dollars_per_extract)
    return fees + pages * s.model.dollars(PAGE_TOKENS, REPLY_TOKENS)


@dataclass(frozen=True)
class Request:
    target_id: str
    cited: str
    bluebook: str | None
    # The cited jurisdiction's name, for a query without a Bluebook form.
    place: str | None = None


@dataclass(frozen=True)
class Found:
    text: str
    url: str
    title: str
    scope: str
    looked_at: date


@dataclass(frozen=True)
class Looked:
    found: Found | None
    dollars: float = 0.0
    searched: bool = False


def parts(target_id: str) -> tuple[str, str, list[str]]:
    """`us-ny/law/TWN/130/a` -> ("us-ny", "130", ["a"])."""
    pieces = target_id.split("/")
    return pieces[0], pieces[3], pieces[4:]


def query(r: Request) -> str:
    if r.bluebook:
        return r.bluebook
    return f"{r.place} {r.cited}" if r.place else r.cited


def from_row(row: Mapping[str, Any]) -> Found | None:
    if not row["found"]:
        return None
    return Found(row["text"], row["url"], row["title"], row["scope"], row["looked_at"].date())


@dataclass
class Tally:
    """What one lookup spent, for its cache row."""

    searches: int = 0
    extracts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    pages: list[str] = field(default_factory=list)


def lookup(
    cfg: Config,
    s: Settings,
    search: Search,
    extract: Extract,
    complete: Complete,
    writer: str,
    r: Request,
    may_search: bool = True,
) -> Looked:
    jurisdiction, section, path = parts(r.target_id)
    held = cache.held(cfg, jurisdiction, excerpt.VERSION).get(r.target_id)
    if held is not None:
        return Looked(from_row(held))
    if not may_search:
        return Looked(None)
    q = query(r)
    tally = Tally()
    found = None
    try:
        for domains in (s.domains, None):
            tally.searches += 1
            pages = whole(search(q, domains, s.results), extract, tally)
            found = first_excerpt(s, pages, complete, r, section, path, tally)
            if found is not None:
                break
    except (ProviderError, httpx.TransportError) as e:
        log.warning("lookup %s: %s; not cached", r.target_id, e)
        return Looked(None, spent(s, tally), searched=True)
    looked_at = cache.now()
    row = {
        "target_id": r.target_id, "query": q, "found": found is not None,
        "url": found.url if found else None, "title": found.title if found else None,
        "scope": found.scope if found else None, "text": found.text if found else None,
        "model": s.model.slug, "input_tokens": tally.input_tokens,
        "output_tokens": tally.output_tokens, "searches": tally.searches,
        "extracts": tally.extracts,
        "dollars": spent(s, tally), "looked_at": looked_at, "version": excerpt.VERSION,
    }  # fmt: skip
    cache.write(cfg, jurisdiction, row, writer)
    if found is not None:
        found = Found(found.text, found.url, found.title, found.scope, looked_at.date())
    return Looked(found, row["dollars"], searched=True)


def spent(s: Settings, tally: Tally) -> float:
    fees = tally.searches * s.dollars_per_search + tally.extracts * s.dollars_per_extract
    return fees + s.model.dollars(tally.input_tokens, tally.output_tokens)


def whole(pages: Sequence[Page], extract: Extract, tally: Tally) -> list[Page]:
    """The pages, the short ones read whole by one extraction."""
    short = [p.url for p in pages if len(p.text) < SHORT_PAGE and p.url not in tally.pages]
    if not short:
        return list(pages)
    tally.extracts += 1
    texts = extract(short)
    return [replace(p, text=texts[p.url]) if texts.get(p.url) else p for p in pages]


def first_excerpt(
    s: Settings,
    pages: Sequence[Page],
    complete: Complete,
    r: Request,
    section: str,
    path: list[str],
    tally: Tally,
) -> Found | None:
    citation = r.bluebook or r.cited
    for page in pages:
        if not page.text or page.url in tally.pages:
            continue
        tally.pages.append(page.url)
        text = excerpt.window(page.text, section)
        try:
            reply = complete(excerpt.messages(citation, text))
        except ChatError as e:
            log.warning("lookup %s: %s", r.target_id, e)
            continue
        tally.input_tokens += reply.input_tokens
        tally.output_tokens += reply.output_tokens
        try:
            parsed = excerpt.Reply.model_validate(json.loads(reply.content))
        except ValidationError, json.JSONDecodeError:
            continue
        kept = excerpt.checked(parsed, text, section, path)
        if kept is not None:
            return Found(kept[0], page.url, page.title, kept[1], cache.now().date())
    return None
