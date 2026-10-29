import json
from datetime import date

import httpx
import polars as pl
import pytest

from nomoscope.answer import ask, client, group, models, prompt, schema, store
from nomoscope.search.query import Hit

GLM = models.chat_model("glm-5-3-low")


def transport(handler) -> httpx.Client:
    return httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(handler))


def reply(content: str, prompt_tokens: int = 100, completion_tokens: int = 20):
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }
        return httpx.Response(200, json=body)

    return handler


GOOD = json.dumps(
    {"answer": "Yes.", "rationale": "[P1] says so.", "citations": ["P1"], "confidence": 0.9}
)


def test_the_request_asks_for_the_schema_and_the_models_effort():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.read()))
        return reply(GOOD)(request)

    messages = [{"role": "user", "content": "q"}]
    with transport(handler) as c:
        out = client.complete(c, GLM, messages, schema.json_schema())
    assert seen["model"] == "accounts/fireworks/models/glm-5p3"
    assert seen["reasoning_effort"] == "low" and seen["temperature"] == 0.0
    assert seen["messages"] == messages
    fmt = seen["response_format"]
    assert fmt["type"] == "json_schema"
    assert set(fmt["json_schema"]["schema"]["properties"]) == {
        "answer",
        "rationale",
        "citations",
        "confidence",
    }
    assert (out.content, out.input_tokens, out.output_tokens) == (GOOD, 100, 20)


def test_an_empty_reply_is_an_error():
    with transport(reply("")) as c, pytest.raises(models.AnswerError, match="empty reply"):
        client.complete(c, GLM, [], {})


def test_a_reply_is_parsed_and_checked_against_the_passages():
    found = schema.parse(GOOD, {"P1", "P2"})
    assert (found.answer, found.citations, found.confidence) == ("Yes.", ["P1"], 0.9)
    with pytest.raises(models.AnswerError, match="not supplied: P1"):
        schema.parse(GOOD, {"P2"})
    with pytest.raises(models.AnswerError, match="does not fit"):
        schema.parse('{"answer": "Yes."}', {"P1"})
    with pytest.raises(models.AnswerError, match="does not fit"):
        schema.parse("not json", {"P1"})
    high = json.dumps({**json.loads(GOOD), "confidence": 1.5})
    with pytest.raises(models.AnswerError, match="between 0 and 1"):
        schema.parse(high, {"P1"})


def test_cost_is_priced_per_token():
    assert GLM.dollars(1_000_000, 1_000_000) == pytest.approx(1.4 + 4.4)
    with pytest.raises(models.AnswerError, match="known: glm-5-3-low"):
        models.chat_model("nope")


def group_file(tmp_path, text: str, name: str = "paraphernalia.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


GROUP = '''
[[questions]]
id = "Q3"
after = ["Q1"]
text = "What penalties attach?"

[[questions]]
id = "Q1"
text = """Does the jurisdiction restrict
the sale of drug paraphernalia?"""

[[questions]]
id = "Q2"
after = ["Q1"]
text = "If so, what types are restricted?"

[[questions]]
id = "Q4"
after = ["Q2"]
text = "Are any exempt?"
'''


def test_a_group_is_ordered_by_dependency_then_file_order(tmp_path):
    g = group.load(group_file(tmp_path, GROUP))
    assert g.name == "paraphernalia"
    assert [q.id for q in g.order()] == ["Q1", "Q3", "Q2", "Q4"]
    assert g.by_id()["Q1"].text == "Does the jurisdiction restrict the sale of drug paraphernalia?"
    assert [q.id for q in g.ancestors("Q4")] == ["Q1", "Q2"]
    assert g.ancestors("Q1") == []
    assert len(g.sha256) == 64


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ('[[questions]]\nid = "A"\ntext = "x"\nafter = ["B"]\n', "A is after B, not in the file"),
        ('[[questions]]\nid = "A"\ntext = "x"\n[[questions]]\nid = "A"\ntext = "y"\n',
         "repeated ids A"),
        ('[[questions]]\nid = "A"\ntext = "x"\nafter = ["B"]\n[[questions]]\nid = "B"\n'
         'text = "y"\nafter = ["A"]\n', "A, B form a cycle"),
        ('[[questions]]\nid = "A"\ntext = "x"\nwhen = "y"\n', "unknown question keys when"),
        ('title = "x"\n', "unknown keys title"),
        ('name = "Bad Name"\n[[questions]]\nid = "A"\ntext = "x"\n', "must be lower case"),
        ("", "no \\[\\[questions\\]\\]"),
        ('[[questions]]\nid = "A"\n', "A needs text"),
    ],
)  # fmt: skip
def test_a_bad_group_is_refused(tmp_path, text, error):
    with pytest.raises(models.AnswerError, match=error):
        group.load(group_file(tmp_path, text))


def test_a_single_question_is_a_group_of_one():
    g = group.single("Are chickens allowed?")
    assert (g.name, [q.id for q in g.order()]) == ("ask", ["Q1"])


def hit(n: int, section: str, text: str) -> Hit:
    return Hit(
        rank=n, law_code="municipal", section_num=section, path="", heading=f"Heading {section}",
        snippet=text, law_version_id=f"v{section}", segment_id=f"v{section}/seg/0",
        score=1.0 / n, context=f"CHAPTER 8 > municipal {section} Heading {section}",
    )  # fmt: skip


HITS = [hit(1, "8-91", "No person shall sell drug paraphernalia."), hit(2, "8-92", "Fines.")]


