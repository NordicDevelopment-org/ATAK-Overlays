"""Generic OpenStreetMap driver via the Overpass API (the WORLD tier).

Any OSM feature class, anywhere on Earth, from one YAML block:

    - layer: power_plants
      driver: overpass
      tags: ["power=plant"]                 # OR-ed selectors; see below
      elements: nwr                          # nodes+ways+relations (default)
      geometry: auto                         # auto | point | line | polygon
      represent: both                        # both | point | shape  (polygons -> also a point placemark)
      tile_deg: 1.0                          # split big AOIs into tiles this size
      endpoints: [https://overpass-api.de/api/interpreter, https://overpass.kumi.systems/api/interpreter]

Selector syntax (each item is AND-ed within itself, items are OR-ed):
    "power=plant"                          key=value
    "power=plant;plant:source=nuclear"     two tags AND-ed
    "man_made~^(mast|tower)$"              regex value
    "telecom"                              key present
    "power=line;voltage>=100000"           numeric compare (post-filtered client side)

Scoping: county/state/region/bbox AOIs use bbox tiles (clipped later by the
orchestrator); country AOIs use an Overpass area on ISO3166-1; `world` is
refused (use the osm_pbf driver with a planet/continent extract instead).

Geometry: `out geom` gives node coords and full way/relation member geometry.
Closed ways with area-ish tags become Polygons; multipolygon relations are
stitched from outer/inner member ways (best effort; unstitchable relations
fall back to their centroid, noted in provenance).

OSM is ODbL: attribution + share-alike are carried in provenance.
"""
import json
import re
import time
from typing import Dict, List, Optional, Tuple

from ..aoi import representative_point, tile_bbox
from ..model import Feature, LayerResult, Provenance
from .base import Context, HttpStatusError, driver, http_get, today

DEFAULT_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# Keys whose closed ways are areas (OSM "area" semantics, trimmed to what we use)
AREA_KEYS = {"building", "landuse", "natural", "amenity", "leisure", "power", "man_made",
             "industrial", "military", "aeroway", "healthcare", "emergency", "water",
             "waterway", "reservoir_type", "place", "boundary", "area"}
NOT_AREA_VALUES = {("power", "line"), ("power", "minor_line"), ("power", "cable"),
                   ("man_made", "pipeline"), ("man_made", "embankment"), ("waterway", "river"),
                   ("waterway", "stream"), ("waterway", "canal"), ("natural", "coastline"),
                   ("aeroway", "runway"), ("aeroway", "taxiway"), ("man_made", "breakwater"),
                   ("man_made", "pier"), ("natural", "tree_row"), ("man_made", "goods_conveyor")}


# ---- selector parsing -------------------------------------------------------
_NUM_RX = re.compile(r"^([A-Za-z0-9_:\-]+)\s*(>=|<=|>|<|!=)\s*(-?\d+(?:\.\d+)?)$")


def _parse_selector(sel: str) -> Tuple[str, List[Tuple[str, str, str]]]:
    """Return (overpass_filter_string, [(key, op, num) client-side numeric filters])."""
    parts = [p.strip() for p in sel.split(";") if p.strip()]
    ql, numeric = [], []
    for p in parts:
        m = _NUM_RX.match(p)
        if m:
            k, op, v = m.groups()
            ql.append(f'["{k}"]')
            numeric.append((k, op, v))
            continue
        if "~" in p and "=" not in p.split("~", 1)[0]:
            k, rx = p.split("~", 1)
            ql.append(f'["{k.strip()}"~"{rx.strip()}"]')
        elif "!=" in p:
            k, v = p.split("!=", 1)
            ql.append(f'["{k.strip()}"!="{v.strip()}"]')
        elif "=" in p:
            k, v = p.split("=", 1)
            ql.append(f'["{k.strip()}"="{v.strip()}"]')
        else:
            ql.append(f'["{p}"]')
    return "".join(ql), numeric


