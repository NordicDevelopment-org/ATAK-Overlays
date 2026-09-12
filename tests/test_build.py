import json
import os
import time
import zipfile
import xml.dom.minidom as minidom

from overlaybuilder.aoi import parse_aoi
from overlaybuilder.build import run_build
from overlaybuilder.drivers import Context, driver
from overlaybuilder.model import Feature, LayerResult, Provenance
from overlaybuilder.reconcile import compare_pair

RING = [[-93.2, 45.3], [-92.6, 45.3], [-92.6, 45.8], [-93.2, 45.8], [-93.2, 45.3]]


@driver("_fake")
def _fake(logical, spec, ctx):
    prov = Provenance("Fake-" + spec.get("provider", "x"), "http://x", "public", "2026-09-11", "_fake")
    if logical == "county_boundary":
        return LayerResult(logical, [Feature({"type": "Polygon", "coordinates": [RING]}, {"NAME": "Chisago"})], prov)
    if logical == "power_plants" and spec.get("provider") == "osm":
        poly = [[-92.901, 45.501], [-92.899, 45.501], [-92.899, 45.499], [-92.901, 45.499], [-92.901, 45.501]]
        return LayerResult(logical, [Feature({"type": "Polygon", "coordinates": [poly]},
                                             {"name": "Test Plant", "power": "plant", "plant:output:electrical": "130 MW",
                                              "plant:source": "gas", "osm_id": 1, "osm_type": "way"})], prov)
    if logical == "power_plants":
        return LayerResult(logical, [
            Feature({"type": "Point", "coordinates": [-92.9, 45.5]},
                    {"Plant_Name": "Test Plant", "Total_MW": "120.5", "PrimSource": "natural gas", "Plant_Code": 1}),
            Feature({"type": "Point", "coordinates": [-92.9, 45.5]},
                    {"Plant_Name": "Test Plant", "Total_MW": "120.5", "PrimSource": "natural gas", "Plant_Code": 1}),  # dup
            Feature({"type": "Point", "coordinates": [-91.0, 45.5]}, {"Plant_Name": "Outside", "Total_MW": "50"}),
        ], prov)
    if logical == "boom":
        raise RuntimeError("server down")
    return LayerResult(logical, [], prov)


def _sources():
    return [{"layer": "county_boundary", "driver": "_fake", "id": "cb"},
            {"layer": "power_plants", "driver": "_fake", "id": "pp_eia", "entity": "power_plant", "provider": "eia"},
            {"layer": "power_plants", "driver": "_fake", "id": "pp_osm", "entity": "power_plant", "provider": "osm",
             "represent": "both", "fields": {"capacity_mw": {"from": ["plant:output:electrical@W"]}}},
            {"layer": "boom", "driver": "_fake", "id": "boom"}]


def test_end_to_end(tmp_path):
    aoi = parse_aoi("county:27025", county_name="Chisago")
    ctx = Context(aoi=aoi)
    m = run_build(ctx, _sources(), str(tmp_path), ["kmz", "geojson"], True, log=lambda *a: None)
    rows = {r["id"]: r for r in m["layers"]}
    assert ctx.boundary is not None and ctx.bbox == (-93.2, 45.3, -92.6, 45.8)
    assert rows["pp_eia"]["features"] == 1 and rows["pp_eia"]["dropped_outside_aoi"] == 1 and rows["pp_eia"]["deduped"] == 1
    assert rows["pp_osm"]["doc"] == "power_plants__osm"
    assert rows["boom"]["status"].startswith("ERROR: server down")
    files = set(os.listdir(tmp_path))
    assert {"ALL.kmz", "power_plants.kmz", "power_plants__osm.kmz", "manifest.json", "reconcile.md",
            "power_plants.geojson", "county_boundary.kmz"} <= files
    z = zipfile.ZipFile(tmp_path / "ALL.kmz")
    kml = z.read("doc.kml").decode()
    minidom.parseString(kml)
    assert "icons/power_plants.png" in z.namelist()
    assert "<name>Test Plant (120.5 MW)</name>" in kml
    assert "capacity_mw Δ 7%" in kml                       # reconcile stamped before writing
    man = json.load(open(tmp_path / "manifest.json"))
    assert man["aoi"]["fips5"] == "27025" and "power_plants__osm" in man["provenance"]
    rep = open(tmp_path / "reconcile.md").read()
    assert "| matched | 1 |" in rep and "120.5 MW" in rep and "130.0 MW" in rep


