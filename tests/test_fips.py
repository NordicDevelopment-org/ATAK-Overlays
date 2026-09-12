import pytest

from overlaybuilder import fips


def test_state_fp():
    assert fips.state_fp("MN") == "27"
    assert fips.state_fp("minnesota") == "27"
    assert fips.state_fp("27") == "27"


def test_abbr_for_fp():
    assert fips.abbr_for_fp("27") == "MN"


def test_resolve_by_fips_no_network():
    # passing county name avoids the gazetteer download
    sfp, cfp, name, abbr = fips.resolve("", county="Chisago", fips="27025")
    assert (sfp, cfp, abbr, name) == ("27", "025", "MN", "Chisago")


def test_bad_fips():
    with pytest.raises(ValueError):
        fips.resolve("", fips="2702")


def test_fips_without_a_name_survives_no_network(monkeypatch):
    import overlaybuilder.fips as f
    monkeypatch.setattr(f, "_gazetteer", lambda cache_dir: (_ for _ in ()).throw(OSError("no network")))
    sfp, cfp, name, abbr = f.resolve("", fips="27025")
    assert (sfp, cfp, abbr) == ("27", "025", "MN") and name == ""      # cosmetic only
    from overlaybuilder.aoi import parse_aoi
    assert parse_aoi("county:27025").slug == "us/mn/27025"


def test_name_lookup_failure_is_explained(monkeypatch):
    import overlaybuilder.fips as f
    monkeypatch.setattr(f, "_gazetteer", lambda cache_dir: (_ for _ in ()).throw(OSError("no network")))
    with pytest.raises(RuntimeError, match="county:27025"):
        f.resolve("MN", county="Chisago")


def test_unknown_state_fips_is_rejected():
    with pytest.raises(ValueError, match="known state FIPS"):
        fips.resolve("", fips="99999")
    from overlaybuilder.aoi import parse_aoi
    with pytest.raises(ValueError):
        parse_aoi("county:99999")


def test_failed_gazetteer_download_leaves_no_poisoned_cache(tmp_path, monkeypatch):
    import overlaybuilder.fips as f
    calls = []

    def boom(url, *a, **kw):
        calls.append(url)
        raise RuntimeError("network down")
    monkeypatch.setattr(f, "http_get", boom)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            f.resolve("MN", county="Chisago", cache_dir=str(tmp_path))
    assert len(calls) == 2                                   # retried, not served from a 0-byte file
    stale = tmp_path / "national_county2020.txt"
    assert not stale.exists() or stale.stat().st_size >= 1024


def test_truncated_gazetteer_is_rejected(tmp_path, monkeypatch):
    import overlaybuilder.fips as f
    monkeypatch.setattr(f, "http_get", lambda url, *a, **kw: b"<html>nope</html>")
    with pytest.raises(RuntimeError, match="gazetteer"):
        f.resolve("MN", county="Chisago", cache_dir=str(tmp_path))
