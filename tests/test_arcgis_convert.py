from overlaybuilder.aoi import parse_aoi
from overlaybuilder.drivers import Context
from overlaybuilder.drivers.arcgis import _esri_to_geojson, _spatial_params, _where


def test_point():
    assert _esri_to_geojson({"x": -92.9, "y": 45.5}, "esriGeometryPoint") == \
        {"type": "Point", "coordinates": [-92.9, 45.5]}


def test_polyline_single_and_multi():
    assert _esri_to_geojson({"paths": [[[0, 0], [1, 1]]]}, "x")["type"] == "LineString"
    assert _esri_to_geojson({"paths": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]}, "x")["type"] == "MultiLineString"
    # a one-vertex "path" is not a line: dropped, not emitted as a degenerate geometry
    assert _esri_to_geojson({"paths": [[[0, 0]], [[2, 2]]]}, "x") is None
    assert _esri_to_geojson({"paths": [[[0, 0]], [[2, 2], [3, 3]]]}, "x")["type"] == "LineString"


def test_polygon_rings():
    g = _esri_to_geojson({"rings": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}, "x")
    assert g["type"] == "Polygon"                      # one ring is one polygon, not a MultiPolygon
    assert g["coordinates"][0][0] == g["coordinates"][0][-1]


def test_none():
    assert _esri_to_geojson(None, "x") is None


def test_where_templating_by_aoi_kind():
    spec = {"where_by_aoi": {"county": "COUNTYFIPS = '{fips5}'", "state": "STATE = '{state_abbr}'",
                             "region": "STATE IN ({states_sql})"}}
    c = Context(aoi=parse_aoi("county:27025", county_name="Chisago"))
    assert _where(spec, c) == "COUNTYFIPS = '27025'"
    s = Context(aoi=parse_aoi("state:WI"))
    assert _where(spec, s) == "STATE = 'WI'"
    r = Context(aoi=parse_aoi("region:upper-midwest"))
    assert _where(spec, r) == "STATE IN ('MN','WI','IA')"
    u = Context(aoi=parse_aoi("us"))
    assert _where(spec, u) == "1=1"
    # county falls back to the state filter when no county clause exists
    assert _where({"where_by_aoi": {"state": "STATE = '{state_abbr}'"}}, c) == "STATE = 'MN'"
    assert _where({"where": "TYPE = 'X'"}, c) == "TYPE = 'X'"


def test_spatial_envelope_params():
    c = Context(aoi=parse_aoi("bbox:-93.2,45.3,-92.6,45.8"))
    p = _spatial_params({}, c)
    assert p["geometryType"] == "esriGeometryEnvelope" and p["geometry"].startswith("-93.200000,45.300000")
    assert _spatial_params({"spatial": "none"}, c) == {}
    assert _spatial_params({}, Context(aoi=parse_aoi("world"))) == {}


def test_null_and_nonfinite_geometry_becomes_none():
    from overlaybuilder.drivers.arcgis import _esri_to_geojson as c
    assert c({"x": "NaN", "y": "NaN"}, "esriGeometryPoint") is None
    assert c({"x": None, "y": None}, "esriGeometryPoint") is None
    assert c({"x": float("inf"), "y": 1}, "esriGeometryPoint") is None
    assert c({}, "x") is None and c({"rings": []}, "x") is None and c({"paths": []}, "x") is None
    assert c({"x": "-92.9", "y": "45.5"}, "esriGeometryPoint") == {"type": "Point", "coordinates": [-92.9, 45.5]}


def test_polygon_holes_are_preserved_not_filled():
    from overlaybuilder.drivers.arcgis import _esri_to_geojson as c
    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]          # clockwise in Esri terms
    hole = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]               # counter-clockwise
    g = c({"rings": [outer, hole]}, "esriGeometryPolygon")
    assert g["type"] == "Polygon" and len(g["coordinates"]) == 2   # one polygon, one hole
    assert g["coordinates"][1] == hole
    # two separate outer rings stay two polygons
    outer2 = [[20, 0], [20, 5], [25, 5], [25, 0], [20, 0]]
    g2 = c({"rings": [outer, outer2]}, "esriGeometryPolygon")
    assert g2["type"] == "MultiPolygon" and len(g2["coordinates"]) == 2
    assert all(len(p) == 1 for p in g2["coordinates"])


def test_polygon_hole_reaches_the_kml_as_an_inner_boundary():
    from overlaybuilder.convert.kmz import _geom
    from overlaybuilder.drivers.arcgis import _esri_to_geojson as c
    g = c({"rings": [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]],
                     [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]]}, "esriGeometryPolygon")
    kml = _geom(g, 2)
    assert kml.count("<outerBoundaryIs>") == 1 and kml.count("<innerBoundaryIs>") == 1
