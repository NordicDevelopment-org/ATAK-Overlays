import sqlite3
import struct

import pytest

from overlaybuilder.drivers._csv import read_delimited, dms_to_dd
from overlaybuilder.drivers._gpkg import parse_gpkg_geometry, read_gpkg


def test_csv_basic_and_filter():
    raw = b"NAME,LAT,LON,STATE\nA,45.5,-92.9,MN\nB,x,y,MN\nC,44.0,-90.0,WI\n"
    feats = read_delimited(raw, "LAT", "LON")
    assert [f.properties["NAME"] for f in feats] == ["A", "C"]
    feats = read_delimited(raw, "LAT", "LON", keep=lambda p: p["STATE"] == "MN")
    assert len(feats) == 1 and feats[0].geometry["coordinates"] == [-92.9, 45.5]


def test_csv_pipe_with_header_and_dms():
    raw = b"RA|123|45|30|0|N|92|54|0|W\n"
    hdr = ["REC", "REG", "LAT_D", "LAT_M", "LAT_S", "LAT_H", "LON_D", "LON_M", "LON_S", "LON_H"]
    feats = read_delimited(raw, "", "", delimiter="|", header=hdr,
                           dms={"lat": ["LAT_D", "LAT_M", "LAT_S", "LAT_H"], "lon": ["LON_D", "LON_M", "LON_S", "LON_H"]})
    assert len(feats) == 1
    lon, lat = feats[0].geometry["coordinates"]
    assert abs(lat - 45.5) < 1e-9 and abs(lon - (-92.9)) < 1e-9
    assert dms_to_dd("45", "30", "0", "S") == -45.5


def _wkb_point(x, y):
    return struct.pack("<BIdd", 1, 1, x, y)


def _gp(wkb: bytes) -> bytes:
    # GP header: magic, version, flags (little endian, no envelope), srs_id
    return b"GP" + bytes([0, 0b00000001]) + struct.pack("<i", 4326) + wkb


def test_parse_gpkg_geometry_point_and_polygon():
    assert parse_gpkg_geometry(_gp(_wkb_point(-92.9, 45.5))) == {"type": "Point", "coordinates": [-92.9, 45.5]}
    ring = [(0, 0), (1, 0), (1, 1), (0, 0)]
    wkb = struct.pack("<BII", 1, 3, 1) + struct.pack("<I", len(ring)) + b"".join(struct.pack("<dd", *p) for p in ring)
    g = parse_gpkg_geometry(_gp(wkb))
    assert g["type"] == "Polygon" and g["coordinates"][0][2] == [1.0, 1.0]
    assert parse_gpkg_geometry(b"") is None


def test_read_gpkg(tmp_path):
    p = tmp_path / "t.gpkg"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, geometry_type_name TEXT, srs_id INTEGER, z INTEGER, m INTEGER)")
    con.execute("INSERT INTO gpkg_geometry_columns VALUES ('dams','geom','POINT',4326,0,0)")
    con.execute("CREATE TABLE dams (fid INTEGER PRIMARY KEY, name TEXT, height REAL, geom BLOB)")
    con.execute("INSERT INTO dams VALUES (1,'Big Dam',120.0,?)", (_gp(_wkb_point(-93.0, 45.6)),))
    con.execute("INSERT INTO dams VALUES (2,'Small',10.0,?)", (_gp(_wkb_point(-93.1, 45.7)),))
    con.commit(); con.close()
    feats = read_gpkg(str(p))
    assert len(feats) == 2 and feats[0].properties["name"] == "Big Dam"
    assert feats[0].geometry == {"type": "Point", "coordinates": [-93.0, 45.6]}
    assert len(read_gpkg(str(p), "dams", keep=lambda pr: pr["height"] > 50)) == 1


def test_projected_prj_is_not_mistaken_for_geographic():
    from overlaybuilder.drivers._shp import _crs_from_prj
    utm = ('PROJCS["NAD_1983_UTM_Zone_15N",GEOGCS["GCS_North_American_1983",DATUM["D_North_American_1983",'
           'SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
           'PROJECTION["Transverse_Mercator"],UNIT["Meter",1.0]]')
    assert _crs_from_prj(utm) == utm                 # full WKT handed to pyproj, never guessed
    assert _crs_from_prj("") == 4269
    assert _crs_from_prj('GEOGCS["GCS_North_American_1983",DATUM["D_North_American_1983"]]') == 4269
    assert _crs_from_prj('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984"]]') == 4326
    assert _crs_from_prj('GEOGCS["x",AUTHORITY["EPSG","26915"]]') == 26915


def test_utm_shapefile_reprojects_into_minnesota(tmp_path):
    pytest_shapefile = pytest.importorskip("shapefile")
    import io
    import zipfile
    w = pytest_shapefile.Writer(str(tmp_path / "pts"), shapeType=pytest_shapefile.POINT)
    w.field("NAME", "C")
    w.point(500000, 5030000)          # UTM 15N metres, central Minnesota
    w.record("Sample")
    w.close()
    prj = ('PROJCS["NAD_1983_UTM_Zone_15N",GEOGCS["GCS_North_American_1983",DATUM["D_North_American_1983",'
           'SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
           'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
           'PARAMETER["False_Easting",500000.0],PARAMETER["Central_Meridian",-93.0],'
           'PARAMETER["Scale_Factor",0.9996],PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for ext in ("shp", "dbf", "shx"):
            z.write(str(tmp_path / f"pts.{ext}"), f"pts.{ext}")
        z.writestr("pts.prj", prj)
    from overlaybuilder.drivers._shp import read_zipped_shapefile
    feats = read_zipped_shapefile(buf.getvalue())
    lon, lat = feats[0].geometry["coordinates"]
    assert -94 < lon < -92 and 45 < lat < 46, (lon, lat)


def test_payload_sniffing():
    from overlaybuilder.drivers.file import _guess, sniff
    assert _guess("https://x/y.zip") == "shp" and _guess("https://x/api/download?f=geojson") is None
    assert sniff(b"PK\x03\x04rest") == "shp"
    assert sniff(b"SQLite format 3\x00") == "gpkg"
    assert sniff(b'\n  {"type":"FeatureCollection"}') == "geojson"
    assert sniff(b"<!DOCTYPE html><html>Page Not Found") == "html"
    assert sniff(b"NAME,LAT,LON\n") is None


def test_http_does_not_cache_html_error_pages():
    from overlaybuilder.http import _cacheable
    assert not _cacheable("https://x/data.zip", b"<!DOCTYPE html>...")
    assert _cacheable("https://x/data.zip", b"PK\x03\x04")
    assert _cacheable("https://x/api?f=json", b'{"a":1}')
    assert not _cacheable("https://x/data.zip", b"")
