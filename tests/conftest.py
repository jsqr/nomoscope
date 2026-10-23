import pytest

from nomoscope import settings
from tests import conventions as drafting
from tests import fake


@pytest.fixture(autouse=True)
def scratch_settings(monkeypatch, tmp_path_factory):
    """Point every test at a scratch data root and config root.

    The config root routes the fake source to its cruncher.
    """
    monkeypatch.delenv(settings.env_data_root(fake.APP), raising=False)
    root = tmp_path_factory.mktemp("settings")
    (root / "config").mkdir()
    (root / "config" / "crunchers.csv").write_text(fake.ROUTES)
    path = root / f"{fake.APP}.toml"
    path.write_text(f'data_root = "{root / "data"}"\nconfig_root = "config"\n')
    monkeypatch.setenv(settings.env_config(fake.APP), str(path))


@pytest.fixture
def cfg() -> settings.Settings:
    return settings.load(fake.APP)


@pytest.fixture
def conventions(cfg):
    """Write the test conventions to the scratch config root."""
    drafting.write(cfg.config_root)
    return drafting.CONVENTIONS
