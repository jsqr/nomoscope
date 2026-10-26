"""The registry: a project's sources and crunchers, by name, and what else
nomoscope needs to know about the project: the routes from records to
crunchers, the segment settings derive uses, and the directory of drafting
conventions.

A cruncher plans and runs steps over the records routed to it. For each
record it carves, it must write a carve step keyed (carve, <cruncher>,
<record>) with runner.write_carved. Derive reads only those steps.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from nomoscope.crunch import CrunchError
from nomoscope.crunch.results import CrunchResult
from nomoscope.crunch.routes import Routes
from nomoscope.graph.records import RecordRow
from nomoscope.graph.steps import Planned
from nomoscope.graph.store import Store
from nomoscope.segments import DEFAULT as DEFAULT_SEGMENTS
from nomoscope.segments import Settings as SegmentSettings
from nomoscope.settings import Settings
from nomoscope.snarf.feeds import FeedError, Source


class Cruncher(Protocol):
    def plan(self, cfg: Settings, name: str, rows: Sequence[RecordRow]) -> list[Planned]:
        """Plan every step for the routed records, in run order.

        name is the cruncher's name in the registry, and the scope of its steps.
        """
        ...

    def run(
        self,
        cfg: Settings,
        store: Store,
        planned: Planned,
        rows: Mapping[str, RecordRow],
    ) -> CrunchResult:
        """Run one planned step and record it.

        rows holds every record routed to the cruncher, by id.
        """
        ...


@dataclass(frozen=True)
class Registry:
    sources: Mapping[str, Source] = field(default_factory=dict)
    crunchers: Mapping[str, Cruncher] = field(default_factory=dict)
    routes: Routes = ()
    segments: SegmentSettings = DEFAULT_SEGMENTS
    conventions: Path | None = None

    def source(self, source_id: str) -> Source:
        try:
            return self.sources[source_id]
        except KeyError:
            raise FeedError(
                f"unknown source {source_id!r}; known: {sorted(self.sources)}"
            ) from None

    def cruncher(self, name: str) -> Cruncher:
        try:
            return self.crunchers[name]
        except KeyError:
            raise CrunchError(
                f"unknown cruncher {name!r}; known: {sorted(self.crunchers)}"
            ) from None
