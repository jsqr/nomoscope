import hashlib
import json

import httpx
import polars as pl
import pytest

from nomoscope import providers
from nomoscope.derive import registry
from nomoscope.derive.segment import content
from nomoscope.embed import client, models, run, store, text
from nomoscope.parquet import write_parquet
from nomoscope.partition import dataset_path

J = "us-il-windytown"
QWEN = models.embedding_model("qwen3-embedding-8b")
SMALL = models.embedding_model("qwen3-embedding-8b", 4)
RERANKER = models.reranker("qwen3-reranker-8b")


def transport(handler) -> httpx.Client:
    return httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(handler))


def answer(width: int):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        data = [
            {"index": i, "embedding": [float(i + 1)] + [0.0] * (width - 1)}
            for i in range(len(payload["input"]))
        ]
        return httpx.Response(200, json={"data": data})

    return handler


def test_the_request_asks_for_the_models_width_and_vectors_come_back_unit_length():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.read()))
        data = [{"index": 0, "embedding": [3.0, 4.0, 0.0, 0.0]}]
        return httpx.Response(200, json={"data": data})

    with transport(handler) as c:
        [v] = client.embed(c, SMALL, ["a"])
    assert seen == {"model": "fireworks/qwen3-embedding-8b", "input": ["a"], "dimensions": 4}
    assert v == pytest.approx([0.6, 0.8, 0.0, 0.0])


def test_vectors_come_back_in_the_order_asked_for():
    def handler(request):
        data = [{"index": 1, "embedding": [0, 1, 0, 0]}, {"index": 0, "embedding": [1, 0, 0, 0]}]
        return httpx.Response(200, json={"data": data})

    with transport(handler) as c:
        assert client.embed(c, SMALL, ["a", "b"]) == [[1, 0, 0, 0], [0, 1, 0, 0]]


def test_a_wrong_width_is_an_error():
    with transport(answer(8)) as c, pytest.raises(models.EmbedError, match="expected 4"):
        client.embed(c, SMALL, ["a"])


def test_a_rate_limit_is_retried_and_a_bad_request_is_not():
    calls, waits = [], []

    def throttled(request):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else answer(4)(request)

    with transport(throttled) as c:
        client.embed(c, SMALL, ["a"], sleep=waits.append, jitter=lambda: 0.5)
    assert waits == [2.0, 4.0]
    with (
        transport(lambda r: httpx.Response(400, text="no")) as c,
        pytest.raises(providers.ProviderError, match="HTTP 400: no"),
    ):
        client.embed(c, SMALL, ["a"])


def test_the_retry_wait_follows_retry_after_and_is_capped():
    with transport(lambda r: httpx.Response(429, headers={"retry-after": "12"})) as c:
        response = c.post("/embeddings", json={})
    assert providers.retry_wait(response, 1, lambda: 0.5) == 12.0
    with transport(lambda r: httpx.Response(429)) as c:
        response = c.post("/embeddings", json={})
    assert providers.retry_wait(response, 20, lambda: 0.5) == providers.MAX_WAIT


def test_batches_respect_count_and_tokens():
    assert client.batches(["x"] * 5, inputs=2, tokens=1000) == [[0, 1], [2, 3], [4]]
    assert client.batches(["y" * 4000] * 4, tokens=2500) == [[0, 1], [2, 3]]
    assert client.batches(["z" * 100_000], tokens=100) == [[0]]


def test_the_query_carries_the_instruction_in_the_models_template():
    assert QWEN.query_text("Find laws", "dogs") == "Instruct: Find laws\nQuery:dogs"


def test_a_model_is_named_by_slug_and_width():
    assert QWEN.key == "qwen3-embedding-8b-1024"
    assert models.configured({"dimensions": 256}).key == "qwen3-embedding-8b-256"
    with pytest.raises(models.EmbedError, match="known: qwen3-embedding-8b"):
        models.embedding_model("nope")
    with pytest.raises(models.EmbedError, match="1 to 4096"):
        models.embedding_model("qwen3-embedding-8b", 5000)


def test_a_missing_key_is_reported(monkeypatch):
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    with pytest.raises(providers.ProviderError, match="set FIREWORKS_API_KEY"):
        providers.make_client(QWEN.provider)


def test_rerank_sends_the_template_and_reads_the_yes_probability():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.read()))
        data = [{"index": 0, "embedding": [0.9, 0.1]}, {"index": 1, "embedding": [0.2, 0.8]}]
        return httpx.Response(200, json={"data": data})

    prompts = [RERANKER.prompt("Find laws", "dogs", d) for d in ("cats", "dogs on leash")]
    with transport(handler) as c:
        assert client.rerank(c, RERANKER, prompts) == [0.1, 0.8]
    assert seen["return_logits"] == [2753, 9454] and seen["normalize"] is True
    assert seen["input"][1].startswith("<|im_start|>system\nJudge whether the Document")
    assert (
        "<Instruct>: Find laws\n<Query>: dogs\n<Document>: dogs on leash<|im_end|>"
        in (seen["input"][1])
    )
    assert seen["input"][1].endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")


