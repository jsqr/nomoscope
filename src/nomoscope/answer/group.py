"""A group of questions that form a DAG, read from a TOML file.

    name = "paraphernalia"        # optional; default the file's stem

    [[questions]]
    id = "Q1"
    text = "Does the jurisdiction restrict the sale of drug paraphernalia?"

    [[questions]]
    id = "Q2"
    after = ["Q1"]
    text = "If so, what types of paraphernalia are restricted?"

Ids are unique, every `after` names a question in the file, and the
dependencies have no cycle. A question is asked after every question it
depends on, directly or through others; among questions that are ready
together, file order decides. A question's context holds the answers of
all its ancestors.
"""

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from nomoscope.answer.models import AnswerError
from nomoscope.partition import PartitionError, check_value

GROUP_KEYS = frozenset({"name", "questions"})
QUESTION_KEYS = frozenset({"id", "text", "after"})
# The group a single question from the command line is stored under.
SINGLE = "ask"


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    after: tuple[str, ...] = ()


@dataclass(frozen=True)
class Group:
    name: str
    questions: tuple[Question, ...]
    # Of the file's bytes, or of the single question's text.
    sha256: str

    def by_id(self) -> dict[str, Question]:
        return {q.id: q for q in self.questions}

    def order(self) -> list[Question]:
        """Every question after those it depends on; ties in file order."""
        done: set[str] = set()
        out: list[Question] = []
        left = list(self.questions)
        while left:
            ready = next((q for q in left if set(q.after) <= done), None)
            if ready is None:
                raise AnswerError(
                    f"{self.name}: the dependencies of {', '.join(q.id for q in left)} form a cycle"
                )
            out.append(ready)
            done.add(ready.id)
            left.remove(ready)
        return out

    def ancestors(self, qid: str) -> list[Question]:
        """The questions `qid` depends on, directly or through others, in asking order."""
        known = self.by_id()
        found: set[str] = set()
        stack = list(known[qid].after)
        while stack:
            q = stack.pop()
            if q not in found:
                found.add(q)
                stack.extend(known[q].after)
        return [q for q in self.order() if q.id in found]


def single(text: str) -> Group:
    """A group of one question."""
    return Group(SINGLE, (Question("Q1", text),), hashlib.sha256(text.encode()).hexdigest())


def load(path: Path) -> Group:
    raw = path.read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise AnswerError(f"{path}: {e}") from e
    unknown = sorted(set(data) - GROUP_KEYS)
    if unknown:
        raise AnswerError(f"{path}: unknown keys {', '.join(unknown)}")
    name = data.get("name", path.stem)
    try:
        check_value(name)
    except PartitionError as e:
        raise AnswerError(
            f"{path}: name {name!r} must be lower case letters, digits, - and _"
        ) from e
    questions = [question(path, q) for q in data.get("questions", [])]
    if not questions:
        raise AnswerError(f"{path}: no [[questions]]")
    ids = [q.id for q in questions]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise AnswerError(f"{path}: repeated ids {', '.join(repeated)}")
    for q in questions:
        missing = [a for a in q.after if a not in ids]
        if missing:
            raise AnswerError(f"{path}: {q.id} is after {', '.join(missing)}, not in the file")
    group = Group(name, tuple(questions), hashlib.sha256(raw).hexdigest())
    group.order()
    return group


def question(path: Path, raw: object) -> Question:
    if not isinstance(raw, dict):
        raise AnswerError(f"{path}: each [[questions]] entry is a table")
    unknown = sorted(set(raw) - QUESTION_KEYS)
    if unknown:
        raise AnswerError(f"{path}: unknown question keys {', '.join(unknown)}")
    qid, text, after = raw.get("id"), raw.get("text"), raw.get("after", [])
    if not isinstance(qid, str) or not qid:
        raise AnswerError(f"{path}: a question needs an id")
    if not isinstance(text, str) or not text.strip():
        raise AnswerError(f"{path}: {qid} needs text")
    if not isinstance(after, list) or not all(isinstance(a, str) for a in after):
        raise AnswerError(f"{path}: {qid}: after is a list of ids")
    return Question(qid, " ".join(text.split()), tuple(after))
