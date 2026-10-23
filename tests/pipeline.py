"""Run the fake source through snarf, crunch, and derive."""

from nomoscope.crunch import runner
from nomoscope.derive import build, reference
from nomoscope.graph.store import Store
from nomoscope.settings import Settings
from nomoscope.snarf import archive, local
from tests import fake

P1 = """\
publication 2024-01-15
edition Supp. No. 1
node 1 | Chapter 1 | General Provisions
section 1-1 | Title | Ord. 2019-12, passed 12-3-2019, eff. 1-1-2020
This code is the Windytown Code.
section 1-2 | Definitions | Ord. 2019-12, eff. 1-1-2020
(a) "City" means Windytown.
(b) "Code" means this code.
node 2 | Chapter 2 | Animals
section 2-1 | Dogs at large | Ord. 2019-12, eff. 1-1-2020
No dog shall run at large.
"""
# A partial publication with only the amended section.
P2 = """\
publication 2024-06-01
edition Supp. No. 2
node 1 | Chapter 1 | General Provisions
section 1-2 | Definitions | Ord. 2019-12; Ord. 2024-5, passed 5-1-2024, eff. 6-1-2024
(a) "City" means the City of Windytown.
(b) "Code" means this code.
(c) "Person" includes a corporation.
"""
# A full publication without section 2-1 or chapter 2.
P3 = """\
publication 2025-01-10
edition Supp. No. 3
node 1 | Chapter 1 | General Provisions
section 1-1 | Title | Ord. 2019-12, passed 12-3-2019, eff. 1-1-2020
This code is the Windytown Code.
section 1-2 | Definitions | Ord. 2019-12; Ord. 2024-5, passed 5-1-2024, eff. 6-1-2024
(a) "City" means the City of Windytown.
(b) "Code" means this code.
(c) "Person" includes a corporation.
"""


def publish(cfg: Settings, tmp_path, text: str, name: str, **params: str) -> archive.Record:
    path = tmp_path / name
    path.write_text(text)
    out = local.import_path(
        cfg,
        fake.SOURCE,
        fake.CODE,
        path,
        {"jurisdiction": "us-il-windytown"} | params,
        importer="test",
    )
    assert out.record is not None
    return out.record


def crunch(cfg: Settings) -> None:
    runner.crunch(cfg, Store(cfg), fake.REGISTRY)


def derive(cfg: Settings, *, force: bool = False) -> list[build.BuildResult]:
    store = Store(cfg)
    reference.build(cfg, store, force=force)
    return build.build(cfg, store, force=force)
