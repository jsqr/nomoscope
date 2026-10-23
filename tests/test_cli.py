from typer.testing import CliRunner

from nomoscope import cli
from nomoscope.crunch.registry import Registry
from nomoscope.snarf import archive
from nomoscope.snarf.feeds import Feed, Source
from tests import fake

FILE = Feed("code", ("",), "none", days=(), completeness="full")
SRC = Source("manual", (FILE,), (), lambda d: {})
APP = cli.app(Registry(sources={SRC.id: SRC}), fake.APP)


def invoke(*args):
    return CliRunner().invoke(APP, list(args))


def test_settings_prints_the_resolved_roots_and_the_registry(cfg):
    out = invoke("settings")
    assert out.exit_code == 0, out.output
    assert f"data_root:   {cfg.data_root}" in out.output
    assert f"config_root: {cfg.config_root}" in out.output
    assert "sources:     manual" in out.output and "crunchers:   (none)" in out.output


def test_snarf_import_archives_a_file_once(cfg, tmp_path):
    path = tmp_path / "code.txt"
    path.write_text("Sec. 1.\n")
    args = ("snarf", "import", "manual", str(path), "--feed", "code", "-p", "jurisdiction=us-il")
    first = invoke(*args, "--importer", "jj")
    assert first.exit_code == 0, first.output
    [record] = archive.records(cfg.snarfed, "manual")
    assert record.id in first.output
    assert record.fetch().params == {
        "jurisdiction": "us-il",
        "importer": "jj",
        "mtime": record.fetch().params["mtime"],
    }
    again = invoke(*args)
    assert again.exit_code == 0 and "unchanged" in again.output


def test_commands_report_user_errors_without_a_traceback(cfg, tmp_path):
    path = tmp_path / "x.txt"
    path.write_text("x")
    unknown = invoke("snarf", "import", "nobody", str(path), "--feed", "code")
    assert unknown.exit_code == 1 and "unknown source 'nobody'" in unknown.output
    bad = invoke("snarf", "import", "manual", str(path), "--feed", "code", "-p", "oops")
    assert bad.exit_code == 1 and "KEY=VALUE" in bad.output
    missing = invoke("snarf", "fetch", "manual")
    assert missing.exit_code == 1 and "choose feeds" in missing.output
    invoke("snarf", "import", "manual", str(path), "--feed", "code", "-p", "jurisdiction=us-il")
    (cfg.config_root / "crunchers.csv").write_text(
        "source,jurisdiction,law_code,cruncher,valid_from\nmanual,*,*,absent,\n"
    )
    unregistered = invoke("crunch")
    assert unregistered.exit_code == 1 and "unknown cruncher 'absent'" in unregistered.output


def test_a_record_no_route_matches_is_not_crunched(cfg, tmp_path):
    path = tmp_path / "x.txt"
    path.write_text("x")
    invoke("snarf", "import", "manual", str(path), "--feed", "code", "-p", "jurisdiction=us-il")
    out = invoke("crunch")
    assert out.exit_code == 0 and "nothing to crunch" in out.output
    status = invoke("status")
    assert status.exit_code == 0, status.output
    # archived, registered, unrouted
    assert ["manual", "1", "1", "1"] in [line.split() for line in status.output.splitlines()]


def test_snarf_due_fetches_nothing_when_no_feed_is_due(cfg):
    out = invoke("snarf", "due")
    assert out.exit_code == 0 and out.output == ""
