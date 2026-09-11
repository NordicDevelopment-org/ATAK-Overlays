"""ArcGIS REST driver (MapServer/FeatureServer) with AOI-aware scoping.

Discovers the target layer by explicit id or a name regex, then pulls features
as GeoJSON in EPSG:4326, paginated (resultOffset, or OBJECTID ranges when the
server lacks pagination). Falls back to Esri JSON if f=geojson is unsupported.

Scoping (all optional; combine freely):
  where:        SQL filter, templated with {state_abbr} {fips5} {states_sql} ...
  where_by_aoi: per-AOI-kind SQL, e.g. {county: "COUNTYFIPS='{fips5}'",
                state: "STATE='{state_abbr}'", region: "STATE IN ({states_sql})"}
                The first key matching ctx.aoi.kind wins; else `where`; else 1=1.
  spatial:      bbox (default when the AOI has one) | none  - sends the AOI
                envelope as an esriGeometryEnvelope intersects filter. Cheap,
                works on every server, and lets one national layer serve any AOI.
  out_fields:   "*" (default) or a comma list
  token_env:    name of an env var holding a token for services that need one
  page:         page size (default 2000; clamped to server maxRecordCount)
  min_interval: seconds between requests to this host (politeness)

Other spec fields: url, layer_id, layer_match, group_by, source_name,
source_url, license, notes, entity, fields (see normalize.py).
"""
import json
import os
import re
from typing import Optional

from ..model import Feature, LayerResult, Provenance
from .base import Context, HttpStatusError, driver, get_json, http_get, today


def _esri_to_geojson(g, gtype):
    if g is None:
        return None
    if "x" in g and "y" in g:
        return {"type": "Point", "coordinates": [g["x"], g["y"]]}
    if "points" in g:
        return {"type": "MultiPoint", "coordinates": g["points"]}
    if "paths" in g:
        paths = g["paths"]
        return ({"type": "LineString", "coordinates": paths[0]} if len(paths) == 1
                else {"type": "MultiLineString", "coordinates": paths})
    if "rings" in g:
        return {"type": "MultiPolygon", "coordinates": [[r] for r in g["rings"]]}
    return None


def _resolve_layer_id(url, spec, token):
    if spec.get("layer_id") is not None:
        return int(spec["layer_id"])
    rx = re.compile(spec.get("layer_match", ""), re.I)
    meta = get_json(url, {"token": token} if token else None, cache=False)
    if "error" in meta:
        raise RuntimeError(f"service error at {url}: {meta['error']}")
    for lyr in meta.get("layers", []) + meta.get("tables", []):
        if lyr.get("subLayerIds"):
            continue
        if rx.search(lyr.get("name", "")):
            return lyr["id"]
    names = [l.get("name") for l in meta.get("layers", [])]
    raise RuntimeError(f"no layer matched /{spec.get('layer_match')}/ at {url}; layers: {names}")


def _where(spec: dict, ctx: Context) -> str:
    by = spec.get("where_by_aoi") or {}
    kind = ctx.aoi.kind
    if kind in by:
        return ctx.render(by[kind])
    if kind == "county" and "county" not in by and "state" in by and spec.get("state_fallback", True):
        # a county build can use the state filter + bbox
        return ctx.render(by["state"])
    return ctx.render(spec.get("where", "1=1")) or "1=1"


def _spatial_params(spec: dict, ctx: Context) -> dict:
    mode = spec.get("spatial", "bbox")
    if mode == "none" or not ctx.bbox or ctx.aoi.kind in ("world",):
        return {}
    w, s, e, n = ctx.bbox
    return {"geometry": f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}",
            "geometryType": "esriGeometryEnvelope", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects"}


def layer_info(url: str, layer_id: int, token: Optional[str] = None) -> dict:
    return get_json(f"{url}/{layer_id}", {"token": token} if token else None, cache=False)


