"""Write a LayerResult as a FeatureCollection (EPSG:4326). Provenance rides in
the collection-level "metadata" so the source is never lost.

Features whose geometry is absent or degenerate are skipped (a GeoJSON Feature
needs a geometry), and the count of skipped features is returned so the caller
can report the loss instead of silently shipping a shorter file.
"""
import json
from dataclasses import asdict
from typing import Tuple

from ..convert.kmz import _xy
from ..model import LayerResult


def _usable(geom) -> bool:
    """A geometry with at least one real coordinate pair."""
    if not geom:
        return False
    if geom.get("type") == "GeometryCollection":
        return any(_usable(g) for g in geom.get("geometries") or ())
    c = geom.get("coordinates")
    if not c:
        return False

    def walk(x):
        if x and isinstance(x[0], (int, float, str)):
            return _xy(x) is not None
        return any(walk(y) for y in x if y)
    try:
        return walk(c)
    except (TypeError, IndexError):
        return False


def write_geojson(path: str, result: LayerResult) -> Tuple[int, int]:
    """Returns (written, dropped_without_geometry)."""
    feats = [{"type": "Feature", "geometry": f.geometry, "properties": f.properties}
             for f in result.features if _usable(f.geometry)]
    fc = {
        "type": "FeatureCollection",
        "metadata": {"logical": result.logical,
                     "provenance": asdict(result.provenance),
                     "server_count": result.server_count,
                     "dropped_without_geometry": len(result.features) - len(feats)},
        "features": feats,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(fc, fh)
    return len(feats), len(result.features) - len(feats)
