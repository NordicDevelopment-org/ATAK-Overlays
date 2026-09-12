"""Catalog `alternates:` fallback and the doctor/probe layer (all offline)."""
import pytest

from overlaybuilder.aoi import parse_aoi
from overlaybuilder.build import fetch_with_fallback, merge_alternate, run_build
from overlaybuilder.drivers import Context, driver
from overlaybuilder.model import Feature, LayerResult, Provenance
from overlaybuilder.probe import Probe, _missing_fields, _oneline

_CALLS = []


@driver("_flaky")
def _flaky(logical, spec, ctx):
    _CALLS.append(spec.get("url"))
    if spec.get("url") != "good":
        raise RuntimeError(f"HTTP 404 for {spec.get('url')}")
    return LayerResult(logical, [Feature({"type": "Point", "coordinates": [-92.9, 45.5]}, {"NAME": "x"})],
                       Provenance("Alt source", spec["url"], "public", "2026-09-12", "_flaky"))


def _spec(**kw):
    s = {"layer": "dams", "driver": "_flaky", "id": "dams@primary", "url": "bad1",
         "layer_id": 7, "fields": {"name": {"from": ["NAME"]}}, "entity": "dam"}
    s.update(kw)
    return s


def test_merge_alternate_inherits_and_clears_endpoint_keys():
    m = merge_alternate(_spec(), {"url": "other", "layer_match": "dam", "note": "mirror"})
    assert m["url"] == "other" and m["layer_match"] == "dam"
    assert "layer_id" not in m                      # stale numeric id must not ride along
    assert m["fields"] == {"name": {"from": ["NAME"]}} and m["entity"] == "dam"
    assert "mirror" in m["notes"] and m["_alternate_of"] == "dams@primary"
    assert "alternates" not in m


def test_merge_alternate_keeps_layer_id_when_only_note_changes():
    m = merge_alternate(_spec(), {"note": "same endpoint, documented"})
    assert m["layer_id"] == 7


def test_fallback_uses_second_alternate_and_records_primary_error():
    _CALLS.clear()
    spec = _spec(alternates=[{"url": "bad2", "note": "first mirror"}, {"url": "good", "note": "second mirror"}])
    res, used, attempts = fetch_with_fallback(spec, Context(aoi=parse_aoi("state:MN")), log=lambda *a: None)
    assert _CALLS == ["bad1", "bad2", "good"]
    assert used["url"] == "good" and used["_alternate_of"] == "dams@primary"
    assert [a[1] for a in attempts] == ["ERROR: HTTP 404 for bad1", "ERROR: HTTP 404 for bad2", "ok"]
    assert "FALLBACK" in res.provenance.notes and "bad1" in res.provenance.notes


def test_fallback_disabled_raises_primary_error():
    spec = _spec(alternates=[{"url": "good"}])
    with pytest.raises(RuntimeError, match="bad1"):
        fetch_with_fallback(spec, Context(aoi=parse_aoi("state:MN")), use_alternates=False, log=lambda *a: None)


def test_all_endpoints_dead_reports_the_primary_error():
    spec = _spec(alternates=[{"url": "bad2"}])
    with pytest.raises(RuntimeError, match="bad1"):     # not the last alternate's error
        fetch_with_fallback(spec, Context(aoi=parse_aoi("state:MN")), log=lambda *a: None)


def test_build_marks_fallback_in_the_manifest(tmp_path):
    ctx = Context(aoi=parse_aoi("state:MN"), clip=False)
    srcs = [_spec(alternates=[{"url": "good", "note": "mirror"}])]
    m = run_build(ctx, srcs, str(tmp_path), ["kmz"], False, do_reconcile=False, log=lambda *a: None)
    row = m["layers"][0]
    assert row["status"] == "ok" and row["features"] == 1 and row["fallback"] is True
    assert len(row["attempts"]) == 2
    m2 = run_build(Context(aoi=parse_aoi("state:MN"), clip=False), [_spec()], str(tmp_path), ["kmz"], False,
                   do_reconcile=False, log=lambda *a: None)
    assert m2["layers"][0]["status"].startswith("ERROR") and m2["layers"][0]["fallback"] is not True


def test_probe_detail_is_single_line():
    p = Probe("dead", "GET failed after 1 tries: https://x\n  <urlopen error timed out>")
    assert "\n" not in p.detail and "|" not in p.detail
    assert p.ok is False and Probe("warn", "x").ok and Probe("skip", "x").ok


def test_missing_fields_flags_unmapped_columns():
    spec = {"fields": {"capacity_mw": {"from": ["Total_MW", "Install_MW"]},
                       "voltage_kv": {"from": ["VOLTAGE@kV"]},
                       "fuel": {"const": "gas"}}}
    assert _missing_fields(spec, ["total_mw", "NAME"]) == ["voltage_kv"]
    assert _missing_fields(spec, []) == []           # unknown schema: do not cry wolf


def test_probe_never_reports_a_placeholder_url_as_dead(monkeypatch):
    from overlaybuilder import probe as P
    from overlaybuilder.aoi import parse_aoi as _p
    ctx = Context(aoi=_p("state:MN"))
    pr = P._file({"url": "https://x/v1.geojson?api_key=${NREL_API_KEY}"}, ctx)
    assert pr.status == P.SKIP and pr.ok


def test_probe_rejects_an_html_body_served_for_a_zip(monkeypatch):
    from overlaybuilder import probe as P
    monkeypatch.setattr(P, "http_get", lambda *a, **k: b"<!DOCTYPE html><html>Not Found")
    pr = P._probe_download("https://x/data.zip")
    assert pr.status == P.DEAD and "HTML" in pr.detail
    monkeypatch.setattr(P, "http_get", lambda *a, **k: b"PK\x03\x04")
    assert P._probe_download("https://x/data.zip").status == P.OK


def test_monthly_probe_walks_back_when_the_newest_release_is_missing(monkeypatch):
    from overlaybuilder import probe as P
    from overlaybuilder.aoi import parse_aoi as _p
    tried = []

    def fake(url, *a, **k):
        tried.append(url)
        if len(tried) < 3:
            raise P.HttpStatusError(404, url)
        return b"PK\x03\x04"
    monkeypatch.setattr(P, "http_get", fake)
    pr = P._file({"url": "https://x/{month}_generator{year}.xlsx"}, Context(aoi=_p("state:MN")))
    assert pr.status == P.OK and len(tried) == 3
