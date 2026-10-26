import json

import httpx2
import polars as pl
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from nomoscope.structure import grammar as gr
from nomoscope.structure import infer, sample

LINES = pl.DataFrame(
    [
        {"page": p, "ordinal": i, "x0": 72.0, "y": 80.0 + 13 * i, "size": 12.0 if i == 0 else 11.0,
         "bold": 1.0 if i == 0 else 0.0, "italic": 0.0, "centered": i == 0, "caps": False,
         "gap_before": None if i == 0 else 13.0, "block_type": None, "page_width": 612.0,
         "page_height": 792.0, "text": f"line {p}.{i}"}
        for p in range(40)
        for i in range(3)
    ]
)  # fmt: skip
ANSWER = {
    "levels": [{"name": "section", "kind": "division", "label": "§ {n}.", "style": "arabic"}],
}


def test_sample_is_deterministic_and_sized():
    pages = sample.pages(LINES, 10, seed=1)
    assert pages == sample.pages(LINES, 10, seed=1)
    assert len(pages) == 10
    assert pages[:3] == [0, 1, 2]


def test_render():
    assert sample.render(LINES, [3]).splitlines() == [
        "== page 3 (612 x 792)",
        "y80 x72 s12 B C | line 3.0",
        "y93 x72 s11 g13 | line 3.1",
        "y106 x72 s11 g13 | line 3.2",
    ]


def test_render_without_a_page_height():
    # An HTML export's nominal pages have a width and no height.
    paged = LINES.with_columns(page_height=pl.lit(None, pl.Float64))
    assert sample.render(paged, [3]).splitlines()[0] == "== page 3"


def test_heading_pages_show_a_rare_top_style():
    # "Title 2" headings (12 pt, bold, centered) appear on a few pages; a
    # sampler that showed only page 0 led the model to leave the title
    # level out.
    rows = [
        {"page": p, "size": 11.0, "bold": 0.0, "centered": True, "caps": True} for p in range(30)
    ] + [
        {"page": p, "size": 12.0, "bold": 1.0, "centered": True, "caps": False} for p in (5, 17, 26)
    ]
    assert sample.heading_pages(pl.DataFrame(rows), limit=4)[:2] == [5, 17]


def test_render_marks():
    marks = [
        {"depth": 0, "title": "Chapter 1", "page": 0},
        {"depth": 1, "title": "§ 1-1", "page": 2},
    ]
    assert sample.render_marks(marks).splitlines() == [
        "== outline: 2 bookmarks",
        "Chapter 1 (p0)",
        "  § 1-1 (p2)",
    ]


def test_infer_with_a_stub_model_discards_its_review():
    answer = {**ANSWER, "review": {"status": "reviewed"}}

    def reply(messages, info):
        return ModelResponse(parts=[TextPart(json.dumps(answer))])

    out = infer.infer(LINES, FunctionModel(reply), "stub", [0, 1])
    assert isinstance(out.grammar, gr.Grammar)
    assert out.grammar.levels[0].label == "§ {n}."
    assert not out.grammar.review.reviewed
    assert "stub" in infer.comment(out)


def server(status: int, body: dict, seen: list | None = None) -> httpx2.AsyncClient:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx2.Response(status, json=body)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handle))


def completion(content, tool_args: dict | None = None) -> dict:
    message = {"role": "assistant", "content": content}
    if tool_args is not None:
        message["tool_calls"] = [
            {"id": "call0", "type": "function",
             "function": {"name": "final_result", "arguments": json.dumps(tool_args)}}
        ]  # fmt: skip
    return {
        "id": "r", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if tool_args else "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }  # fmt: skip


def test_reasoning_reply_with_thinking_chunks():
    # A reasoning model returns content as a list whose first chunk is its
    # reasoning; the OpenAI-compatible adapter rejected that.
    thinking = [{"type": "thinking", "thinking": [{"type": "text", "text": "Levels are"}],
                 "closed": True}]  # fmt: skip
    seen: list = []
    model = infer.backend("m", "key", server(200, completion(thinking, ANSWER), seen))
    out = infer.infer(LINES, model, "m", [0])
    assert out.grammar.levels[0].label == "§ {n}."
    assert (out.input_tokens, out.output_tokens) == (100, 20)
    assert [t["function"]["name"] for t in seen[0]["tools"]] == ["final_result"]


def test_reasoning_effort_is_sent_as_given():
    # pydantic-ai would send "high" for every level but "none".
    for effort in ("low", "high"):
        seen: list = []
        model = infer.backend("m", "key", server(200, completion(None, ANSWER), seen), effort)
        infer.infer(LINES, model, "m", [0])
        assert seen[0]["reasoning_effort"] == effort


def test_no_effort_sends_no_reasoning_field():
    seen: list = []
    model = infer.backend("m", "key", server(200, completion(None, ANSWER), seen), None)
    out = infer.infer(LINES, model, "m", [0])
    assert "reasoning_effort" not in seen[0]
    assert out.requests == 1


def test_effort_the_sdk_would_drop_is_refused():
    # The SDK omits an effort it does not list from the request, with no
    # error, and the model then reasons at length. Older SDKs dropped "max".
    assert "ultra" not in infer.SENDABLE
    with pytest.raises(ValueError, match="is not one the SDK sends"):
        infer.backend("m", "key", effort="ultra")
