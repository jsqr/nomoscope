"""Write parquet files atomically: to a temporary file, then rename."""

import os
from pathlib import Path

import polars as pl


def write_parquet(frame: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    frame.write_parquet(tmp)
    os.replace(tmp, path)
