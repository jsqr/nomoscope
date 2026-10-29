"""The messages for one question: the answers it depends on, its passages, and itself.

Each passage is labeled P1, P2, ... in rank order and given as it was
embedded: its citation and heading, its context (headings and lead-ins),
then its text. The answers of the question's ancestors come first, each
with its question and rationale.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from nomoscope.answer.group import Question
from nomoscope.search.query import Hit

SYSTEM = """\
You answer questions about the law of one jurisdiction from numbered passages of its code.

- Answer only from the passages. Earlier answers in the same inquiry are given for context;
  they rest on other passages, which you cannot see.
- A question that follows earlier answers is about their subject: "violations" means
  violations of what those answers describe. Leave out passages on other subjects.
- In the rationale, cite each passage you rely on by its label, as [P2]. List those labels
  in citations.
- If the passages do not answer the question, say so in the answer, cite nothing, and give
  a low confidence.
- Confidence is your probability, from 0 to 1, that the answer is right given the passages.

Reply with one JSON object that fits the schema you were given."""


@dataclass(frozen=True)
class Passage:
    label: str
    hit: Hit


@dataclass(frozen=True)
class Prior:
    question: Question
    answer: str
    rationale: str


def passages(hits: Sequence[Hit]) -> list[Passage]:
    return [Passage(f"P{i}", h) for i, h in enumerate(hits, start=1)]


def render(p: Passage) -> str:
    head = f"[{p.label}] {p.hit.citation}" + (f" {p.hit.heading}" if p.hit.heading else "")
    body = f"{p.hit.context}\n\n{p.hit.snippet}" if p.hit.context else p.hit.snippet
    return f"{head}\n{body}"


def retrieval_query(q: Question, ancestors: Sequence[Question]) -> str:
    """The question with the questions it depends on before it, so a question
    like "If so, which ones?" is searched with its referent."""
    return " ".join([*(a.text for a in ancestors), q.text])


def messages(
    q: Question, priors: Sequence[Prior], found: Sequence[Passage], jurisdiction: str
) -> list[dict[str, str]]:
    parts = [f"Jurisdiction: {jurisdiction}"]
    if priors:
        parts.append(
            "Earlier answers:\n\n"
            + "\n\n".join(
                f"{p.question.id}. {p.question.text}\nAnswer: {p.answer}\nRationale: {p.rationale}"
                for p in priors
            )
        )
    parts.append(
        "Passages:\n\n" + ("\n\n".join(render(p) for p in found) if found else "(none found)")
    )
    parts.append(f"Question {q.id}: {q.text}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]
