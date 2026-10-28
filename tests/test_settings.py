from pathlib import Path

import pytest

from nomoscope import settings

APP = "proj"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv(settings.env_config(APP), raising=False)
    monkeypatch.delenv(settings.env_data_root(APP), raising=False)
    monkeypatch.setattr(settings, "USER_CONFIG_DIR", tmp_path / "home-config")


def write(path, data_root="/srv/one", extra=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'data_root = "{data_root}"\n{extra}')
    return path


def test_the_tiers_hang_off_the_data_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "proj.toml", data_root=str(tmp_path / "d"))
    cfg = settings.load(APP)
    assert cfg.app == APP
    assert cfg.snarfed == tmp_path / "d" / "sources" / "snarfed"
    assert cfg.crunched == tmp_path / "d" / "sources" / "crunched"
    assert cfg.derived == tmp_path / "d" / "derived"
    assert cfg.staging == tmp_path / "d" / "staging"
    assert cfg.graph == tmp_path / "d" / "graph.duckdb"
    assert cfg.catalog == tmp_path / "d" / "catalog.duckdb"


def test_the_file_and_variables_are_named_for_the_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "other.toml", data_root="/srv/other")
    with pytest.raises(settings.SettingsError, match="PROJ_DATA_ROOT"):
        settings.load(APP)


def test_search_walks_up_from_cwd(tmp_path, monkeypatch):
    write(tmp_path / "proj.toml")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert settings.find_config_file(APP) == tmp_path / "proj.toml"


def test_the_user_config_is_last(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    user = write(tmp_path / "home-config" / "proj" / "config.toml", data_root="/srv/user")
    assert settings.find_config_file(APP) == user


def test_env_config_beats_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "proj.toml", data_root="/srv/cwd")
    named = write(tmp_path / "named.toml", data_root="/srv/named")
    monkeypatch.setenv("PROJ_CONFIG", str(named))
    assert settings.load(APP).data_root.as_posix() == "/srv/named"


def test_env_data_root_overrides_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "proj.toml", data_root="/srv/file")
    monkeypatch.setenv("PROJ_DATA_ROOT", "/srv/env")
    cfg = settings.load(APP)
    assert cfg.data_root.as_posix() == "/srv/env"
    assert cfg.path == tmp_path / "proj.toml"


def test_env_data_root_alone_needs_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PROJ_DATA_ROOT", str(tmp_path / "d"))
    cfg = settings.load(APP)
    assert cfg.path is None
    assert cfg.user_agent == settings.DEFAULT_USER_AGENT


def test_source_settings_and_user_agent_are_passed_through(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(
        tmp_path / "proj.toml",
        extra='user_agent = "proj/1 (ops@example.org)"\n[sources.codifier]\nkey_env = "K"\n',
    )
    cfg = settings.load(APP)
    assert cfg.source_settings["codifier"]["key_env"] == "K"
    assert cfg.user_agent == "proj/1 (ops@example.org)"


@pytest.mark.parametrize("extra", ['config_root = "config"\n', "[segments]\nlimit = 200\n"])
def test_a_setting_that_moved_to_the_registry_is_refused(tmp_path, monkeypatch, extra):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "proj.toml", extra=extra)
    with pytest.raises(settings.SettingsError, match="registry"):
        settings.load(APP)


def test_a_secret_is_read_from_the_variable_the_file_names(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "proj.toml", extra='[sources.api]\nkey_env = "PROJ_API_KEY"\n')
    cfg = settings.load(APP)
    with pytest.raises(settings.SettingsError, match="PROJ_API_KEY is not set"):
        cfg.secret("api")
    monkeypatch.setenv("PROJ_API_KEY", "s3cret")
    assert cfg.secret("api") == "s3cret"
    with pytest.raises(settings.SettingsError, match="sets no key_env"):
        cfg.secret("other")


def test_missing_data_root_names_the_search_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(settings.SettingsError, match="no data root"):
        settings.load(APP)


def test_explicit_missing_path_is_an_error(tmp_path):
    with pytest.raises(settings.SettingsError, match="not found"):
        settings.load(APP, tmp_path / "absent.toml")


def project_file(tmp_path, text):
    path = tmp_path / "repo" / "settings.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_the_machine_file_is_read_over_the_project_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    machine = write(tmp_path / "proj.toml", extra='[stage]\nmodel = "local"\n[box]\nx = 1\n')
    project = project_file(tmp_path, '[stage]\nmodel = "shared"\neffort = "low"\n')
    cfg = settings.load(APP, project=project, tables={"stage", "box"})
    assert cfg.table("stage") == {"model": "local", "effort": "low"}
    assert cfg.table("box") == {"x": 1}
    assert cfg.table("absent") == {}
    assert cfg.origins["stage.model"] == machine
    assert cfg.origins["stage.effort"] == project
    assert cfg.origins["data_root"] == machine
    assert cfg.project_path == project


def test_an_unknown_top_level_key_names_the_file_and_the_known_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = project_file(tmp_path, '[stagee]\nmodel = "m"\n')
    write(tmp_path / "proj.toml")
    with pytest.raises(settings.SettingsError, match=r"unknown setting stagee in .*settings.toml"):
        settings.load(APP, project=project, tables={"stage"})


def test_nomoscope_reads_its_own_ocr_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "proj.toml", extra='[ocr]\nmodel = "m"\n')
    assert settings.load(APP).table("ocr") == {"model": "m"}


def test_a_missing_project_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "proj.toml")
    with pytest.raises(settings.SettingsError, match="project settings file not found"):
        settings.load(APP, project=tmp_path / "absent.toml")


def test_env_data_root_is_its_own_origin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PROJ_DATA_ROOT", str(tmp_path / "d"))
    assert settings.load(APP).origins["data_root"].name == "$PROJ_DATA_ROOT"


def test_layered_replaces_a_value_that_is_not_a_table():
    merged, origins = settings.layered(
        [(Path("a"), {"t": {"x": {"y": 1}}}), (Path("b"), {"t": {"x": 2}})]
    )
    assert merged == {"t": {"x": 2}}
    assert origins == {"t.x": Path("b")}
