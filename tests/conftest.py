from dataclasses import replace

import pytest

from nomoscope import settings
from tests import conventions as drafting
from tests import fake


@pytest.fixture(autouse=True)
def scratch_settings(monkeypatch, tmp_path_factory):
    """Point every test at a scratch data root."""
    monkeypatch.delenv(settings.env_data_root(fake.APP), raising=False)
    root = tmp_path_factory.mktemp("settings")
    path = root / f"{fake.APP}.toml"
    path.write_text(f'data_root = "{root / "data"}"\n')
    monkeypatch.setenv(settings.env_config(fake.APP), str(path))


@pytest.fixture
def cfg() -> settings.Settings:
    return settings.load(fake.APP)


@pytest.fixture
def conventions(tmp_path, monkeypatch):
    """Write the test conventions to a scratch directory, and give it to the fake registry."""
    directory = tmp_path / "conventions"
    drafting.write(directory)
    monkeypatch.setattr(fake, "REGISTRY", replace(fake.REGISTRY, conventions=directory))
    return drafting.CONVENTIONS
