"""Read a GeoPackage (.gpkg) with only the standard library.

GeoPackage = SQLite + a `gpkg_geometry_columns` table + geometries stored as
"GP" header + WKB. Parsing WKB for Point/LineString/Polygon/Multi* is ~80 lines,
so the project stays GDAL-free while still consuming the many gpkg downloads
that state portals and USACE publish.
"""
import sqlite3
import struct
from typing import Callable, List, Optional

from ..model import Feature

try:
    from pyproj import Transformer
except ImportError:  # pragma: no cover
    Transformer = None


def _parse_wkb(buf: bytes, off: int = 0):
    bo = "<" if buf[off] == 1 else ">"
    off += 1
    (t,) = struct.unpack_from(bo + "I", buf, off)
    off += 4
    t &= 0xFFFF
    base = t % 1000
    dims = 2 + (1 if t >= 1000 else 0) + (1 if t >= 2000 and t < 3000 or t >= 3000 else 0)
    if t >= 3000:
        dims = 4
    elif t >= 1000:
        dims = 3
    fmt = bo + "d" * dims

    def pt():
        nonlocal off
        v = struct.unpack_from(fmt, buf, off)
        off += 8 * dims
        return [v[0], v[1]]

    def ring():
        nonlocal off
        (n,) = struct.unpack_from(bo + "I", buf, off)
        off += 4
        return [pt() for _ in range(n)]

    if base == 1:
        return {"type": "Point", "coordinates": pt()}, off
    if base == 2:
        return {"type": "LineString", "coordinates": ring()}, off
    if base == 3:
        (n,) = struct.unpack_from(bo + "I", buf, off)
        off += 4
        return {"type": "Polygon", "coordinates": [ring() for _ in range(n)]}, off
    if base in (4, 5, 6, 7):
        (n,) = struct.unpack_from(bo + "I", buf, off)
        off += 4
        parts = []
        for _ in range(n):
            g, off = _parse_wkb(buf, off)
            parts.append(g)
        if base == 4:
            return {"type": "MultiPoint", "coordinates": [p["coordinates"] for p in parts]}, off
        if base == 5:
            return {"type": "MultiLineString", "coordinates": [p["coordinates"] for p in parts]}, off
        if base == 6:
            return {"type": "MultiPolygon", "coordinates": [p["coordinates"] for p in parts]}, off
        return {"type": "GeometryCollection", "geometries": parts}, off
    raise ValueError(f"unsupported WKB type {t}")


def parse_gpkg_geometry(blob: bytes):
    if not blob or blob[:2] != b"GP":
        return None
    flags = blob[3]
    env_type = (flags >> 1) & 0x07
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env_type, 0)
    if flags & 0x10:  # empty geometry
        return None
    geom, _ = _parse_wkb(blob, 8 + env_len)
    return geom


def _reproject(geom, tf):
    def rec(c):
        if c and isinstance(c[0], (int, float)):
            x, y = tf.transform(c[0], c[1])
            return [x, y]
        return [rec(x) for x in c]
    if geom is None:
        return None
    if geom["type"] == "GeometryCollection":
        return {"type": "GeometryCollection", "geometries": [_reproject(g, tf) for g in geom["geometries"]]}
    return {"type": geom["type"], "coordinates": rec(geom["coordinates"])}


def read_gpkg(path: str, table: Optional[str] = None,
              keep: Optional[Callable[[dict], bool]] = None) -> List[Feature]:
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT table_name, column_name, srs_id FROM gpkg_geometry_columns").fetchall()
        if not rows:
            raise RuntimeError("gpkg has no geometry tables")
        if table:
            hit = [r for r in rows if r[0] == table]
            if not hit:
                raise RuntimeError(f"gpkg table '{table}' not found; tables: {[r[0] for r in rows]}")
            tname, gcol, srs = hit[0]
        else:
            tname, gcol, srs = rows[0]
        tf = None
        if srs not in (4326, 0, -1) and Transformer is not None:
            tf = Transformer.from_crs(int(srs), 4326, always_xy=True)
        cur = con.execute(f'SELECT * FROM "{tname}"')
        cols = [d[0] for d in cur.description]
        gi = cols.index(gcol)
        out: List[Feature] = []
        for row in cur:
            props = {c: v for i, (c, v) in enumerate(zip(cols, row)) if i != gi}
            if keep and not keep(props):
                continue
            geom = parse_gpkg_geometry(row[gi])
            if tf is not None:
                geom = _reproject(geom, tf)
            out.append(Feature(geom, props))
        return out
    finally:
        con.close()
