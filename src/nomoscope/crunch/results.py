"""One-line summaries of crunch steps, for the command's output."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TableResult:
    table: str
    rows: int
    path: str


@dataclass(frozen=True)
class CrunchResult:
    cruncher: str
    stage: str
    unit: str
    jurisdiction: str
    tables: dict[str, TableResult]
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        rows = sum(t.rows for t in self.tables.values())
        line = (
            f"{self.cruncher:20} {self.stage:6} {self.unit}  {self.jurisdiction:24}"
            f" {len(self.tables):2} tables {rows:9} rows"
        )
        if self.skipped:
            line += "  skipped " + ", ".join(f"{k} {n}" for k, n in self.skipped.items())
        return line
