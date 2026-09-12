"""CLI surface: catalog discovery, error handling, exit codes (all offline)."""
import os

import pytest

from overlaybuilder import cli


def _run(args, **env):
    old = dict(os.environ)
    os.environ.update(env)
    try:
        return cli.main(args)
    finally:
        os.environ.clear()
        os.environ.update(old)


def test_explicit_catalog_is_authoritative(tmp_path):
    with pytest.raises(SystemExit, match="not a catalog directory"):
        cli._catalog_dir(str(tmp_path / "nope"))
    (tmp_path / "global").mkdir()
    (tmp_path / "global" / "x.yaml").write_text("sources: []\n")
    assert cli._catalog_dir(str(tmp_path)) == str(tmp_path)


def test_env_var_catalog(tmp_path, monkeypatch):
    (tmp_path / "global").mkdir()
    (tmp_path / "global" / "x.yaml").write_text("sources: []\n")
    monkeypatch.setenv("OVERLAYBUILDER_CATALOG", str(tmp_path))
    assert cli._catalog_dir(None) == str(tmp_path)


def test_validate_fails_on_an_empty_catalog(tmp_path, capsys):
    (tmp_path / "global").mkdir()
    (tmp_path / "global" / "empty.yaml").write_text("sources: []\n")
    assert _run(["validate", "--catalog", str(tmp_path)]) == 1
    assert "empty" in capsys.readouterr().out


def test_validate_passes_on_the_shipped_catalog(capsys):
    assert _run(["validate"]) == 0
    out = capsys.readouterr().out
    assert "0 problem(s)" in out and " sources in " in out


def test_unexpected_errors_do_not_traceback(capsys, monkeypatch):
    monkeypatch.setattr(cli, "cmd_validate", lambda a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _run(["validate"]) == 2
    assert "error: boom" in capsys.readouterr().err


def test_debug_env_reraises(monkeypatch):
    monkeypatch.setattr(cli, "cmd_validate", lambda a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        _run(["validate"], OVERLAYBUILDER_DEBUG="1")


def test_bad_aoi_exits_cleanly():
    with pytest.raises(SystemExit, match="cannot resolve the AOI"):
        _run(["sources", "--aoi", "nonsense"])
    with pytest.raises(SystemExit, match="known state FIPS"):
        _run(["sources", "--aoi", "county:99999"])


def test_world_build_explains_itself(tmp_path, capsys):
    assert _run(["build", "--aoi", "world", "--out", str(tmp_path)]) == 1
    assert "osm_pbf" in capsys.readouterr().out


def test_list_commands_and_sources(capsys):
    assert _run(["list-drivers"]) == 0 and "overpass" in capsys.readouterr().out
    assert _run(["list-regions"]) == 0 and "upper-midwest" in capsys.readouterr().out
    assert _run(["sources", "--aoi", "county:27025", "--sectors", "water"]) == 0
    assert "dams" in capsys.readouterr().out
