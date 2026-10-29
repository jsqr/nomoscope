"""The graph show listing: every planned step, in run order, with its state."""

from dataclasses import dataclass

from nomoscope.crunch import runner
from nomoscope.crunch.registry import Registry
from nomoscope.derive import build, reference
from nomoscope.derive import conventions as drafting
from nomoscope.extract import build as extracted
from nomoscope.extract import names
from nomoscope.graph import steps
from nomoscope.graph.steps import Planned
from nomoscope.graph.store import Store
from nomoscope.settings import Settings


@dataclass(frozen=True)
class StepLine:
    stage: str
    scope: str
    unit: str
    state: str
    inputs: int


def listing(cfg: Settings, known: Registry) -> list[StepLine]:
    store = Store(cfg)
    if not store.exists():
        return []
    routes = known.routes
    found = drafting.load(known.conventions)
    planned: list[Planned] = []
    with store.open(read_only=True) as conn:
        for routed in runner.plans(cfg, conn, known, routes):
            planned.extend(routed.planned)
        census = reference.census_records(cfg)
        if census:
            planned.append(reference.plan(census))
        planned.extend(p.planned for p in build.plan(conn, routes, found, known.segments))
        tasks = extracted.configured(cfg, known.chat_models)
        planned.extend(extracted.plan(cfg, conn, tasks, names.load(known.citations)))
        state = steps.classify(cfg, conn, planned)
    return [
        StepLine(p.key.stage, p.key.scope, p.key.unit, state[p.key].value, len(p.inputs))
        for p in planned
    ]
