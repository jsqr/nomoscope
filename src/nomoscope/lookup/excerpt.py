"""A cited section or subdivision found in a web page.

The model gives the provision's first and last words as the page prints
them, and the excerpt is the page's own text from the one to the other
(`extract/locate.py`), so nothing the model wrote reaches the prompt and a
long section costs no more than a short one. Words not in the page, an end
before the start, or a start that is not where the citation says are
rejected. A section's number may be in a title line just above its text, as
on FindLaw's pages, so the check also looks a little before the start.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from nomoscope.extract import locate

# Pages longer than this are cut to a window around the section number.
PAGE_CHARS = 40_000
# Bump when the prompt or the check changes, so cached misses are tried again.
VERSION = "4"
# A line that starts with an enumerator: "1.", "(a)", "(10)".
ENUMERATED = re.compile(r"^\s*\(?[0-9a-zA-Z]{1,4}[.)]\s", re.MULTILINE)
# How far before the copy a section's number may sit, in a title line.
TITLE_CHARS = 400
PROMPT = """\
You are given a citation of a statute and the text of a web page. Find the
cited provision on the page and mark where it starts and ends.

If the citation names a subdivision (such as "(a)" or "subdivision 3") and the
page has it, mark that subdivision, including everything under it, and give
scope "subdivision". If the page has the section but you cannot find the
subdivision, mark the whole section and give scope "section". If the citation
names only a section, mark the whole section. Leave out page navigation,
notes, and history lines.

- start: the provision's first 12 or so words, copied exactly from the page,
  beginning with the section number or the subdivision's enumerator.
- end: the provision's last 12 or so words, copied exactly from the page.

If the page does not contain the cited provision, give found false and start
and end null.

Reply with one JSON object that fits the schema you were given."""


class Reply(BaseModel):
    found: bool
    scope: Literal["section", "subdivision"] | None = None
    start: str | None = Field(default=None, description="The first words, verbatim, or null.")
    end: str | None = Field(default=None, description="The last words, verbatim, or null.")


def window(page: str, section: str) -> str:
    if len(page) <= PAGE_CHARS:
        return page
    m = re.search(rf"(?<![\w.-]){re.escape(section)}(?![\w-])", page)
    start = min(max(0, (m.start() if m else 0) - PAGE_CHARS // 4), len(page) - PAGE_CHARS)
    return page[start : start + PAGE_CHARS]


def messages(citation: str, page: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": f"Citation: {citation}\n\nPage:\n{page}"},
    ]


def starts_as_cited(
    text: str, before: str, section: str, path: list[str], scope: str | None
) -> bool:
    """`before` is the page text just before the copy."""
    head = text.lstrip()[:200]
    if scope == "subdivision" and path:
        enumerator = re.escape(path[-1])
        return re.match(rf"\(?{enumerator}[.)]", head, re.IGNORECASE) is not None
    # Not "(10)", which is an enumerator.
    number = re.compile(rf"(?<![\w.(-]){re.escape(section)}(?![\w)-])")
    if number.search(head):
        return True
    # In a title line: the number, then a line break, and no numbered line
    # between it and the copy, which would make the copy a later part.
    titles = list(number.finditer(before))
    if not titles:
        return False
    gap = before[titles[-1].end() :]
    return "\n" in gap and ENUMERATED.search(gap) is None


def checked(reply: Reply, page: str, section: str, path: list[str]) -> tuple[str, str] | None:
    """The page's own text for the excerpt and its scope, or None if rejected."""
    if not reply.found or not reply.start or not reply.end:
        return None
    first = locate.locate(page, reply.start)
    if first is None:
        return None
    last = locate.locate(page[first[0] :], reply.end)
    if last is None:
        return None
    text = page[first[0] : first[0] + last[1]]
    before = page[max(0, first[0] - TITLE_CHARS) : first[0]]
    scope = reply.scope or ("subdivision" if path else "section")
    if not starts_as_cited(text, before, section, path, scope):
        return None
    return text, scope
