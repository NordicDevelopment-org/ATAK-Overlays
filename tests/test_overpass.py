import json

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
