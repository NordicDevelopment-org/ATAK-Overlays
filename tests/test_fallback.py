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


def test_fallback_provenance_names_the_endpoint_that_answered():
    spec = {"layer": "dams", "driver": "file", "id": "dams@nid",
            "url": "https://nid.sec.usace.army.mil/api/nation/csv",
            "source_name": "USACE National Inventory of Dams",
            "source_url": "https://nid.sec.usace.army.mil/",
            "license": "Public domain (USACE)", "layer_match": "dam"}
    m = merge_alternate(spec, {"driver": "arcgis", "url": "https://mirror/FeatureServer",
                               "note": "Esri weekly cache"})
    assert m["source_url"] == "https://mirror/FeatureServer"        # not the primary's site
    assert "alternate endpoint: Esri weekly cache" in m["source_name"]
    assert "USACE National Inventory of Dams" in m["source_name"]   # the dataset is still named
    assert m["license"] == "Public domain (USACE)"                  # the data licence follows the data
    # an alternate that states its own provenance keeps it
    m2 = merge_alternate(spec, {"url": "https://x", "source_name": "Mirror Co", "source_url": "https://x/about"})
    assert m2["source_name"] == "Mirror Co" and m2["source_url"] == "https://x/about"
    # a note-only alternate is the same endpoint: provenance untouched
    assert merge_alternate(spec, {"note": "documented"})["source_url"] == "https://nid.sec.usace.army.mil/"


def test_alternate_keeps_layer_match_but_drops_layer_id():
    spec = {"layer": "x", "driver": "arcgis", "url": "a", "layer_id": 7, "layer_match": "dam"}
    m = merge_alternate(spec, {"url": "b"})
    assert m["layer_match"] == "dam" and "layer_id" not in m    # ids are host-specific, names travel
    m2 = merge_alternate(spec, {"url": "b", "layer_match": "other"})
    assert m2["layer_match"] == "other"
    m3 = merge_alternate(spec, {"url": "b", "layer_id": 3})
    assert m3["layer_id"] == 3


def test_failed_source_reports_every_endpoint_it_tried(tmp_path):
    spec = _spec(alternates=[{"url": "bad2", "note": "mirror"}])
    ctx = Context(aoi=parse_aoi("state:MN"), clip=False)
    m = run_build(ctx, [spec], str(tmp_path), ["kmz"], False, do_reconcile=False, log=lambda *a: None)
    row = m["layers"][0]
    assert row["status"].startswith("ERROR")
    assert len(row["attempts"]) == 2 and all(a[1].startswith("ERROR") for a in row["attempts"])


def test_probe_missing_fields_honours_normalize_defaults():
    from overlaybuilder.probe import _missing_fields
    # capacity_mw has DEFAULT_FROM candidates including Total_MW
    spec = {"fields": {"capacity_mw": {"from": ["Nonexistent_Column"]}}}
    assert _missing_fields(spec, ["Total_MW", "NAME"]) == []          # the default finds it
    assert _missing_fields(spec, ["OTHER"]) == ["capacity_mw"]
    # punctuation-insensitive, like normalize
    assert _missing_fields({"fields": {"name": {"from": ["DAM_NAME"], "defaults": False}}},
                           ["Dam Name"]) == []


def test_country_bounds_are_cached_not_written_to_the_context(monkeypatch):
    from overlaybuilder.aoi import parse_aoi as _p
    from overlaybuilder.drivers import overpass as ov
    calls = []
    monkeypatch.setattr(ov, "_COUNTRY_BOUNDS", {})
    monkeypatch.setattr(ov, "country_bounds",
                        lambda cc, eps, t, m: calls.append(cc) or (-141.0, 41.6, -52.6, 83.1))
    monkeypatch.setattr(ov, "_run", lambda *a, **k: {"elements": []})
    ctx = Context(aoi=_p("country:CA"))
    for _ in range(3):
        ov.fetch_elements(["power=plant"], "nwr", ctx, {"tile_deg": 40.0})
    assert calls == ["CA"]                       # looked up once
    assert ctx.bbox is None                      # the shared Context is never mutated