@driver("arcgis")
def fetch(logical: str, spec: dict, ctx: Context) -> LayerResult:
    url = ctx.render(spec["url"]).rstrip("/")
    token = os.environ.get(spec["token_env"]) if spec.get("token_env") else None
    if spec.get("token_env") and not token:
        raise RuntimeError(f"{logical}: env var {spec['token_env']} not set (service needs a token)")
    layer_id = _resolve_layer_id(url, spec, token)
    base = f"{url}/{layer_id}/query"
    where = _where(spec, ctx)
    sp = _spatial_params(spec, ctx)
    min_iv = float(spec.get("min_interval", 0.0))
    out_fields = spec.get("out_fields", "*")

    info = {}
    try:
        info = layer_info(url, layer_id, token)
    except Exception:
        pass
    max_rc = int(info.get("maxRecordCount") or 2000)
    page = max(1, min(int(spec.get("page", 2000)), max_rc))
    supports_pag = bool((info.get("advancedQueryCapabilities") or {}).get("supportsPagination", True))
    oid_field = info.get("objectIdField") or "OBJECTID"
    gtype = info.get("geometryType", "")

    common = {"where": where, "outFields": out_fields, "outSR": "4326",
              "geometryPrecision": int(spec.get("precision", 6)), "returnGeometry": "true"}
    common.update(sp)
    if token:
        common["token"] = token

    total = None
    try:
        probe = get_json(base, dict(common, returnCountOnly="true"), cache=False, min_interval=min_iv)
        total = probe.get("count")
    except Exception:
        pass

    feats = []
    use_geojson = spec.get("format", "geojson") == "geojson"

    def _pull(params):
        nonlocal use_geojson
        if use_geojson:
            p = dict(params, f="geojson")
            raw = http_get(base, p, cache=False, min_interval=min_iv)
            try:
                js = json.loads(raw)
            except json.JSONDecodeError:
                js = {"error": "not json"}
            if "error" not in js:
                out = []
                for ft in js.get("features", []):
                    out.append(Feature(ft.get("geometry"), ft.get("properties", {}) or {}))
                return out, bool(js.get("exceededTransferLimit") or
                                 (js.get("properties") or {}).get("exceededTransferLimit"))
            use_geojson = False  # fall through to esri json
        p = dict(params, f="json")
        js = json.loads(http_get(base, p, cache=False, min_interval=min_iv))
        if "error" in js:
            raise RuntimeError(f"layer {layer_id} error: {js['error']}")
        gt = js.get("geometryType", gtype)
        out = [Feature(_esri_to_geojson(f.get("geometry"), gt), f.get("attributes", {}) or {})
               for f in js.get("features", [])]
        return out, bool(js.get("exceededTransferLimit"))

    if supports_pag:
        offset = 0
        while True:
            batch, more = _pull(dict(common, resultOffset=offset, resultRecordCount=page))
            feats.extend(batch)
            offset += len(batch)
            if not batch or (len(batch) < page and not more) or (total is not None and offset >= total):
                break
    else:
        # OBJECTID-range paging for old servers
        ids_js = get_json(base, dict(common, returnIdsOnly="true"), cache=False, min_interval=min_iv)
        ids = sorted(ids_js.get("objectIds") or [])
        oid_field = ids_js.get("objectIdFieldName") or oid_field
        for i in range(0, len(ids), page):
            chunk = ids[i:i + page]
            w2 = f"({where}) AND {oid_field} >= {chunk[0]} AND {oid_field} <= {chunk[-1]}"
            batch, _ = _pull(dict(common, where=w2))
            feats.extend(batch)

    prov = Provenance(
        source_name=spec.get("source_name", f"ArcGIS REST {info.get('name') or layer_id}"),
        source_url=spec.get("source_url", f"{url}/{layer_id}"),
        license=spec.get("license", "see source server terms"),
        retrieved=today(), driver="arcgis",
        notes=(spec.get("notes", "") + f" | query: {where}" + (" + bbox" if sp else "")).strip(" |"))
    return LayerResult(logical, feats, prov, spec.get("group_by"), total)
