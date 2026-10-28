"""The registry: a project's sources and crunchers, by name, and what else
nomoscope needs to know about the project: the routes from records to
crunchers, the segment settings derive uses, the directory of drafting
conventions, a reader of publishers' listings for `coverage match`, and the
project's own tables that the catalog shows as views.

A cruncher plans and runs steps over the records routed to it. For each
record it tabulates, it must write a tabulate step keyed (tabulate, <cruncher>,
<record>) with runner.write_tabulated. Derive reads only those steps.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from nomoscope.coverage import Listing
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
    listings: Callable[[Settings], list[Listing]] | None = None
    # View name -> parquet glob relative to the data root, for tables the project keeps.
    views: Callable[[Settings], Mapping[str, str]] | None = None

    def source(self, source_id: str) -> Source:
        try:
            return self.sources[source_id]
        except KeyError:
            raise FeedError(
                f"unknown source {source_id!r}; known: {sorted(self.sources)}"
            ) from None

    def project_views(self, cfg: Settings) -> Mapping[str, str]:
        return self.views(cfg) if self.views else {}

    def cruncher(self, name: str) -> Cruncher:
        try:
            return self.crunchers[name]
        except KeyError:
            raise CrunchError(
                f"unknown cruncher {name!r}; known: {sorted(self.crunchers)}"
            ) from None
