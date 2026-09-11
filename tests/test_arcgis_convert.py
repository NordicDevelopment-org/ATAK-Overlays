from overlaybuilder.aoi import parse_aoi
from overlaybuilder.drivers import Context
from overlaybuilder.drivers.arcgis import _esri_to_geojson, _spatial_params, _where


def test_point():
    assert _esri_to_geojson({"x": -92.9, "y": 45.5}, "esriGeometryPoint") == \
        {"type": "Point", "coordinates": [-92.9, 45.5]}


def test_polyline_single_and_multi():
    assert _esri_to_geojson({"paths": [[[0, 0], [1, 1]]]}, "x")["type"] == "LineString"
    assert _esri_to_geojson({"paths": [[[0, 0]], [[2, 2]]]}, "x")["type"] == "MultiLineString"


def test_polygon_rings():
    g = _esri_to_geojson({"rings": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}, "x")
    assert g["type"] == "MultiPolygon"


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
