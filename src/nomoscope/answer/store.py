"""Answers, in files that are only ever added.

    answers/group=<name>/jurisdiction=<j>/<time>-<writer>.parquet

One file per group run in a jurisdiction, one row per question asked.
Answers are paid for, so they are kept with the archive's rules, not
derived's. A row holds the passages supplied with their scores, so the
same prompt can be rebuilt from the segments.
"""

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from nomoscope.answer.ask import GroupRun
from nomoscope.answer.group import Group
from nomoscope.coverage import writer_slug
from nomoscope.parquet import write_parquet
from nomoscope.partition import partition_path
from nomoscope.settings import Settings

TIER = "answers"
VIEW = "answers"
PASSAGE = pl.Struct(
    {
        "label": pl.String,
        "law_version_id": pl.String,
        "segment_id": pl.String,
        "citation": pl.String,
        "score": pl.Float64,
    }
)
SCHEMA = pl.Schema(
    {
        "run_id": pl.String,
        "group": pl.String,
        "group_sha256": pl.String,
        "question_id": pl.String,
        "question": pl.String,
        "after": pl.List(pl.String),
        "jurisdiction": pl.String,
        "on_date": pl.Date,
        "model": pl.String,
        "answer": pl.String,
        "rationale": pl.String,
        "confidence": pl.Float64,
        "citations": pl.List(pl.String),
        "passages": pl.List(PASSAGE),
        "input_tokens": pl.Int64,
        "output_tokens": pl.Int64,
        "dollars": pl.Float64,
        "asked_at": pl.Datetime("us"),
        "error": pl.String,
    }
)


def folder(cfg: Settings, group: str, jurisdiction: str) -> Path:
    return partition_path(cfg.data_root / TIER, {"group": group, "jurisdiction": jurisdiction})


def rows(g: Group, run: GroupRun, run_id: str, asked_at: datetime) -> pl.DataFrame:
    out = []
    for a in run.answered:
        r = a.response
        out.append(
            {
                "run_id": run_id,
                "group": g.name,
                "group_sha256": g.sha256,
                "question_id": a.question.id,
                "question": a.question.text,
                "after": list(a.question.after),
                "jurisdiction": a.jurisdiction,
                "on_date": a.on,
                "model": a.model,
                "answer": r.answer if r else None,
                "rationale": r.rationale if r else None,
                "confidence": r.confidence if r else None,
                "citations": list(r.citations) if r else [],
                "passages": [
                    {
                        "label": p.label,
                        "law_version_id": p.hit.law_version_id,
                        "segment_id": p.hit.segment_id,
                        "citation": p.hit.citation,
                        "score": p.hit.score,
                    }
                    for p in a.passages
                ],
                "input_tokens": a.input_tokens,
                "output_tokens": a.output_tokens,
                "dollars": a.dollars,
                "asked_at": asked_at.replace(tzinfo=None),
                "error": a.error,
            }
        )
    return pl.DataFrame(out, schema=SCHEMA)


def write(
    cfg: Settings, g: Group, run: GroupRun, writer: str, at: datetime | None = None
) -> Path | None:
    """Add one file for the run. None when nothing was asked."""
    if not run.answered:
        return None
    when = (at or datetime.now(UTC)).astimezone(UTC)
    stamp = when.strftime("%Y-%m-%dT%H%M%S%fZ")
    run_id = f"{stamp}-{writer_slug(writer)}"
    path = folder(cfg, g.name, run.jurisdiction) / f"{run_id}.parquet"
    if path.exists():
        raise FileExistsError(path)
    write_parquet(rows(g, run, run_id, when), path)
    return path


def views(cfg: Settings) -> dict[str, str]:
    """The catalog view of the stored answers, if there are any."""
    root = cfg.data_root / TIER
    if not any(root.glob("*/*/*.parquet")):
        return {}
    return {VIEW: (root.relative_to(cfg.data_root) / "*" / "*" / "*.parquet").as_posix()}
