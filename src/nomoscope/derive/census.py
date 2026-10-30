"""The jurisdictions table, from the Census files (`snarf/census.py`).

It holds the country; the states, DC, and territories; every county and
county equivalent; and the places and county subdivisions that have a
functioning government. Census-designated places and other statistical
areas are left out.

A jurisdiction's id joins its state's id and a slug of its name:
`us-il-elgin`, `us-ny-pine_plains`. A county's id ends in its kind,
`us-ny-dutchess_county`, because counties often share a name with a city.
When places and county subdivisions in one state share a slug, as a town
and a village of the same name do, each takes its kind as a suffix,
`us-ny-hunter_town` and `us-ny-hunter_village`. A county subdivision
whose id is still shared, as Iowa's many Webster townships are, sits under
its county: `us-ia-hamilton_county-webster_township`. A place whose id is
still shared, as "Webster" with the suffix `_city` collides with "Webster
City", takes its GEOID: `us-ia-webster_city_1983145`.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping

import polars as pl

from nomoscope.errors import NomoscopeError
from nomoscope.rows import Row
from nomoscope.snarf import census

SCHEMA = pl.Schema(
    {
        "id": pl.String,
        "name": pl.String,
        "kind": pl.String,
        "parent": pl.String,
        "state_fips": pl.String,
        "county_fips": pl.String,
        "place_fips": pl.String,
        "geoid": pl.String,
    }
)
COUNTRY = {"id": "us", "name": "United States", "kind": "country"}
STATE_KINDS = {"DC": "district"}
# Function status: A, B, and C are active general-purpose governments; G is
# one subordinate to another unit, such as an Iowa township.
PLACE_STATUS = {"A", "B", "C"}
COUSUB_STATUS = {"A", "B", "C", "G"}
# The kinds at the end of a name, longest first so "city and borough" wins
# over "borough".
KINDS = sorted(
    [
        "city and borough",
        "census area",
        "planning region",
        "consolidated government",
        "unified government",
        "metropolitan government",
        "metro township",
        "urban county",
        "municipality",
        "municipio",
        "corporation",
        "plantation",
        "township",
        "borough",
        "village",
        "parish",
        "county",
        "town",
        "city",
    ],
    key=len,
    reverse=True,
)


class CensusError(NomoscopeError):
    pass


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")


def split_kind(name: str, default: str) -> tuple[str, str]:
    """A name without its kind, and the kind: "Cold Bay city" -> ("Cold Bay", "city")."""
    lower = name.lower()
    for kind in KINDS:
        if lower.endswith(" " + kind):
            return name[: -len(kind) - 1], kind
    return name, default


def _table(data: bytes, separator: str) -> pl.DataFrame:
    frame = pl.read_csv(data, separator=separator, infer_schema=False)
    return frame.rename({c: c.strip() for c in frame.columns}).with_columns(
        pl.all().str.strip_chars()
    )


def _gazetteer(data: bytes) -> pl.DataFrame:
    """A gazetteer file, as the archive keeps it: unwrapped from its zip."""
    return _table(data, "\t")


def states(data: bytes) -> list[Row]:
    rows = _table(data, "|").to_dicts()
    return [
        {
            "id": f"us-{r['STUSAB'].lower()}",
            "name": r["STATE_NAME"],
            "kind": STATE_KINDS.get(r["STUSAB"], "state" if int(r["STATE"]) <= 56 else "territory"),
            "parent": "us",
            "state_fips": r["STATE"],
            "geoid": r["STATE"],
        }
        for r in rows
    ]


def counties(frame: pl.DataFrame) -> list[Row]:
    out = []
    for r in frame.to_dicts():
        base, kind = split_kind(r["NAME"], "county")
        out.append(
            {
                "id": f"us-{r['USPS'].lower()}-{slug(base)}_{slug(kind)}",
                "name": base,
                "kind": kind,
                "parent": f"us-{r['USPS'].lower()}",
                "state_fips": r["GEOID"][:2],
                "county_fips": r["GEOID"][2:5],
                "geoid": r["GEOID"],
            }
        )
    return out


def locals_(
    places: pl.DataFrame, cousubs: pl.DataFrame, county_ids: Mapping[str, str]
) -> list[Row]:
    """Places and county subdivisions with a functioning government, with ids.

    `county_ids` maps each county's GEOID to its id. A place named for its
    county would otherwise take a county's id: "Webster County unified
    government".
    """
    taken = set(county_ids.values())
    found = []
    for r in places.filter(pl.col("FUNCSTAT").is_in(PLACE_STATUS)).to_dicts():
        base, kind = split_kind(r["NAME"], "place")
        found.append(
            {"usps": r["USPS"], "base": base, "kind": kind, "geoid": r["GEOID"],
             "state_fips": r["GEOID"][:2], "place_fips": r["GEOID"][2:]}
        )  # fmt: skip
    for r in cousubs.filter(pl.col("FUNCSTAT").is_in(COUSUB_STATUS)).to_dicts():
        base, kind = split_kind(r["NAME"], "county subdivision")
        found.append(
            {"usps": r["USPS"], "base": base, "kind": kind, "geoid": r["GEOID"],
             "state_fips": r["GEOID"][:2], "county_fips": r["GEOID"][2:5]}
        )  # fmt: skip

    def stem(f: Row) -> str:
        return f"us-{f['usps'].lower()}-{slug(f['base'])}"

    shared = Counter(stem(f) for f in found) + Counter(taken)
    ids = [f"{stem(f)}_{slug(f['kind'])}" if shared[stem(f)] > 1 else stem(f) for f in found]
    still = Counter(ids) + Counter(taken)

    def unique(found_id: str, f: Row) -> str:
        if still[found_id] == 1:
            return found_id
        county = county_ids.get(f["geoid"][:5]) if f.get("county_fips") else None
        if county is not None:
            return f"{county}-{slug(f['base'])}_{slug(f['kind'])}"
        return f"{found_id}_{f['geoid']}"

    ids = [unique(i, f) for i, f in zip(ids, found, strict=True)]
    # Two subdivisions of one name in one county.
    last = Counter(ids)
    ids = [i if last[i] == 1 else f"{i}_{f['geoid']}" for i, f in zip(ids, found, strict=True)]
    out = []
    for found_id, f in zip(ids, found, strict=True):
        out.append(
            {
                "id": found_id,
                "name": f["base"],
                "kind": f["kind"],
                "parent": f"us-{f['usps'].lower()}",
                "state_fips": f["state_fips"],
                "county_fips": f.get("county_fips"),
                "place_fips": f.get("place_fips"),
                "geoid": f["geoid"],
            }
        )
    return out


def jurisdictions(files: Mapping[str, bytes]) -> pl.DataFrame:
    """The jurisdictions table from the four files, by feed name, as the archive keeps them."""
    missing = [f for f in census.FEEDS if f not in files]
    if missing:
        raise CensusError(f"no Census record of {', '.join(missing)}")
    county_rows = counties(_gazetteer(files[census.COUNTIES]))
    county_ids = {r["geoid"]: r["id"] for r in county_rows}
    local_rows = locals_(
        _gazetteer(files[census.PLACES]), _gazetteer(files[census.COUSUBS]), county_ids
    )
    rows = [COUNTRY, *states(files[census.STATES]), *county_rows, *local_rows]
    frame = pl.DataFrame(rows, schema=SCHEMA)
    known = set(frame["id"])
    orphans = frame.filter(pl.col("parent").is_not_null() & ~pl.col("parent").is_in(known))
    # Gazetteer files can list areas whose state is not in state.txt.
    return frame.join(orphans.select("id"), on="id", how="anti").sort("id")
