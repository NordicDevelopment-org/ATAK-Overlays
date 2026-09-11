import pytest

from overlaybuilder.aoi import (parse_aoi, point_in_geometry, geometry_touches, tile_bbox,
                                bbox_of_geometry, representative_point, STATE_BBOX)


def test_parse_county_with_name_is_offline():
    a = parse_aoi("county:27025", county_name="Chisago")
    assert a.kind == "county" and a.fips5 == "27025" and a.state_abbr == "MN"
    assert a.bbox == STATE_BBOX["MN"]
    assert a.slug == "us/mn/27025_chisago"
    v = a.template_vars()
    assert v["fips5"] == "27025" and v["state_abbr"] == "MN" and v["states_sql"] == "'MN'"


def test_parse_state_region_us_country_bbox():
    s = parse_aoi("state:mn")
    assert s.kind == "state" and s.state_abbrs == ["MN"] and s.slug == "us/mn"
    r = parse_aoi("region:upper-midwest")
    assert r.kind == "region" and set(r.state_abbrs) == {"MN", "WI", "IA"}
    assert r.bbox[0] <= STATE_BBOX["IA"][0] and r.bbox[3] >= STATE_BBOX["MN"][3]
    assert r.template_vars()["states_sql"] == "'MN','WI','IA'"
    u = parse_aoi("us")
    assert u.kind == "us" and len(u.state_abbrs) > 50
    c = parse_aoi("country:ca")
    assert c.kind == "country" and c.country == "CA" and c.bbox is None
    b = parse_aoi("bbox:-93.2,45.3,-92.6,45.8")
    assert b.kind == "bbox" and b.bbox == (-93.2, 45.3, -92.6, 45.8)
    assert parse_aoi("27025", county_name="Chisago").kind == "county"
    assert parse_aoi("WI").kind == "state"


def test_parse_errors():
    with pytest.raises(ValueError):
        parse_aoi("county:123")
    with pytest.raises(ValueError):
        parse_aoi("bbox:1,2,3")
    with pytest.raises(KeyError):
        parse_aoi("region:nope")
    with pytest.raises(ValueError):
        parse_aoi("galaxy:milky-way")


SQ = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                                          [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}  # with hole


def test_point_in_polygon_with_hole():
    assert point_in_geometry(1, 1, SQ)
    assert not point_in_geometry(5, 5, SQ)     # inside the hole
    assert not point_in_geometry(11, 5, SQ)
    mp = {"type": "MultiPolygon", "coordinates": [SQ["coordinates"]]}
    assert point_in_geometry(9, 9, mp)


def test_geometry_touches_and_bbox():
    line_in = {"type": "LineString", "coordinates": [[-5, 5], [2, 2]]}
    line_out = {"type": "LineString", "coordinates": [[-5, -5], [-1, -1]]}
    assert geometry_touches(line_in, SQ, (0, 0, 10, 10))
    assert not geometry_touches(line_out, SQ, (0, 0, 10, 10))
    assert bbox_of_geometry(line_in) == (-5, 2, 2, 5)
    assert representative_point({"type": "Point", "coordinates": [1, 2]}) == [1, 2]
    rp = representative_point(SQ)
    assert 0 < rp[0] < 10


def test_tile_bbox():
    assert tile_bbox((0, 0, 1, 1), 1.0) == [(0, 0, 1, 1)]
    tiles = tile_bbox((0, 0, 2.5, 1.5), 1.0)
    assert len(tiles) == 6
    assert tiles[-1] == (2.0, 1.0, 2.5, 1.5)