def answering(*contents: str):
    """A stub model that replies with `contents` in turn and records what it was sent."""
    sent: list = []
    replies = iter(contents)

    def complete(messages):
        sent.append(messages)
        return client.Completion(next(replies), 1000, 100)

    return complete, sent


def reply_json(answer: str, citations=("P1",), confidence: float = 0.8) -> str:
    return json.dumps(
        {"answer": answer, "rationale": f"See [{citations[0]}].", "citations": list(citations),
         "confidence": confidence}
    )  # fmt: skip


ON = date(2026, 10, 28)


def test_the_prompt_gives_earlier_answers_then_labeled_passages_then_the_question():
    q2 = group.Question("Q2", "If so, what types?", ("Q1",))
    q1 = group.Question("Q1", "Does it restrict paraphernalia?")
    [system, user] = prompt.messages(
        q2, [prompt.Prior(q1, "Yes.", "[P1] says so.")], prompt.passages(HITS), "us-co-denver"
    )
    assert system["role"] == "system" and "cite each passage" in system["content"]
    text = user["content"]
    assert (
        text.index("Q1. Does it restrict")
        < text.index("[P1] municipal 8-91")
        < text.index("Question Q2: If so, what types?")
    )
    assert "CHAPTER 8 > municipal 8-91 Heading 8-91\n\nNo person shall sell" in text
    assert "[P2] municipal 8-92 Heading 8-92" in text
    assert prompt.retrieval_query(q2, [q1]) == "Does it restrict paraphernalia? If so, what types?"


def test_a_rejected_reply_is_sent_back_once_with_the_reason():
    complete, sent = answering(reply_json("Yes.", ("P9",)), reply_json("Yes."))
    out = ask.ask(group.single("q").questions[0], [], "us-x", ON, model=GLM,
                  retrieve=lambda query: HITS, complete=complete)  # fmt: skip
    assert out.response is not None and out.error is None
    assert len(sent) == 2 and "not supplied: P9" in sent[1][-1]["content"]
    assert (out.input_tokens, out.output_tokens) == (2000, 200)
    assert out.dollars == pytest.approx(GLM.dollars(2000, 200))
    assert [p.hit.section_num for p in out.cited] == ["8-91"]


def test_a_second_bad_reply_is_recorded_as_the_error():
    complete, _ = answering("nope", "still nope")
    out = ask.ask(group.single("q").questions[0], [], "us-x", ON, model=GLM,
                  retrieve=lambda query: HITS, complete=complete)  # fmt: skip
    assert out.response is None and "does not fit" in (out.error or "")


def run_group(g, *contents, spend=1.0, queries=None):
    complete, sent = answering(*contents)

    def retrieve(query):
        if queries is not None:
            queries.append(query)
        return HITS

    run = ask.ask_group(g, "us-x", ON, model=GLM, passages=12, retrieve=retrieve,
                        complete=complete, spend=spend)  # fmt: skip
    return run, sent


def test_a_dependent_gets_its_ancestors_answers_and_their_questions_in_retrieval(tmp_path):
    g = group.load(group_file(tmp_path, GROUP))
    queries: list = []
    run, sent = run_group(
        g, reply_json("Yes, sale is banned."), reply_json("Fines."), reply_json("Pipes."),
        reply_json("None."), queries=queries,
    )  # fmt: skip
    assert [a.question.id for a in run.answered] == ["Q1", "Q3", "Q2", "Q4"]
    q4 = sent[3][1]["content"]
    assert "Answer: Yes, sale is banned." in q4 and "Answer: Pipes." in q4
    assert "Answer: Fines." not in q4
    assert queries[3].startswith("Does the jurisdiction restrict")
    assert "Earlier answers" not in sent[0][1]["content"]


def test_a_failed_parent_leaves_its_dependents_asked_without_it(tmp_path):
    g = group.load(group_file(tmp_path, GROUP))
    run, sent = run_group(g, "bad", "bad", *(reply_json("x") for _ in range(3)))
    assert run.answered[0].error and len(run.answered) == 4
    assert "Earlier answers" not in sent[2][1]["content"]


def test_the_spend_stops_the_run_before_a_question(tmp_path):
    g = group.load(group_file(tmp_path, GROUP))
    one = ask.estimate(GLM, 12, 1)
    run, _ = run_group(g, reply_json("x"), reply_json("y"), spend=one + 0.001)
    assert [a.question.id for a in run.answered] == ["Q1"]
    assert [q.id for q in run.unasked] == ["Q3", "Q2", "Q4"]


def test_answers_are_stored_one_file_per_run(cfg, tmp_path):
    g = group.load(group_file(tmp_path, GROUP))
    run, _ = run_group(g, "bad", "bad", *(reply_json("x") for _ in range(3)))
    path = store.write(cfg, g, run, "J. Doe")
    assert path is not None and path.parent == store.folder(cfg, "paraphernalia", "us-x")
    assert path.name.endswith("-j_doe.parquet")
    frame = pl.read_parquet(path)
    assert frame["question_id"].to_list() == ["Q1", "Q3", "Q2", "Q4"]
    assert frame["error"][0] is not None and frame["answer"][1] == "x"
    assert frame["passages"][1][0]["segment_id"] == "v8-91/seg/0"
    assert store.views(cfg) == {"answers": "answers/*/*/*.parquet"}
    assert store.write(cfg, g, ask.GroupRun("us-x", ()), "a") is None


def test_answer_settings_are_checked():
    assert ask.options({"passages": 8}).passages == 8
    with pytest.raises(models.AnswerError, match="passages: expected int"):
        ask.options({"passages": "8"})
    with pytest.raises(models.AnswerError, match="modle: unknown"):
        ask.options({"modle": "x"})
