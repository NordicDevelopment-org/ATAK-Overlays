"""Generic file driver: download (or open) a static dataset and load it.

Formats (spec `format`, default auto by extension):
  shp      zipped shapefile -> reprojected to 4326 (CRS from .prj)
  geojson  FeatureCollection, assumed EPSG:4326
  gpkg     GeoPackage (any table; `table:` to pick), reprojected from its SRS
  csv      delimited text with lat/lon columns (`lat_field`, `lon_field`,
           `delimiter`, `encoding`, `skip_lines`, `header: [...]`, `dms: {...}`)
  A .zip whose member isn't a shapefile: set `zip_member: "<regex>"` to pick
  the file inside and `format:` to say what it is (e.g. FCC ASR RA.dat).

Scoping: `filter: {field: STATE, in: ["{state_abbr}"]}` (templated; `in` may
list several) is applied while reading; the orchestrator then clips to the AOI
bbox/boundary. `url` may be templated too (e.g. per-state downloads).
"""
import io
import json
import os
import re
import tempfile
import zipfile

from ..model import Feature, LayerResult, Provenance
from ._csv import read_delimited
from ._gpkg import read_gpkg
from ._shp import read_zipped_shapefile
from .base import Context, driver, http_get, today


def _guess(url: str) -> str:
    u = url.lower().split("?")[0]
    for ext, fmt in ((".zip", "shp"), (".geojson", "geojson"), (".json", "geojson"),
                     (".gpkg", "gpkg"), (".csv", "csv"), (".txt", "csv"), (".dat", "csv")):
        if u.endswith(ext):
            return fmt
    return "shp"


def _make_keep(spec: dict, ctx: Context):
    f = spec.get("filter")
    if not f:
        return None
    field = f["field"]
    vals = f.get("in") or ([f["equals"]] if "equals" in f else [])
    vals = {str(ctx.render(v)).upper() for v in vals}
    vals.discard("")
    if not vals:
        return None

    def keep(props):
        for k in props:
            if k.upper() == field.upper():
                return str(props[k]).strip().upper() in vals
        return False
    return keep


def _load_bytes(url: str) -> bytes:
    if url.startswith(("http://", "https://")):
        return http_get(url, timeout=600)
    with open(url, "rb") as fh:
        return fh.read()


@driver("file")
def fetch(logical: str, spec: dict, ctx: Context) -> LayerResult:
    url = ctx.render(spec["url"])
    fmt = spec.get("format", "auto")
    if fmt == "auto":
        fmt = _guess(url)
    raw = _load_bytes(url)
    keep = _make_keep(spec, ctx)

    if spec.get("zip_member"):
        zf = zipfile.ZipFile(io.BytesIO(raw))
        rx = re.compile(spec["zip_member"], re.I)
        names = [n for n in zf.namelist() if rx.search(n)]
        if not names:
            raise RuntimeError(f"no zip member matched /{spec['zip_member']}/ in {zf.namelist()[:20]}")
        raw = zf.read(names[0])

    if fmt == "shp":
        feats = read_zipped_shapefile(raw, keep)
    elif fmt == "geojson":
        fc = json.loads(raw)
        feats = []
        for f in fc.get("features", []):
            props = f.get("properties", {}) or {}
            if keep and not keep(props):
                continue
            feats.append(Feature(f.get("geometry"), props))
    elif fmt == "gpkg":
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
            tmp.write(raw)
            path = tmp.name
        try:
            feats = read_gpkg(path, spec.get("table"), keep)
        finally:
            os.unlink(path)
    elif fmt == "csv":
        feats = read_delimited(raw, spec.get("lat_field", "latitude"), spec.get("lon_field", "longitude"),
                               delimiter=spec.get("delimiter", ","), encoding=spec.get("encoding", "utf-8"),
                               skip_lines=int(spec.get("skip_lines", 0)), header=spec.get("header"),
                               keep=keep, dms=spec.get("dms"))
    else:
        raise RuntimeError(f"unsupported file format '{fmt}'")

    prov = Provenance(
        source_name=spec.get("source_name", "file source"),
        source_url=spec.get("source_url", url),
        license=spec.get("license", "see source terms"),
        retrieved=today(), driver="file", notes=spec.get("notes", ""))
    return LayerResult(logical, feats, prov, spec.get("group_by"), len(feats))