def test_no_clip_keeps_outside(tmp_path):
    ctx = Context(aoi=parse_aoi("county:27025", county_name="Chisago"), clip=False)
    m = run_build(ctx, _sources()[:2], str(tmp_path), ["kmz"], False, do_reconcile=False, log=lambda *a: None)
    assert {r["id"]: r for r in m["layers"]}["pp_eia"]["features"] == 2


def test_compare_pair_unmatched():
    prov = Provenance("A", "u", "l", "d", "x")
    a = LayerResult("x", [Feature({"type": "Point", "coordinates": [0, 0]}, {"name": "a", "capacity_mw": 10})], prov)
    b = LayerResult("x", [Feature({"type": "Point", "coordinates": [1, 1]}, {"name": "b", "capacity_mw": 10})],
                    Provenance("B", "u", "l", "d", "x"))
    r = compare_pair(a, b, 500)
    assert r["matched"] == [] and len(r["only_a"]) == 1 and len(r["only_b"]) == 1
    assert a.features[0].properties["xcheck"].startswith("unmatched in B")


def test_state_aoi_prefers_state_boundary(tmp_path):
    from overlaybuilder.build import BOUNDARY_LAYERS

    @driver("_fake_state")
    def _fs(logical, spec, ctx):
        prov = Provenance("F", "u", "l", "d", "x")
        if logical == "state_boundary":
            ring = [[-97.3, 43.4], [-89.4, 43.4], [-89.4, 49.4], [-97.3, 49.4], [-97.3, 43.4]]
            return LayerResult(logical, [Feature({"type": "Polygon", "coordinates": [ring]}, {"NAME": "Minnesota"})], prov)
        if logical == "county_boundary":
            raise AssertionError("county_boundary should not be needed to set the boundary first")
        return LayerResult(logical, [], prov)

    ctx = Context(aoi=parse_aoi("state:MN"))
    srcs = [{"layer": "county_boundary", "driver": "_fake_state"}, {"layer": "state_boundary", "driver": "_fake_state"}]
    m = run_build(ctx, srcs, str(tmp_path), ["kmz"], False, do_reconcile=False, log=lambda *a: None)
    assert m["layers"][0]["layer"] == "state_boundary"
    assert ctx.boundary is not None


def test_boundary_failure_refuses_unclipped_county_pack(tmp_path):
    @driver("_fake_boom_boundary")
    def _fb(logical, spec, ctx):
        if logical == "county_boundary":
            raise RuntimeError("tiger down")
        return LayerResult(logical, [], Provenance("F", "u", "l", "d", "x"))

    srcs = [{"layer": "county_boundary", "driver": "_fake_boom_boundary"},
            {"layer": "power_plants", "driver": "_fake_boom_boundary"}]
    ctx = Context(aoi=parse_aoi("county:27025", county_name="Chisago"))
    try:
        run_build(ctx, srcs, str(tmp_path), ["kmz"], False, do_reconcile=False, log=lambda *a: None)
    except RuntimeError as e:
        assert "--no-clip" in str(e)
    else:
        raise AssertionError("expected refusal")
    ctx2 = Context(aoi=parse_aoi("county:27025", county_name="Chisago"), clip=False)
    m = run_build(ctx2, srcs, str(tmp_path), ["kmz"], False, do_reconcile=False, log=lambda *a: None)
    assert m["layers"][0]["status"].startswith("ERROR") and m["layers"][1]["status"] == "ok"


