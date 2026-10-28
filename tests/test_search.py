from datetime import date

import pytest
from typer.testing import CliRunner

from nomoscope import cli
from nomoscope.embed import models, store, text
from nomoscope.search import fulltext, query
from tests import census, fake
from tests.pipeline import P1, P2, P3

pytestmark = pytest.mark.usefixtures("conventions")
J = "us-il-windytown"
MODEL = models.embedding_model(models.DEFAULT_EMBEDDING)
BEFORE = date(2024, 7, 1)


def invoke(*args, code: int = 0) -> str:
    out = CliRunner().invoke(cli.app(fake.REGISTRY, fake.APP), list(args))
    assert out.exit_code == code, out.output
    return out.output


@pytest.fixture
def three(cfg, tmp_path):
    census.write(cfg, tmp_path / "census")
    for body, name, params in ((P1, "p1.txt", []), (P2, "p2.txt", ["-pcompleteness=partial"]),
                               (P3, "p3.txt", [])):  # fmt: skip
        path = tmp_path / name
        path.write_text(body)
        invoke("snarf", "import", "fake", str(path), "-f", "code", f"-pjurisdiction={J}", *params)
    invoke("crunch")
    invoke("derive")
    return cfg


def axis(i: int) -> list[float]:
    return [1.0 if k == i else 0.0 for k in range(MODEL.dim)]


@pytest.fixture
def vectors(three):
    """Each distinct segment text on its own axis; returns the axis of each segment id."""
    found = text.segments(three, J)
    hashes = found.unique("content_sha256", maintain_order=True)["content_sha256"].to_list()
    store.write(three, MODEL, J, hashes, [axis(i) for i in range(len(hashes))], "test")
    return {
        sid: hashes.index(h) for sid, h in zip(found["id"], found["content_sha256"], strict=True)
    }


def segment_of(cfg, section: str) -> str:
    found = text.segments(cfg, J)
    return next(s for s in found["id"].to_list() if f"/{section}/" in s)


def test_the_nearest_vector_is_the_first_hit(three, vectors):
    target = segment_of(three, "2-1")
    hits = query.search(three, "dogs", J, on=BEFORE, vectorize=lambda t: axis(vectors[target]))
    assert hits[0].section_num == "2-1"
    assert hits[0].vector == pytest.approx(1.0)
    assert hits[0].snippet == "No dog shall run at large."
    assert len({h.law_version_id for h in hits}) == len(hits)


def test_the_query_carries_the_instruction(three, vectors):
    seen = []
    query.search(three, "dogs", J, vectorize=lambda t: seen.append(t) or axis(0))
    assert seen == [f"Instruct: {query.INSTRUCTION}\nQuery:dogs"]


def test_a_section_not_in_force_on_the_date_is_not_a_hit(three, vectors):
    target = segment_of(three, "2-1")
    hits = query.search(three, "dogs", J, vectorize=lambda t: axis(vectors[target]))
    assert "2-1" not in {h.section_num for h in hits}


def test_full_text_alone_needs_no_vectors(three):
    hits = query.search(three, "dog at large", J, on=BEFORE, vector=False, text=True)
    assert hits[0].section_num == "2-1" and hits[0].text is not None and hits[0].vector is None


def test_without_vectors_the_error_names_embed(three):
    with pytest.raises(query.SearchError, match="run embed"):
        query.search(three, "dogs", J, vectorize=lambda t: axis(0))


def test_fusion_adds_reciprocal_ranks():
    fused = query.fused([["a", "b", "c"], ["c", "a"]], k=60)
    assert [sid for sid, _ in fused] == ["a", "c", "b"]
    assert dict(fused)["a"] == pytest.approx(1 / 61 + 1 / 62)


def test_both_lists_are_fused(three, vectors):
    target = segment_of(three, "1-1")
    hits = query.search(
        three, "dog at large", J, on=BEFORE, text=True, vectorize=lambda t: axis(vectors[target])
    )
    by_section = {h.section_num: h for h in hits}
    assert by_section["2-1"].text is not None and by_section["1-1"].vector == pytest.approx(1.0)
    assert hits[0].score == pytest.approx(max(h.score for h in hits))


def test_rerank_orders_the_top_by_its_scores(three, vectors):
    seen = []

    def scores(q, documents):
        seen.append((q, documents))
        return [1.0 if "dog" in d else 0.0 for d in documents]

    hits = query.search(
        three, "dogs", J, on=BEFORE, rerank=True, vectorize=lambda t: axis(0), rerank_scores=scores
    )
    assert hits[0].section_num == "2-1" and hits[0].rerank == 1.0
    [(q, documents)] = seen
    assert q == "dogs" and any(d.endswith("No dog shall run at large.") for d in documents)


def test_the_text_index_is_rebuilt_when_segments_change(three, monkeypatch):
    path = fulltext.ensure(three)
    built = path.stat().st_mtime_ns
    assert fulltext.ensure(three).stat().st_mtime_ns == built
    monkeypatch.setattr(fulltext, "stamp", lambda cfg: "changed")
    assert fulltext.ensure(three).stat().st_mtime_ns != built
    assert fulltext.current(three, "changed")


def test_an_unknown_search_setting_is_refused():
    with pytest.raises(query.SearchError, match="deepth: unknown"):
        query.options({"deepth": 5})
    assert query.options({"depth": 5}).depth == 5


def test_the_search_command_prints_citations(three):
    out = invoke("search", "dog at large", "-j", J, "--no-vector", "--text", "--on", "2024-07-01")
    assert "municipal 2-1  Dogs at large" in out
    assert "No dog shall run at large." in out
    assert "run embed" in invoke("search", "dogs", "-j", J, code=1)


def test_the_catalog_shows_one_stored_vector_per_hash(three, vectors):
    hashes = text.segments(three, J)["content_sha256"].unique().to_list()
    store.write(three, MODEL, J, hashes[:1], [axis(0)], "again")
    invoke("catalog")
    out = invoke("sql", "SELECT count(*) AS n FROM segment_embeddings", "--format", "json")
    assert f'"n":{len(hashes)}' in out


def test_embed_estimates_without_spend_and_checks_texts(three):
    out = invoke("embed")
    assert J in out and "pending" in out and "embedded" not in out
    assert "0 segments whose text does not match" in invoke("embed", "--check")


def test_embed_spends_and_rebuilds_the_catalog(three, monkeypatch):
    from nomoscope.embed import client

    monkeypatch.setenv("FIREWORKS_API_KEY", "test")
    monkeypatch.setattr(client, "embed", lambda http, model, texts: [axis(0) for _ in texts])
    out = invoke("embed", "--spend", "1")
    assert "embedded" in out and "catalog" in out
    assert "0 pending" in invoke("embed")
    assert '"n":' in invoke(
        "sql", "SELECT count(*) AS n FROM segment_embeddings", "--format", "json"
    )


def test_search_prints_json_and_refuses_an_unknown_format(three):
    out = invoke("search", "dog", "-j", J, "--no-vector", "--text", "--on", "2024-07-01",
                 "--format", "json")  # fmt: skip
    first = out.splitlines()[0]
    assert '"citation": "municipal 2-1"' in first
    assert "--format must be" in invoke("search", "dog", "-j", J, "--format", "csv", code=1)


def test_a_mistyped_setting_is_an_error():
    with pytest.raises(query.SearchError, match="depth: expected int"):
        query.options({"depth": "5"})
    with pytest.raises(models.EmbedError, match="1 to 4096"):
        models.configured({"dimensions": "1024"})
    with pytest.raises(models.EmbedError, match="dims: unknown"):
        models.configured({"dims": 1024})
