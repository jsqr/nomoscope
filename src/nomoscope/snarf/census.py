"""The Census Bureau's lists of governments, a source that any US project can register.

Four feeds, each one file: the states (`state.txt`), and the national
gazetteer files of counties, places, and county subdivisions for a year.
Fetch them with `snarf fetch census -f states -f counties -f places
-f cousubs -p year=<year>`. No cruncher reads them; the jurisdictions
reference dataset does (`derive/census.py`).
"""

from nomoscope.snarf.feeds import Feed, Source

ID = "census"
STATES = "states"
COUNTIES = "counties"
PLACES = "places"
COUSUBS = "cousubs"
FEEDS = (STATES, COUNTIES, PLACES, COUSUBS)

GAZETTEER = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "{year}_Gazetteer/{year}_Gaz_%s_national.zip"
)


def _feed(name: str, url: str, unwrap: str) -> Feed:
    return Feed(name, (url,), unwrap, days=(), completeness="full", crunchable=False)


SOURCE = Source(
    ID,
    (
        _feed(STATES, "https://www2.census.gov/geo/docs/reference/state.txt", "none"),
        _feed(COUNTIES, GAZETTEER % "counties", "zip"),
        _feed(PLACES, GAZETTEER % "place", "zip"),
        _feed(COUSUBS, GAZETTEER % "cousubs", "zip"),
    ),
    (),
    lambda d: {"jurisdiction": "us"},
)
