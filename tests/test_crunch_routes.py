from datetime import UTC, date, datetime

import pytest

from nomoscope.crunch import routes
from nomoscope.crunch.routes import Route, RouteError
from nomoscope.graph.records import RecordRow


def row(source="codifier", jurisdiction="us-il-chicago", code="municipal", on=date(2024, 1, 1)):
    when = datetime(on.year, on.month, on.day, 12, tzinfo=UTC)
    return RecordRow(
        record="r1",
        feed="code",
        jurisdiction=jurisdiction,
        code=code,
        session=None,
        completeness="full",
        fetched_at=when,
        last_modified=None,
        sha256="a" * 64,
        crunchable=True,
        item_count=1,
        size=1,
        source=source,
        code_version="1",
        registered_at=when,
    )


def parsed(*lines: str) -> routes.Routes:
    """Routes from "source,jurisdiction,law_code,cruncher,valid_from" lines."""
    found = []
    for line in lines:
        source, jurisdiction, code, cruncher, since = line.split(",")
        valid_from = date.fromisoformat(since) if since else None
        found.append(Route(source, jurisdiction, code, cruncher, valid_from))
    return tuple(found)


def test_the_most_specific_route_wins():
    table = parsed(
        "codifier,*,*,generic,",
        "codifier,us-il-chicago,*,chicago,",
        "codifier,us-il-chicago,zoning,chicago-zoning,",
    )
    assert routes.route(table, row()) == "chicago"
    assert routes.route(table, row(code="zoning")) == "chicago-zoning"
    assert routes.route(table, row(jurisdiction="us-il-elgin")) == "generic"
    assert routes.route(table, row(source="other")) is None


def test_valid_from_switches_cruncher_on_the_records_date():
    table = parsed(
        "codifier,us-il-chicago,municipal,old-format,",
        "codifier,us-il-chicago,municipal,new-format,2024-06-01",
    )
    assert routes.route(table, row(on=date(2024, 5, 31))) == "old-format"
    assert routes.route(table, row(on=date(2024, 6, 1))) == "new-format"


def test_a_record_of_every_code_matches_only_a_wildcard_code():
    table = parsed("state,us-ca,GOV,one-code,", "state,us-ca,*,dump,")
    assert routes.route(table, row(source="state", jurisdiction="us-ca", code="")) == "dump"


def test_a_tie_between_crunchers_is_an_error():
    table = parsed("codifier,us-il-chicago,*,a,", "*,us-il-chicago,municipal,b,")
    with pytest.raises(RouteError, match="tie between crunchers"):
        routes.route(table, row())
    same = (Route("codifier", "*", "*", "a"), Route("codifier", "*", "*", "a"))
    assert routes.route(same, row()) == "a"