def test_parallel_matches_serial_and_keeps_order(tmp_path):
    import threading
    seen = []

    @driver("_slow")
    def _slow(logical, spec, ctx):
        seen.append((threading.get_ident(), spec["id"]))
        time.sleep(0.05)
        prov = Provenance(f"src-{spec['id']}", "u", "l", "d", "_slow")
        if logical == "county_boundary":
            return LayerResult(logical, [Feature({"type": "Polygon", "coordinates": [RING]}, {"NAME": "C"})], prov)
        return LayerResult(logical, [Feature({"type": "Point", "coordinates": [-92.9, 45.5]},
                                             {"NAME": spec["id"]})], prov)

    srcs = [{"layer": "county_boundary", "driver": "_slow", "id": "cb"}] + [
        {"layer": "power_plants", "driver": "_slow", "id": f"s{i}", "provider": f"p{i}"} for i in range(6)]

    def run(jobs, out):
        seen.clear()
        ctx = Context(aoi=parse_aoi("county:27025", county_name="Chisago"))
        m = run_build(ctx, srcs, str(out), ["kmz"], False, do_reconcile=False, jobs=jobs, log=lambda *a: None)
        return m, len({t for t, _ in seen})

    serial, threads_used = run(1, tmp_path / "a")
    assert threads_used == 1
    par, threads_used = run(4, tmp_path / "b")
    assert threads_used > 1                                   # actually ran concurrently
    assert [r["id"] for r in serial["layers"]] == [r["id"] for r in par["layers"]]
    assert [r.get("doc") for r in serial["layers"]] == [r.get("doc") for r in par["layers"]]
    assert all(r["status"] == "ok" for r in par["layers"])


def test_parallel_boundary_runs_before_the_rest(tmp_path):
    order = []

    @driver("_order")
    def _order(logical, spec, ctx):
        order.append(logical)
        prov = Provenance("s", "u", "l", "d", "_order")
        if logical == "county_boundary":
            time.sleep(0.05)
            return LayerResult(logical, [Feature({"type": "Polygon", "coordinates": [RING]}, {})], prov)
        assert ctx.boundary is not None, "boundary must be set before other sources fetch"
        return LayerResult(logical, [], prov)

    srcs = [{"layer": "power_plants", "driver": "_order", "id": f"s{i}"} for i in range(4)]
    srcs.append({"layer": "county_boundary", "driver": "_order", "id": "cb"})
    ctx = Context(aoi=parse_aoi("county:27025", county_name="Chisago"))
    run_build(ctx, srcs, str(tmp_path), ["kmz"], False, do_reconcile=False, jobs=4, log=lambda *a: None)
    assert order[0] == "county_boundary"


def test_xcheck_accumulates_across_three_sources(tmp_path):
    from overlaybuilder.reconcile import reconcile_layers
    here = {"type": "Point", "coordinates": [-92.90, 45.50]}
    far = {"type": "Point", "coordinates": [-92.00, 45.50]}
    a = LayerResult("hospitals", [Feature(here, {"name": "A", "beds": 100})], Provenance("HIFLD", "u", "l", "d", "x"))
    b = LayerResult("hospitals", [Feature(here, {"name": "B", "beds": 100})], Provenance("OSM", "u", "l", "d", "x"))
    c = LayerResult("hospitals", [Feature(far, {"name": "C", "beds": 100})], Provenance("CMS", "u", "l", "d", "x"))
    for r, k in ((a, "hospitals"), (b, "hospitals__osm"), (c, "hospitals__cms")):
        r.doc_key = k
    specs = {"hospitals": {"entity": "hospital"}, "hospitals__osm": {"entity": "hospital"},
             "hospitals__cms": {"entity": "hospital"}}
    reconcile_layers([a, b, c], specs)
    note = a.features[0].properties["xcheck"]
    assert "agree" in note and "OSM" in note          # the match with OSM survives...
    assert "unmatched in CMS" in note                 # ...alongside the CMS miss