TEXTS = {
    "v1": "Dogs must be leashed.",
    "v2": "(a) Cats may roam.\n(b) Cats must be licensed.",
}
SEGMENTS = [
    ("v1/seg/0", "v1", 0, 21, "ANIMALS > municipal 1-1 Dogs."),
    ("v2/seg/0", "v2", 0, 18, "ANIMALS > municipal 1-2 Cats."),
    ("v2/seg/1", "v2", 19, 46, "ANIMALS > municipal 1-2 Cats."),
    ("v3/seg/0", "v1", 0, 21, "ANIMALS > municipal 1-1 Dogs."),
]


def derived(cfg, segments=SEGMENTS):
    versions = pl.DataFrame({"id": list(TEXTS), "text": list(TEXTS.values())})
    write_parquet(versions, dataset_path(cfg, registry.LAW_VERSIONS, J))
    rows = [
        {
            "id": sid,
            "block_id": block,
            "block_kind": "law_section",
            "start_char": s,
            "end_char": e,
            "context": ctx,
            "content_sha256": hashlib.sha256(content(ctx, TEXTS[block][s:e]).encode()).hexdigest(),
        }
        for sid, block, s, e, ctx in segments
    ]
    write_parquet(pl.DataFrame(rows), dataset_path(cfg, registry.SEGMENTS, J))


def test_the_embedded_text_is_rebuilt_from_the_tables(cfg):
    derived(cfg)
    found = text.segments(cfg, J)
    assert found["content"][2] == "ANIMALS > municipal 1-2 Cats.\n\n(b) Cats must be licensed."
    assert text.check(cfg, J) == []
    assert text.jurisdictions(cfg) == [J]


def test_a_changed_text_is_reported_by_check(cfg):
    derived(cfg)
    TEXTS_CHANGED = {**TEXTS, "v1": "Dogs must be muzzled."}
    versions = pl.DataFrame({"id": list(TEXTS_CHANGED), "text": list(TEXTS_CHANGED.values())})
    write_parquet(versions, dataset_path(cfg, registry.LAW_VERSIONS, J))
    assert [m.segment_id for m in text.check(cfg, J)] == ["v1/seg/0", "v3/seg/0"]


def stub(calls):
    def embed(texts):
        calls.append(list(texts))
        return [[float(len(t)), 1.0, 0.0, 0.0] for t in texts]

    return embed


def test_pending_is_each_hash_once_and_embedding_stores_it(cfg):
    derived(cfg)
    todo = run.pending(cfg, SMALL, J)
    assert todo.height == 3
    calls: list = []
    done = run.embed_pending(cfg, SMALL, J, todo, embed=stub(calls), spend=1.0, writer="J. Doe")
    assert done.embedded == 3 and done.spent == pytest.approx(done.dollars)
    assert sum(len(c) for c in calls) == 3
    [path] = done.files
    assert path.parent == store.folder(cfg, SMALL, J)
    assert path.name.endswith("-j_doe.parquet")
    assert run.pending(cfg, SMALL, J).height == 0
    assert store.vectors(cfg, SMALL, J).height == 3
    assert run.pending(cfg, QWEN, J).height == 3


def test_the_spend_limit_stops_the_run_and_a_later_run_resumes(cfg):
    derived(cfg)
    todo = run.pending(cfg, SMALL, J)
    first_cost = todo["tokens"][0] * SMALL.dollars_per_token
    done = run.embed_pending(cfg, SMALL, J, todo, embed=stub([]), spend=first_cost, writer="a")
    assert done.embedded == 1
    rest = run.pending(cfg, SMALL, J)
    assert rest.height == 2
    run.embed_pending(cfg, SMALL, J, rest, embed=stub([]), spend=1.0, writer="a", flush=1)
    assert len(store.files(cfg, SMALL, J)) == 3


def test_no_spend_embeds_nothing(cfg):
    derived(cfg)
    todo = run.pending(cfg, SMALL, J)
    done = run.embed_pending(cfg, SMALL, J, todo, embed=stub([]), spend=0.0, writer="a")
    assert (done.embedded, done.files) == (0, ())
    assert done.pending == 3 and done.dollars > 0


def test_two_stores_of_one_hash_read_as_one_vector(cfg):
    store.write(cfg, SMALL, J, ["h"], [[1.0, 0, 0, 0]], "a")
    store.write(cfg, SMALL, J, ["h"], [[1.0, 0, 0, 0]], "b")
    assert store.vectors(cfg, SMALL, J).height == 1
    assert store.glob(cfg, SMALL) == "embeddings/model=qwen3-embedding-8b-4/*/*.parquet"
