"""Read a zipped shapefile into EPSG:4326 GeoJSON features.

Uses pyshp (pure-python) to parse and pyproj to reproject. Keeps the project
GDAL-free. Source CRS is read from the .prj when possible, else defaults to
EPSG:4269 (NAD83), which is what Census TIGER ships.
"""
import io
import zipfile
from typing import Callable, List, Optional

from ..model import Feature

try:
    import shapefile  # pyshp
except ImportError as e:  # pragma: no cover
    shapefile = None
    _IMPORT_ERR = e

try:
    from pyproj import Transformer
except ImportError:  # pragma: no cover
    Transformer = None


def _require():
    if shapefile is None:
        raise RuntimeError("pyshp is required for shapefile sources: pip install pyshp")
    if Transformer is None:
        raise RuntimeError("pyproj is required for reprojection: pip install pyproj")


def _epsg_from_prj(prj_text: str) -> int:
    t = (prj_text or "").upper()
    if "4326" in t or ("WGS" in t and "84" in t):
        return 4326
    # TIGER and most US county data are NAD83 geographic.
    return 4269


def _reproject_coords(coords, tf):
    if coords and isinstance(coords[0], (int, float)):
        x, y = tf.transform(coords[0], coords[1])
        return [x, y]
    return [_reproject_coords(c, tf) for c in coords]


def read_zipped_shapefile(zip_bytes: bytes,
                          keep: Optional[Callable[[dict], bool]] = None) -> List[Feature]:
    _require()
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = zf.namelist()
    def find(ext):
        for n in names:
            if n.lower().endswith(ext):
                return n
        return None
    shp, dbf, shx, prj = find(".shp"), find(".dbf"), find(".shx"), find(".prj")
    if not (shp and dbf):
        raise RuntimeError("zip does not contain a .shp + .dbf")
    prj_text = zf.read(prj).decode("utf-8", "replace") if prj else ""
    src_epsg = _epsg_from_prj(prj_text)
    tf = (Transformer.from_crs(src_epsg, 4326, always_xy=True)
          if src_epsg != 4326 else None)

    reader = shapefile.Reader(
        shp=io.BytesIO(zf.read(shp)),
        dbf=io.BytesIO(zf.read(dbf)),
        shx=io.BytesIO(zf.read(shx)) if shx else None,
    )
    fields = [f[0] for f in reader.fields[1:]]  # drop DeletionFlag
    out: List[Feature] = []
    for sr in reader.iterShapeRecords():
        props = dict(zip(fields, list(sr.record)))
        if keep and not keep(props):
            continue
        geom = sr.shape.__geo_interface__
        if tf is not None and geom.get("coordinates") is not None:
            geom = {"type": geom["type"],
                    "coordinates": _reproject_coords(geom["coordinates"], tf)}
        out.append(Feature(geom, props))
    return out
