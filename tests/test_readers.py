import sqlite3
import struct

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
