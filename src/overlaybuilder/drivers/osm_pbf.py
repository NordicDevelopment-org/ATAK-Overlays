"""OpenStreetMap from a .osm.pbf extract (offline, nation/continent/planet scale).

Overpass is fine for a county or a state; for a whole country or the planet,
download a Geofabrik extract once and filter it locally:

    - layer: power_plants
      driver: osm_pbf
      path: ./data/minnesota-latest.osm.pbf      # or url: https://download.geofabrik.de/north-america/us/minnesota-latest.osm.pbf
      tags: ["power=plant"]                       # same selector syntax as overpass
      geometry: auto

Requires the optional dependency `osmium` (pip install osmium). Same output
shape as the overpass driver, so catalog entries can switch drivers freely.
"""
import os
import re
from typing import List

from ..aoi import representative_point
from ..model import Feature, LayerResult, Provenance
from .base import Context, driver, http_get, today
from .overpass import _numeric_ok, _parse_selector

try:
    import osmium  # type: ignore
except ImportError:  # pragma: no cover
    osmium = None


def _selector_matchers(selectors: List[str]):
    """Return list of (list of (key, kind, value), numeric_filters)."""
    out = []
    for sel in selectors:
        conds = []
        _, numeric = _parse_selector(sel)
        for p in [x.strip() for x in sel.split(";") if x.strip()]:
            if re.match(r"^[A-Za-z0-9_:\-]+\s*(>=|<=|>|<|!=)\s*-?\d", p):
                continue
            if "~" in p and "=" not in p.split("~", 1)[0]:
                k, rx = p.split("~", 1)
                conds.append((k.strip(), "re", re.compile(rx.strip())))
            elif "!=" in p:
                k, v = p.split("!=", 1)
                conds.append((k.strip(), "ne", v.strip()))
            elif "=" in p:
                k, v = p.split("=", 1)
                conds.append((k.strip(), "eq", v.strip()))
            else:
                conds.append((p, "has", None))
        out.append((conds, numeric))
    return out


def tags_match(tags: dict, matchers) -> bool:
    for conds, numeric in matchers:
        ok = True
        for k, kind, v in conds:
            t = tags.get(k)
            if kind == "has" and t is None: ok = False
            elif kind == "eq" and t != v: ok = False
            elif kind == "ne" and t == v: ok = False
            elif kind == "re" and (t is None or not v.search(t)): ok = False
            if not ok:
                break
        if ok and numeric and not _numeric_ok(tags, numeric):
            ok = False
        if ok:
            return True
    return False


@driver("osm_pbf")
def fetch(logical: str, spec: dict, ctx: Context) -> LayerResult:
    if osmium is None:
        raise RuntimeError("osm_pbf driver needs `pip install osmium`")
    path = spec.get("path")
    if not path and spec.get("url"):
        url = ctx.render(spec["url"])
        os.makedirs(os.path.join(ctx.cache_dir, "pbf"), exist_ok=True)
        path = os.path.join(ctx.cache_dir, "pbf", url.rsplit("/", 1)[-1])
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(http_get(url, timeout=3600, cache=False))
    if not path or not os.path.exists(path):
        raise RuntimeError(f"{logical}: osm_pbf needs `path:` or `url:` to an .osm.pbf")
    selectors = spec.get("tags") or []
    matchers = _selector_matchers(selectors)
    mode = spec.get("geometry", "auto")
    represent = spec.get("represent", "shape")
    bbox = ctx.bbox
    from .overpass import _is_area

    feats: List[Feature] = []
    fab = osmium.geom.GeoJSONFactory()

    class H(osmium.SimpleHandler):
        def _emit(self, otype, oid, tags, geom):
            if geom is None:
                return
            if bbox:
                from ..aoi import bbox_of_geometry
                gb = bbox_of_geometry(geom)
                if gb and (gb[2] < bbox[0] or gb[0] > bbox[2] or gb[3] < bbox[1] or gb[1] > bbox[3]):
                    return
            props = dict(tags)
            props.update({"osm_type": otype, "osm_id": oid,
                          "osm_url": f"https://www.openstreetmap.org/{otype}/{oid}"})
            if represent in ("point", "both") and geom["type"] != "Point":
                rp = representative_point(geom)
                if represent == "point":
                    geom = {"type": "Point", "coordinates": rp}
                else:
                    feats.append(Feature({"type": "Point", "coordinates": rp}, dict(props, _shape="marker")))
            feats.append(Feature(geom, props))

        def node(self, n):
            tags = dict(n.tags)
            if tags and tags_match(tags, matchers) and n.location.valid():
                self._emit("node", n.id, tags, {"type": "Point", "coordinates": [n.location.lon, n.location.lat]})

        def way(self, w):
            tags = dict(w.tags)
            if not tags or not tags_match(tags, matchers):
                return
            try:
                coords = [[nd.lon, nd.lat] for nd in w.nodes if nd.location.valid()]
            except Exception:
                return
            if len(coords) < 2:
                return
            closed = coords[0] == coords[-1] and len(coords) >= 4
            if _is_area(tags, closed, mode):
                self._emit("way", w.id, tags, {"type": "Polygon", "coordinates": [coords]})
            else:
                self._emit("way", w.id, tags, {"type": "LineString", "coordinates": coords})

        def area(self, a):
            if not a.from_way():
                tags = dict(a.tags)
                if tags and tags_match(tags, matchers):
                    import json
                    try:
                        g = json.loads(fab.create_multipolygon(a))
                    except Exception:
                        return
                    self._emit("relation", a.orig_id(), tags, g)

    H().apply_file(path, locations=True)
    prov = Provenance(
        source_name="OpenStreetMap (.osm.pbf extract)",
        source_url=spec.get("url") or path,
        license="ODbL 1.0 - (c) OpenStreetMap contributors (attribution + share-alike)",
        retrieved=today(), driver="osm_pbf", notes=spec.get("notes", ""))
    return LayerResult(logical, feats, prov, spec.get("group_by"), len(feats))