def _osm_number(v) -> Optional[float]:
    """Parse OSM numeric-ish values: '115000', '115000;34500' (max), '1.2 MW', '345 kV'."""
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s:
        return None
    vals = []
    for part in re.split(r"[;,/]", s):
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*([a-z]*)", part.strip())
        if not m:
            continue
        num, unit = float(m.group(1)), m.group(2)
        mult = {"k": 1e3, "kv": 1e3, "kw": 1e3, "mw": 1e6, "mv": 1e6, "mwh": 1e6,
                "g": 1e9, "gw": 1e9, "m": 1.0, "ft": 0.3048}.get(unit, 1.0)   # 'm' = metres, not mega
        vals.append(num * mult)
    return max(vals) if vals else None


def _numeric_ok(tags: dict, filters) -> bool:
    for k, op, v in filters:
        x = _osm_number(tags.get(k))
        if x is None:
            return False
        y = float(v)
        if not {">=": x >= y, "<=": x <= y, ">": x > y, "<": x < y, "!=": x != y}[op]:
            return False
    return True


# ---- geometry assembly ----------------------------------------------------
def _is_area(tags: dict, closed: bool, mode: str) -> bool:
    if mode == "polygon":
        return closed
    if mode == "line":
        return False
    if not closed:
        return False
    if tags.get("area") == "no":
        return False
    for k, v in tags.items():
        if (k, v) in NOT_AREA_VALUES:
            return False
    return any(k in AREA_KEYS for k in tags) or tags.get("area") == "yes"


def _ring(coords):
    return coords if coords[0] == coords[-1] else coords + [coords[0]]


def _stitch(ways: List[List[List[float]]]) -> List[List[List[float]]]:
    """Join way segments end-to-end into closed rings (best effort)."""
    segs = [list(w) for w in ways if len(w) >= 2]
    rings = []
    while segs:
        cur = segs.pop(0)
        changed = True
        while changed and cur[0] != cur[-1]:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == cur[-1]:
                    cur += s[1:]; segs.pop(i); changed = True; break
                if s[-1] == cur[-1]:
                    cur += list(reversed(s))[1:]; segs.pop(i); changed = True; break
                if s[-1] == cur[0]:
                    cur = s[:-1] + cur; segs.pop(i); changed = True; break
                if s[0] == cur[0]:
                    cur = list(reversed(s))[:-1] + cur; segs.pop(i); changed = True; break
        if len(cur) >= 4 and cur[0] == cur[-1]:
            rings.append(cur)
    return rings


def _relation_geometry(el: dict, tags: dict, mode: str):
    outers, inners, lines = [], [], []
    for m in el.get("members", []):
        g = m.get("geometry")
        if not g or m.get("type") != "way":
            continue
        coords = [[p["lon"], p["lat"]] for p in g]
        role = m.get("role", "")
        if role == "inner":
            inners.append(coords)
        elif role in ("outer", ""):
            outers.append(coords)
        else:
            lines.append(coords)
    if tags.get("type") in ("multipolygon", "boundary") or (outers and mode != "line"):
        o_rings, i_rings = _stitch(outers), _stitch(inners)
        if o_rings:
            polys = []
            for o in o_rings:
                polys.append([o] + i_rings)   # holes assigned to every outer: acceptable approx
            return {"type": "MultiPolygon", "coordinates": polys} if len(polys) > 1 \
                else {"type": "Polygon", "coordinates": polys[0]}
        if tags.get("type") == "multipolygon":
            # unstitchable (partial download / broken relation): fall back to the centroid
            pts = [p for w in outers + inners for p in w]
            if pts:
                return {"type": "Point", "coordinates": [sum(p[0] for p in pts) / len(pts),
                                                          sum(p[1] for p in pts) / len(pts)]}
    allines = outers + inners + lines
    if allines:
        return {"type": "MultiLineString", "coordinates": allines} if len(allines) > 1 \
            else {"type": "LineString", "coordinates": allines[0]}
    if "center" in el:
        return {"type": "Point", "coordinates": [el["center"]["lon"], el["center"]["lat"]]}
    return None


