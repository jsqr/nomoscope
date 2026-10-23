"""Readable ids: the jurisdiction, a kind, and the source's keys, joined by "/".

    us-il-chicago/law/municipal/4-60-010                   a section
    us-il-chicago/law/municipal/4-60-010/3f2a9c1d07e4      a version
    us-ca/law/GOV/65920./id_de728466/3f2a9c1d07e4          a version with a source version id
    us-il-chicago/node/municipal/4.60                      a heading
    us-il-chicago/law/municipal/4-60-010/3f2a9c1d07e4/b/1  a subdivision
    us-il-chicago/law/municipal/4-60-010/3f2a9c1d07e4/history/9b1e0c44d2a7  a history note

A version id ends with the first 12 hex digits of its text's hash. The
source's version id, if any, comes before the hash.

Each function takes polars expressions and returns one. A null key gives
a null id, except the optional version id.
"""

import polars as pl

SEP = "/"
HASH_CHARS = 12


def compose(*parts: str) -> str:
    return SEP.join(parts)


def join(*parts: pl.Expr | str) -> pl.Expr:
    exprs = [pl.lit(p) if isinstance(p, str) else p.cast(pl.String) for p in parts]
    return pl.concat_str(exprs, separator=SEP)


def law_section(jurisdiction: str, code: pl.Expr, section_num: pl.Expr) -> pl.Expr:
    return join(jurisdiction, "law", code, section_num)


def law_version(
    jurisdiction: str,
    code: pl.Expr,
    section_num: pl.Expr,
    version_id: pl.Expr,
    text_sha256: pl.Expr,
) -> pl.Expr:
    section = law_section(jurisdiction, code, section_num)
    digest = text_sha256.str.slice(0, HASH_CHARS)
    return (
        pl.when(version_id.is_null())
        .then(join(section, digest))
        .otherwise(join(section, version_id, digest))
    )


def law_node(jurisdiction: str, code: pl.Expr, treepath: pl.Expr) -> pl.Expr:
    return join(jurisdiction, "node", code, treepath)


def law_history(version: pl.Expr, history_sha256: pl.Expr) -> pl.Expr:
    return join(version, "history", history_sha256.str.slice(0, HASH_CHARS))
