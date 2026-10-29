"""The response a question asks for, and the check of a model's reply.

The model is sent `Response`'s JSON schema and asked to reply with an
object that fits it. `parse` checks the reply: it must fit the schema,
the confidence must be between 0 and 1, and every citation must name a
passage the question supplied.
"""

import json
from collections.abc import Collection
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nomoscope.answer.models import AnswerError


class Response(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(description="The answer, in one to three sentences.")
    rationale: str = Field(
        description="Why the passages give this answer, citing them by label, as [P2]."
    )
    citations: list[str] = Field(
        description='The labels of the passages the answer rests on, without brackets, as "P2".'
    )
    confidence: float = Field(
        description="The probability, from 0 to 1, that the answer is right given the passages."
    )


def json_schema() -> dict[str, Any]:
    return Response.model_json_schema()


def parse(content: str, labels: Collection[str]) -> Response:
    """The reply as a Response. Raises AnswerError if it does not fit or cites
    a passage not supplied."""
    try:
        found = Response.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError) as e:
        raise AnswerError(f"the reply does not fit the response schema: {e}") from e
    if not 0 <= found.confidence <= 1:
        raise AnswerError(f"confidence {found.confidence} is not between 0 and 1")
    # The rationale cites "[P2]", and models carry the brackets over.
    found = found.model_copy(update={"citations": [c.strip(" []") for c in found.citations]})
    unknown = [c for c in found.citations if c not in labels]
    if unknown:
        raise AnswerError(f"the reply cites passages not supplied: {', '.join(unknown)}")
    return found
