import json
import re

import pytest

from overlaybuilder.drivers.overpass import (_parse_selector, build_query, element_to_geometry,
                                             _numeric_ok, _osm_number, _stitch)


def test_selector_parsing():
    ql, num = _parse_selector("power=plant;plant:source=nuclear")
    assert ql == '["power"="plant"]["plant:source"="nuclear"]' and num == []
    ql, num = _parse_selector("man_made~^(mast|tower)$")
    assert ql == '["man_made"~"^(mast|tower)$"]'
    ql, num = _parse_selector("power=line;voltage>=100000")
    assert ql == '["power"="line"]["voltage"]' and num == [("voltage", ">=", "100000")]
    ql, num = _parse_selector("telecom")
    assert ql == '["telecom"]'
    ql, _ = _parse_selector("service!=yard")
    assert ql == '["service"!="yard"]'


def test_build_query_union_and_scope():
    q, num = build_query(["power=plant", "power=generator"], "nwr", "(45.3,-93.2,45.8,-92.6)", 90)
    assert q.startswith("[out:json][timeout:90];(")
    assert 'nwr["power"="plant"](45.3,-93.2,45.8,-92.6);' in q
    assert q.endswith("out geom qt;")


def test_numeric_filters():
    assert _osm_number("115000;34500") == 115000
    assert _osm_number("345 kV") == 345000
    assert _numeric_ok({"voltage": "230000"}, [("voltage", ">=", "100000")])
    assert not _numeric_ok({"voltage": "69000"}, [("voltage", ">=", "100000")])
    assert not _numeric_ok({}, [("voltage", ">=", "1")])


def test_node_way_geometry():
    n = {"type": "node", "id": 1, "lat": 45.5, "lon": -92.9, "tags": {"power": "tower"}}
    assert element_to_geometry(n) == {"type": "Point", "coordinates": [-92.9, 45.5]}
    ring = [{"lon": 0, "lat": 0}, {"lon": 1, "lat": 0}, {"lon": 1, "lat": 1}, {"lon": 0, "lat": 0}]
    plant = {"type": "way", "id": 2, "tags": {"power": "plant"}, "geometry": ring}
    assert element_to_geometry(plant)["type"] == "Polygon"
    line = {"type": "way", "id": 3, "tags": {"power": "line"}, "geometry": ring}
    assert element_to_geometry(line)["type"] == "LineString"     # closed but not an area tag
    open_way = {"type": "way", "id": 4, "tags": {"power": "line"}, "geometry": ring[:3]}
    assert element_to_geometry(open_way)["type"] == "LineString"
    assert element_to_geometry(plant, mode="line")["type"] == "LineString"
    assert element_to_geometry(line, mode="polygon")["type"] == "Polygon"


def test_relation_multipolygon_stitch():
    outer1 = [{"lon": 0, "lat": 0}, {"lon": 2, "lat": 0}, {"lon": 2, "lat": 2}]
    outer2 = [{"lon": 2, "lat": 2}, {"lon": 0, "lat": 2}, {"lon": 0, "lat": 0}]
    inner = [{"lon": .5, "lat": .5}, {"lon": 1, "lat": .5}, {"lon": 1, "lat": 1}, {"lon": .5, "lat": .5}]
    rel = {"type": "relation", "id": 9, "tags": {"type": "multipolygon", "power": "plant"},
           "members": [{"type": "way", "role": "outer", "geometry": outer1},
                       {"type": "way", "role": "outer", "geometry": outer2},
                       {"type": "way", "role": "inner", "geometry": inner}]}
    g = element_to_geometry(rel)
    assert g["type"] == "Polygon" and len(g["coordinates"]) == 2
    assert g["coordinates"][0][0] == g["coordinates"][0][-1]
    rings = _stitch([[[0, 0], [1, 0]], [[1, 1], [0, 1], [0, 0]], [[1, 0], [1, 1]]])
    assert len(rings) == 1 and len(rings[0]) == 5


