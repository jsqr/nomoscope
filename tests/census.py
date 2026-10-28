"""A few rows of each Census file, imported into the archive as a fetch would leave them."""

import zipfile
from pathlib import Path

from nomoscope.settings import Settings
from nomoscope.snarf import census, local

STATES = (
    "STATE|STUSAB|STATE_NAME|STATENS\n"
    "11|DC|District of Columbia|01702382\n"
    "17|IL|Illinois|01779784\n"
    "36|NY|New York|01779796\n"
    "72|PR|Puerto Rico|01779808\n"
)
# The real files pad their last column with spaces.
COUNTIES = [
    ("USPS", "GEOID", "ANSICODE", "NAME", "ALAND"),
    ("IL", "17031", "01784766", "Cook County", "2447370818   "),
    ("IL", "17037", "00422191", "DeKalb County", "1645139497   "),
    ("NY", "36039", "00974118", "Greene County", "1678364040   "),
]
PLACES = [
    ("USPS", "GEOID", "ANSICODE", "NAME", "LSAD", "FUNCSTAT", "ALAND"),
    ("IL", "1714000", "00428803", "Chicago city", "25", "A", "589764134   "),
    ("IL", "1723074", "02394670", "Elgin city", "25", "A", "98211498   "),
    # The jurisdiction of the other tests' code.
    ("IL", "1799999", "09999999", "Windytown city", "25", "A", "1000000   "),
    ("NY", "3636167", "02390907", "Hunter village", "47", "A", "4689283   "),
    ("NY", "3601682", "02389131", "Pine Plains CDP", "57", "S", "2964541   "),
]
COUSUBS = [
    ("USPS", "GEOID", "ANSICODE", "NAME", "FUNCSTAT", "ALAND"),
    ("NY", "3602701693", "00978674", "Pine Plains town", "A", "112132696   "),
    ("NY", "3603936178", "00979086", "Hunter town", "A", "232815806   "),
    ("IL", "1703114000", "00428803", "Chicago city", "F", "589764134   "),
    ("IL", "1703148659", "00429339", "Milan township", "A", "93117392   "),
    ("IL", "1703748672", "00429340", "Milan township", "A", "93285743   "),
]


def _tsv(rows: list[tuple[str, ...]]) -> str:
    return "".join("\t".join(r) + "\n" for r in rows)


def write(cfg: Settings, directory: Path, feeds: tuple[str, ...] = census.FEEDS) -> None:
    """Import the files of `feeds` into the archive."""
    directory.mkdir(parents=True, exist_ok=True)
    states = directory / "state.txt"
    states.write_text(STATES)
    paths = {census.STATES: states}
    for feed, rows in (
        (census.COUNTIES, COUNTIES),
        (census.PLACES, PLACES),
        (census.COUSUBS, COUSUBS),
    ):
        path = directory / f"2024_Gaz_{feed}_national.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(f"2024_Gaz_{feed}_national.txt", _tsv(rows))
        paths[feed] = path
    for feed in feeds:
        path = paths[feed]
        local.import_path(
            cfg,
            census.SOURCE,
            census.SOURCE.feed(feed),
            path,
            {"jurisdiction": "us"},
            importer="test",
        )
