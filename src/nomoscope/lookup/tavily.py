"""Tavily web search, with each result's page text, and extraction of whole pages.

A search returns little of some pages (FindLaw's, about 160 characters);
extraction reads the whole page."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

from nomoscope.providers import post


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str


# query, domains to restrict to (None for any), results wanted
Search = Callable[[str, Sequence[str] | None, int], list[Page]]
# urls -> text by url
Extract = Callable[[Sequence[str]], dict[str, str]]


def search(
    client: httpx.Client, query: str, domains: Sequence[str] | None, results: int
) -> list[Page]:
    payload = {
        "query": query,
        "search_depth": "basic",
        "max_results": results,
        "include_raw_content": True,
    }
    if domains:
        payload["include_domains"] = list(domains)
    body = post(client, "/search", payload)
    return [
        Page(r["url"], r.get("title") or "", r.get("raw_content") or r.get("content") or "")
        for r in body.get("results", [])
        if r.get("url")
    ]


def extract(client: httpx.Client, urls: Sequence[str]) -> dict[str, str]:
    body = post(client, "/extract", {"urls": list(urls), "extract_depth": "basic"})
    return {r["url"]: r.get("raw_content") or "" for r in body.get("results", []) if r.get("url")}