def test_geojson_reports_features_without_geometry(tmp_path):
    import json as _json

    @driver("_nogeom")
    def _ng(logical, spec, ctx):
        return LayerResult(logical, [Feature({"type": "Point", "coordinates": [-92.9, 45.5]}, {"N": 1}),
                                     Feature(None, {"N": 2}),
                                     Feature({"type": "Point", "coordinates": ()}, {"N": 3})],
                           Provenance("s", "u", "l", "d", "_nogeom"))

    ctx = Context(aoi=parse_aoi("state:MN"), clip=False)
    m = run_build(ctx, [{"layer": "gas_processing", "driver": "_nogeom", "id": "g"}], str(tmp_path),
                  ["kmz", "geojson"], False, do_reconcile=False, log=lambda *a: None)
    assert m["layers"][0]["dropped_without_geometry"] == 2
    fc = _json.load(open(tmp_path / "gas_processing.geojson"))
    assert len(fc["features"]) == 1 and fc["metadata"]["dropped_without_geometry"] == 2


def test_pack_carries_an_attribution_file(tmp_path):
    @driver("_lic")
    def _lic(logical, spec, ctx):
        lic = spec["_lic"]
        return LayerResult(logical, [Feature({"type": "Point", "coordinates": [-92.9, 45.5]}, {"NAME": "x"})],
                           Provenance(spec["_name"], "http://u", lic, "2026-09-12", "_lic"))

    srcs = [{"layer": "county_boundary", "driver": "_fake", "id": "cb"},
            {"layer": "power_plants", "driver": "_lic", "id": "eia", "provider": "eia",
             "_lic": "Public domain (US EIA)", "_name": "EIA U.S. Energy Atlas"},
            {"layer": "power_plants", "driver": "_lic", "id": "osm", "provider": "osm",
             "_lic": "ODbL 1.0 - (c) OpenStreetMap contributors", "_name": "OpenStreetMap"},
            {"layer": "boom", "driver": "_fake", "id": "boom"}]
    ctx = Context(aoi=parse_aoi("county:27025", county_name="Chisago"))
    run_build(ctx, srcs, str(tmp_path), ["kmz"], True, do_reconcile=False, log=lambda *a: None)
    text = (tmp_path / "ATTRIBUTION.txt").read_text()
    assert "Chisago County, MN" in text
    assert "Public domain (US EIA)" in text and "EIA U.S. Energy Atlas" in text
    assert "openstreetmap.org/copyright" in text and "Share-alike" in text
    assert "boom" in text and "did not build" in text          # failures are disclosed, not hidden
    assert "never inferred" in text


def test_demo_pack_is_unmistakably_synthetic():
    """A sample pack must never be confusable with real infrastructure."""
    import re
    import tempfile
    import zipfile

    from overlaybuilder.demo import PROV_NOTE, build_demo
    d = tempfile.mkdtemp()
    manifest = build_demo(d, log=lambda *a: None)
    names = []
    for f in os.listdir(d):
        if not f.endswith(".kmz"):
            continue
        kml = zipfile.ZipFile(os.path.join(d, f)).read("doc.kml").decode()
        names += [n for n in re.findall(r"<Placemark><name>([^<]*)</name>", kml) if n]
        assert "SYNTHETIC" in kml.upper() or "synthetic" in kml       # provenance on every document
    assert names
    assert not [n for n in names if "SAMPLE" not in n], "a placemark name lacks the SAMPLE marker"
    assert "SYNTHETIC" in PROV_NOTE.upper() and "SYNTHETIC" in manifest["warning"].upper()
    assert "SYNTHETIC" in open(os.path.join(d, "README.txt")).read().upper()


def test_demo_covers_every_sector_it_ships_styles_for():
    import re
    import tempfile
    import zipfile

    from overlaybuilder.demo import build_demo
    d = tempfile.mkdtemp()
    build_demo(d, log=lambda *a: None)
    kml = zipfile.ZipFile(os.path.join(d, "DEMO_SAMPLE_ALL.kmz")).read("doc.kml").decode()
    sectors = set(re.findall(r"<Folder><name>([A-Za-z &;-]+)</name><open>", kml))
    for want in ("Energy - Electric", "Water", "Communications", "Chemical &amp; Hazmat",
                 "Agriculture &amp; Food", "Mining"):
        assert want in sectors, f"{want} missing from the demo pack"
