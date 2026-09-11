import json
import os
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