def element_to_geometry(el: dict, mode: str = "auto"):
    t = el.get("type")
    tags = el.get("tags", {}) or {}
    if t == "node":
        if "lat" in el:
            return {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        return None
    if t == "way":
        g = el.get("geometry")
        if not g:
            if "center" in el:
                return {"type": "Point", "coordinates": [el["center"]["lon"], el["center"]["lat"]]}
            return None
        coords = [[p["lon"], p["lat"]] for p in g]
        if len(coords) < 2:
            return None
        closed = coords[0] == coords[-1] and len(coords) >= 4
        if _is_area(tags, closed, mode):
            return {"type": "Polygon", "coordinates": [_ring(coords)]}
        return {"type": "LineString", "coordinates": coords}
    if t == "relation":
        return _relation_geometry(el, tags, mode)
    return None


# ---- query -----------------------------------------------------------------
# Overpass QL element types. Short forms are accepted in the catalog and
# normalised here: `n`, `w`, `r` on their own are NOT valid Overpass syntax and
# make the whole query fail.
ELEMENT_TYPES = {
    "n": "node", "node": "node", "nodes": "node",
    "w": "way", "way": "way", "ways": "way",
    "r": "rel", "rel": "rel", "relation": "rel", "relations": "rel",
    "nw": "nw", "nr": "nr", "wr": "wr", "nwr": "nwr", "derived": "derived",
}


def normalize_elements(elements: str) -> str:
    e = str(elements or "nwr").strip().lower()
    if e not in ELEMENT_TYPES:
        raise RuntimeError(
            f"'{elements}' is not an Overpass element type; use one of "
            f"{sorted(set(ELEMENT_TYPES.values()))} (or the short forms n/w/r)")
    return ELEMENT_TYPES[e]


def build_query(selectors: List[str], elements: str, scope: str, timeout: int,
                out: str = "geom", prelude: str = "") -> Tuple[str, list]:
    numeric_all = []
    union = []
    elements = normalize_elements(elements)
    for sel in selectors:
        ql, numeric = _parse_selector(sel)
        numeric_all.extend(numeric)
        union.append(f"{elements}{ql}{scope};")
    q = f"[out:json][timeout:{timeout}];{prelude}({''.join(union)});out {out} qt;"
    return q, numeric_all


def _country_prelude(cc: str) -> str:
    return f'area["ISO3166-1"="{cc}"]["admin_level"="2"]->.a;'


def country_bounds(cc: str, endpoints: List[str], timeout: int, min_iv: float):
    """(west, south, east, north) of the country's admin_level=2 relation, or None."""
    q = f'[out:json][timeout:{timeout}];rel["ISO3166-1"="{cc}"]["admin_level"="2"];out bb;'
    js = _run(q, endpoints, timeout, min_iv)
    for el in js.get("elements", []):
        b = el.get("bounds")
        if b:
            return (b["minlon"], b["minlat"], b["maxlon"], b["maxlat"])
    return None


def _run(q: str, endpoints: List[str], timeout: int, min_interval: float) -> dict:
    last = None
    for ep in endpoints:
        for attempt in range(3):
            try:
                raw = http_get(ep, data=("data=" + _urlq(q)).encode("utf-8"), tries=1,
                               timeout=timeout + 30, cache=False, min_interval=min_interval,
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
                js = json.loads(raw)
                if js.get("remark", "").lower().startswith("runtime error"):
                    raise RuntimeError(js["remark"])
                return js
            except HttpStatusError as e:
                last = e
                if e.code in (429, 504):
                    time.sleep(10 * (attempt + 1))
                    continue
                break
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"overpass failed on all endpoints: {last}")


def _urlq(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def fetch_elements(selectors, elements, ctx: Context, spec: dict) -> Tuple[List[dict], list, List[str]]:
    timeout = int(spec.get("overpass_timeout", 180))
    endpoints = spec.get("endpoints") or DEFAULT_ENDPOINTS
    min_iv = float(spec.get("min_interval", 1.0))
    out = "center" if spec.get("geometry") == "point" else "geom"
    seen: Dict[Tuple[str, int], dict] = {}
    numeric = []
    notes = []
    max_tiles = int(spec.get("max_tiles", ctx.options.get("max_tiles", 200)))
    if ctx.aoi.kind == "country" and ctx.aoi.country:
        cc = ctx.aoi.country
        if not ctx.bbox:
            bb = country_bounds(cc, endpoints, 60, min_iv)
            if not bb:
                raise RuntimeError(f"no OSM admin_level=2 relation with ISO3166-1={cc}; check the country code")
            ctx.bbox = bb
            notes.append(f"country bounds from OSM: {tuple(round(x, 3) for x in bb)}")
        tiles = tile_bbox(ctx.bbox, float(spec.get("tile_deg", 1.0)))
        if len(tiles) > max_tiles:
            raise RuntimeError(
                f"country {cc} needs {len(tiles)} Overpass tiles (> max_tiles={max_tiles}); use the osm_pbf "
                f"driver with a Geofabrik extract, a larger tile_deg, or raise max_tiles")
        if len(tiles) > 1:
            notes.append(f"{len(tiles)} bbox tiles within the country area")
        for (w, s, e, n) in tiles:
            scope = f"(area.a)({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
            q, numeric = build_query(selectors, elements, scope, timeout, out, _country_prelude(cc))
            for el in _run(q, endpoints, timeout, min_iv).get("elements", []):
                seen[(el["type"], el["id"])] = el
    elif ctx.aoi.kind == "world" or not ctx.bbox:
        raise RuntimeError("overpass refuses a world/unbounded AOI; use --aoi country:XX, "
                           "a bbox, or the osm_pbf driver with a planet extract")
    else:
        tiles = tile_bbox(ctx.bbox, float(spec.get("tile_deg", 1.0)))
        if len(tiles) > max_tiles:
            raise RuntimeError(
                f"AOI needs {len(tiles)} Overpass tiles (> max_tiles={max_tiles}); use a smaller AOI, "
                f"--aoi country:XX, a larger tile_deg, or the osm_pbf driver with a Geofabrik extract")
        if len(tiles) > 1:
            notes.append(f"{len(tiles)} bbox tiles")
        for (w, s, e, n) in tiles:
            scope = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
            q, numeric = build_query(selectors, elements, scope, timeout, out)
            for el in _run(q, endpoints, timeout, min_iv).get("elements", []):
                seen[(el["type"], el["id"])] = el
    return list(seen.values()), numeric, notes


@driver("overpass")
def fetch(logical: str, spec: dict, ctx: Context) -> LayerResult:
    selectors = spec.get("tags") or ([f'{spec["tag"]}'] if spec.get("tag") else [])
    if not selectors:
        raise RuntimeError(f"{logical}: overpass source needs `tags: [...]`")
    elements = spec.get("elements", "nwr")
    mode = spec.get("geometry", "auto")
    represent = spec.get("represent", "shape")
    els, numeric, notes = fetch_elements(selectors, elements, ctx, spec)

    feats: List[Feature] = []
    dropped = 0
    for el in els:
        tags = el.get("tags", {}) or {}
        if numeric and not _numeric_ok(tags, numeric):
            continue
        geom = element_to_geometry(el, mode)
        if geom is None:
            dropped += 1
            continue
        props = dict(tags)
        props["osm_type"] = el["type"]
        props["osm_id"] = el["id"]
        props["osm_url"] = f"https://www.openstreetmap.org/{el['type']}/{el['id']}"
        if represent in ("point", "both") and geom["type"] != "Point":
            rp = representative_point(geom)
            if represent == "point":
                geom = {"type": "Point", "coordinates": rp}
            else:
                feats.append(Feature({"type": "Point", "coordinates": rp}, dict(props, _shape="marker")))
        feats.append(Feature(geom, props))
    if dropped:
        notes.append(f"{dropped} elements without usable geometry skipped")
    prov = Provenance(
        source_name="OpenStreetMap (Overpass API)",
        source_url=(spec.get("endpoints") or DEFAULT_ENDPOINTS)[0] + "  tags=" + " | ".join(selectors),
        license="ODbL 1.0 - (c) OpenStreetMap contributors (attribution + share-alike)",
        retrieved=today(), driver="overpass",
        notes="; ".join([spec.get("notes", "")] + notes).strip("; "))
    return LayerResult(logical, feats, prov, spec.get("group_by"), len(feats))
