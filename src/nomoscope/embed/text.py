"""The text embedded for each segment, rebuilt from the derived tables.

A segment stores its context and a span of its block's text. The embedded
text is `segment.content(context, block.text[start_char:end_char])`, and its
sha256 is the segment's `content_sha256`. `check` rebuilds every segment's
text and reports those whose hash differs.
"""

import hashlib
from dataclasses import dataclass

import polars as pl

from nomoscope.derive import registry
from nomoscope.derive.segment import content
from nomoscope.partition import dataset_path
from nomoscope.settings import Settings

BLOCK_KINDS = {k.kind: k for k in (registry.LAW_SECTION,)}
COLUMNS = ("id", "block_id", "block_kind", "start_char", "end_char", "context", "content_sha256")


def jurisdictions(cfg: Settings) -> list[str]:
    """The jurisdictions with derived segments."""
    root = cfg.derived / registry.SEGMENTS
    return sorted(p.parent.name.split("=", 1)[1] for p in root.glob("jurisdiction=*/*.parquet"))


def segments(cfg: Settings, jurisdiction: str) -> pl.DataFrame:
    """The jurisdiction's segments with `body` and `content` added. Empty if it has none."""
    path = dataset_path(cfg, registry.SEGMENTS, jurisdiction)
    if not path.is_file():
        return pl.DataFrame(
            schema={**{c: pl.String for c in COLUMNS}, "body": pl.String, "content": pl.String}
        )
    found = pl.read_parquet(path, columns=list(COLUMNS))
    texts: dict[str, str] = {}
    for kind in found["block_kind"].unique().to_list():
        block = BLOCK_KINDS[kind]
        table = pl.read_parquet(
            dataset_path(cfg, block.dataset, jurisdiction), columns=[block.key, "text"]
        )
        texts.update(zip(table[block.key].to_list(), table["text"].to_list(), strict=True))
    bodies = [
        texts[b][s:e]
        for b, s, e in zip(found["block_id"], found["start_char"], found["end_char"], strict=True)
    ]
    contents = [content(c, b) for c, b in zip(found["context"], bodies, strict=True)]
    return found.with_columns(pl.Series("body", bodies), pl.Series("content", contents))


@dataclass(frozen=True)
class Mismatch:
    segment_id: str
    stored: str
    rebuilt: str


def check(cfg: Settings, jurisdiction: str) -> list[Mismatch]:
    """The segments whose rebuilt text does not hash to their content_sha256."""
    found = segments(cfg, jurisdiction)
    wrong = []
    for sid, stored, text in zip(
        found["id"], found["content_sha256"], found["content"], strict=True
    ):
        rebuilt = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if rebuilt != stored:
            wrong.append(Mismatch(sid, stored, rebuilt))
    return wrong
