"""Write a LayerResult as a FeatureCollection (EPSG:4326). Provenance rides in
the collection-level "metadata" so the source is never lost."""
import json
from dataclasses import asdict

from ..model import LayerResult


def write_geojson(path: str, result: LayerResult) -> int:
    fc = {
        "type": "FeatureCollection",
        "metadata": {"logical": result.logical,
                     "provenance": asdict(result.provenance),
                     "server_count": result.server_count},
        "features": [
            {"type": "Feature", "geometry": f.geometry, "properties": f.properties}
            for f in result.features if f.geometry
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(fc, fh)
    return len(fc["features"])
