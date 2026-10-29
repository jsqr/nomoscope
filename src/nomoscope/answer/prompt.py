"""The messages for one question: the answers it depends on, its passages, and itself.

Each passage is labeled P1, P2, ... in rank order and given as it was
embedded: its citation and heading, its context (headings and lead-ins),
then its text. The answers of the question's ancestors come first, each
with its question and rationale.

A passage may carry notes (`augment.py`): definitions that apply to it and
the provisions it cites, listed after its text. A note given under an
earlier passage is a one-line pointer to it.
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
- Notes after a passage give definitions and penalties that apply to it and the text of
  provisions it cites. Cite a note by its passage's label.

Reply with one JSON object that fits the schema you were given."""


DEFINITION = "definition"
PENALTY = "penalty"
REFERENCE = "reference"


@dataclass(frozen=True)
class Note:
    kind: str
    # Definition id or cited target id.
    id: str
    # Term, or target citation.
    name: str
    # Definition or penalty: its section and scope. Reference: the citation as
    # written, and for a lookup, where its text came from.
    about: str
    # None for a cited heading.
    text: str | None


@dataclass(frozen=True)
class Placed:
    note: Note
    # Label of the earlier passage that gave the text.
    given_under: str | None = None


@dataclass(frozen=True)
class Passage:
    label: str
    hit: Hit
    notes: tuple[Placed, ...] = ()


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
    blocks = [f"{head}\n{body}"]
    for kind, lead in (
        (DEFINITION, "Defined terms used in this passage:"),
        (PENALTY, "Penalties that apply to this passage:"),
        (REFERENCE, "Provisions this passage cites:"),
    ):
        mine = [n for n in p.notes if n.note.kind == kind]
        if kind == PENALTY:
            mine, pointers = (
                [n for n in mine if n.given_under is None],
                sorted({n.given_under for n in mine if n.given_under is not None}),
            )
            lines = [note_line(n) for n in mine]
            # Penalties that cover a whole chapter or code recur under every
            # passage; one line per earlier passage points back to them.
            lines += [f"- The penalties given under [{label}] also apply" for label in pointers]
        else:
            lines = [note_line(n) for n in mine]
        if lines:
            blocks.append("\n".join([lead, *lines]))
    return "\n\n".join(blocks)


def note_line(placed: Placed) -> str:
    n = placed.note
    name = f'"{n.name}"' if n.kind == DEFINITION else n.name
    if placed.given_under is not None:
        verb = "defined" if n.kind == DEFINITION else "given"
        return f"- {name}: {verb} under [{placed.given_under}]"
    if n.text is None:
        return f"- {name} ({n.about})"
    return f"- {name} ({n.about}): {n.text}"


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