def test_relation_center_fallback():
    rel = {"type": "relation", "id": 10, "tags": {"power": "plant"}, "members": [], "center": {"lat": 1, "lon": 2}}
    assert element_to_geometry(rel) == {"type": "Point", "coordinates": [2, 1]}


def test_tile_guard_refuses_huge_aoi():
    from overlaybuilder.aoi import parse_aoi
    from overlaybuilder.drivers import Context
    from overlaybuilder.drivers.overpass import fetch_elements
    ctx = Context(aoi=parse_aoi("us"))
    try:
        fetch_elements(["power=plant"], "nwr", ctx, {"tile_deg": 1.0})
    except RuntimeError as e:
        assert "max_tiles" in str(e) and "osm_pbf" in str(e)
    else:
        raise AssertionError("expected a tile-count guard error")


def test_world_refused():
    from overlaybuilder.aoi import parse_aoi
    from overlaybuilder.drivers import Context
    from overlaybuilder.drivers.overpass import fetch_elements
    ctx = Context(aoi=parse_aoi("world"))
    try:
        fetch_elements(["power=plant"], "nwr", ctx, {})
    except RuntimeError as e:
        assert "osm_pbf" in str(e)
    else:
        raise AssertionError("expected refusal")


def test_metre_suffix_is_not_mega():
    assert _osm_number("30 m") == 30 and _osm_number("1.5 MW") == 1.5e6


def test_unstitchable_multipolygon_falls_back_to_centroid():
    rel = {"type": "relation", "id": 11, "tags": {"type": "multipolygon", "power": "plant"},
           "members": [{"type": "way", "role": "outer", "geometry": [{"lon": 0, "lat": 0}, {"lon": 2, "lat": 0}]}]}
    g = element_to_geometry(rel)
    assert g["type"] == "Point" and g["coordinates"] == [1.0, 0.0]


def test_country_query_has_area_prelude_and_bbox():
    from overlaybuilder.drivers.overpass import _country_prelude
    q, _ = build_query(["power=plant"], "nwr", "(area.a)(45.0,-93.0,46.0,-92.0)", 90, "geom", _country_prelude("CA"))
    assert q.startswith('[out:json][timeout:90];area["ISO3166-1"="CA"]["admin_level"="2"]->.a;(')
    assert 'nwr["power"="plant"](area.a)(45.0,-93.0,46.0,-92.0);' in q


def test_element_types_are_normalized_and_validated():
    from overlaybuilder.drivers.overpass import normalize_elements
    assert normalize_elements("n") == "node" and normalize_elements("w") == "way"
    assert normalize_elements("r") == "rel" and normalize_elements("relation") == "rel"
    for keep in ("nwr", "nw", "wr", "nr"):
        assert normalize_elements(keep) == keep
    assert normalize_elements(None) == "nwr"
    with pytest.raises(RuntimeError, match="not an Overpass element type"):
        normalize_elements("q")
    q, _ = build_query(["power=line"], "w", "(1,2,3,4)", 90)
    assert q.startswith('[out:json][timeout:90];(way["power"="line"]')   # not `w[...]`, which is invalid


def test_shipped_catalog_queries_are_valid_overpass():
    import glob

    import yaml

    from overlaybuilder.drivers.overpass import normalize_elements
    checked = 0
    for path in glob.glob("catalog/**/*.yaml", recursive=True):
        doc = yaml.safe_load(open(path)) or {}
        defaults = doc.get("defaults") or {}
        for src in doc.get("sources") or []:
            spec = dict(defaults)
            spec.update(src)
            if spec.get("driver") != "overpass":
                continue
            q, _ = build_query(spec.get("tags") or [spec.get("tag", "")],
                               normalize_elements(spec.get("elements", "nwr")), "(1,2,3,4)", 60)
            assert q.startswith("[out:json]") and q.endswith(";")
            assert not re.search(r"\((?:n|w|r)\[", q), f"{path}: invalid element type in {q[:80]}"
            checked += 1
    assert checked > 30
