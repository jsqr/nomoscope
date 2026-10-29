"""Ask one question, or a group in dependency order, in one jurisdiction.

A question is searched for (`nomoscope.search`), its passages and its
ancestors' answers go to the chat model, and the reply is checked
(`schema.parse`). A reply that fails the check is sent back once with the
reason; a second failure is recorded as the question's error, and its
dependents are still asked without it.

The spend limit works on estimates made before each question: a run stops
before a question whose estimate would pass what is left. Costs recorded
are the provider's token counts at the model's prices. Retrieval's own
calls (the query vector, reranking) are not counted; they cost far less.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from nomoscope.answer import prompt, schema
from nomoscope.answer.client import Completion
from nomoscope.answer.group import Group, Question
from nomoscope.answer.models import DEFAULT_CHAT, AnswerError, ChatModel
from nomoscope.answer.prompt import Passage, Prior
from nomoscope.answer.schema import Response
from nomoscope.search.query import Hit

PASSAGES = 12
# Estimates, before the passages are known: tokens per passage, of the
# instructions, question, and earlier answers, and of the reply with reasoning.
PASSAGE_TOKENS = 300
PROMPT_TOKENS = 800
OUTPUT_TOKENS = 1500

Retrieve = Callable[[str], list[Hit]]
Complete = Callable[[list[dict[str, str]]], Completion]


@dataclass(frozen=True)
class Options:
    model: str = DEFAULT_CHAT
    passages: int = PASSAGES
    text: bool = True
    rerank: bool = True


def options(table: Mapping[str, Any]) -> Options:
    """The `[answers]` table, over the defaults."""
    defaults = Options()
    known = set(Options.__dataclass_fields__)
    unknown = sorted(set(table) - known)
    if unknown:
        raise AnswerError(
            f"[answers] {', '.join(unknown)}: unknown; known: {', '.join(sorted(known))}"
        )
    for key, value in table.items():
        if type(value) is not type(getattr(defaults, key)):
            raise AnswerError(f"[answers] {key}: expected {type(getattr(defaults, key)).__name__}")
    return replace(defaults, **table)


@dataclass(frozen=True)
class Answered:
    question: Question
    jurisdiction: str
    on: date
    model: str
    passages: tuple[Passage, ...]
    response: Response | None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    dollars: float = 0.0

    @property
    def cited(self) -> list[Passage]:
        labels = set(self.response.citations) if self.response else set()
        return [p for p in self.passages if p.label in labels]


def estimate(model: ChatModel, passages: int, questions: int) -> float:
    tokens_in = PROMPT_TOKENS + passages * PASSAGE_TOKENS
    return questions * model.dollars(tokens_in, OUTPUT_TOKENS)


RETRY = (
    "Your reply was rejected: {error}. Reply again with one JSON object that fits the schema,"
    " citing only the labels of the passages given."
)


def ask(
    q: Question,
    priors: Sequence[Prior],
    jurisdiction: str,
    on: date,
    *,
    model: ChatModel,
    retrieve: Retrieve,
    complete: Complete,
    ancestors: Sequence[Question] = (),
) -> Answered:
    found = tuple(prompt.passages(retrieve(prompt.retrieval_query(q, ancestors))))
    labels = {p.label for p in found}
    sent = prompt.messages(q, priors, found, jurisdiction)
    tokens_in = tokens_out = 0
    error = None
    for _ in range(2):
        reply = complete(sent)
        tokens_in += reply.input_tokens
        tokens_out += reply.output_tokens
        try:
            response = schema.parse(reply.content, labels)
        except AnswerError as e:
            error = str(e)
            sent = [
                *sent,
                {"role": "assistant", "content": reply.content},
                {"role": "user", "content": RETRY.format(error=error)},
            ]
            continue
        return Answered(
            q, jurisdiction, on, model.slug, found, response, None,
            tokens_in, tokens_out, model.dollars(tokens_in, tokens_out),
        )  # fmt: skip
    return Answered(
        q, jurisdiction, on, model.slug, found, None, error,
        tokens_in, tokens_out, model.dollars(tokens_in, tokens_out),
    )  # fmt: skip


@dataclass(frozen=True)
class GroupRun:
    jurisdiction: str
    answered: tuple[Answered, ...]
    # Questions not asked because the spend ran out.
    unasked: tuple[Question, ...] = ()

    @property
    def dollars(self) -> float:
        return sum(a.dollars for a in self.answered)


def ask_group(
    g: Group,
    jurisdiction: str,
    on: date,
    *,
    model: ChatModel,
    passages: int,
    retrieve: Retrieve,
    complete: Complete,
    spend: float,
) -> GroupRun:
    """Every question in order, while the spend lasts."""
    done: dict[str, Answered] = {}
    left = spend
    order = g.order()
    for i, q in enumerate(order):
        if estimate(model, passages, 1) > left:
            return GroupRun(jurisdiction, tuple(done.values()), tuple(order[i:]))
        ancestors = g.ancestors(q.id)
        priors = [
            Prior(a, r.response.answer, r.response.rationale)
            for a in ancestors
            if (r := done.get(a.id)) is not None and r.response is not None
        ]
        answered = ask(
            q, priors, jurisdiction, on,
            model=model, retrieve=retrieve, complete=complete, ancestors=ancestors,
        )  # fmt: skip
        done[q.id] = answered
        left -= answered.dollars
    return GroupRun(jurisdiction, tuple(done.values()))
