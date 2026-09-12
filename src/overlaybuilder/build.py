"""Orchestrator: sources -> drivers -> normalize -> clip -> KMZ/GeoJSON + manifest.

Boundary layers (county_boundary / state_boundary) build first so the AOI's
exact polygon can clip everything else and refine the query envelope handed
to bbox-scoped drivers (ArcGIS envelope filter, Overpass tiles).
"""
import datetime as _dt
import json
import os
import time
import traceback
from dataclasses import asdict
from typing import Dict, List, Optional

from . import convert, normalize, reconcile
from .aoi import BoundaryIndex, bbox_of_geometry, geometry_touches
from .drivers import Context, get_driver
from .model import LayerResult

BOUNDARY_LAYERS = ("county_boundary", "state_boundary")


def _merge_boundary(res: LayerResult) -> Optional[dict]:
    polys = []
    for f in res.features:
        g = f.geometry
        if not g:
            continue
        if g["type"] == "Polygon":
            polys.append(g["coordinates"])
        elif g["type"] == "MultiPolygon":
            polys.extend(g["coordinates"])
    if not polys:
        return None
    return {"type": "MultiPolygon", "coordinates": polys}


def clip_layer(res: LayerResult, ctx: Context) -> int:
    """Drop features outside the AOI (bbox + boundary polygon). Returns dropped count."""
    if not ctx.clip or res.logical in BOUNDARY_LAYERS:
        return 0
    if not ctx.boundary and not ctx.bbox:
        return 0
    keep = []
    for f in res.features:
        if f.geometry and geometry_touches(f.geometry, ctx.boundary, ctx.bbox, ctx.boundary_index):
            keep.append(f)
    dropped = len(res.features) - len(keep)
    res.features = keep
    return dropped


def _dedupe_layer(res: LayerResult) -> int:
    """Remove exact duplicates (same osm id / same source id + same geometry)."""
    seen, keep = set(), []
    for f in res.features:
        p = f.properties or {}
        key = None
        if "osm_id" in p:
            key = ("osm", p.get("osm_type"), p["osm_id"], p.get("_shape"))
        elif p.get("source_id") not in (None, ""):
            key = ("src", str(p["source_id"]), json.dumps(f.geometry, sort_keys=True)[:200])
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        keep.append(f)
    d = len(res.features) - len(keep)
    res.features = keep
    return d


ALTERNATE_DROP = ("note", "notes", "confidence", "alternates")
# Keys that select WHICH data a driver reads. When an alternate points at a
# different endpoint these are cleared first, so a mirror that finds its layer
# by name is never paired with the primary's numeric layer_id. Attribute
# filters (where / where_by_aoi) are deliberately inherited: they express the
# AOI, and ArcGIS field comparisons are case-insensitive on most servers.
ENDPOINT_KEYS = ("layer_id", "layer_match", "product", "table", "zip_member",
                 "format", "sheet", "header_row", "lat_field", "lon_field", "skip_lines")


def merge_alternate(spec: dict, alt: dict) -> dict:
    """A catalog `alternates:` entry overrides the endpoint of its parent and
    inherits everything else (fields, group_by, style, entity, licence...)."""
    merged = dict(spec)
    merged.pop("alternates", None)
    if "url" in alt or "driver" in alt:
        for k in ENDPOINT_KEYS:
            merged.pop(k, None)
    for k, v in alt.items():
        if k in ALTERNATE_DROP:
            continue
        merged[k] = v
    if alt.get("note"):
        merged["notes"] = (str(spec.get("notes", "")) + " | alternate: " + alt["note"]).strip(" |")
    merged["_alternate_of"] = spec.get("id", spec["layer"])
    return merged


def fetch_with_fallback(spec: dict, ctx: Context, use_alternates: bool = True, log=print):
    """Run a source, falling back through its `alternates:` when it fails.

    Returns (LayerResult, attempts) where attempts lists every try as
    (label, "ok"|error text). Raises the FIRST error if every attempt fails, so
    the summary reports the primary endpoint's problem rather than the last
    alternate's.
    """
    attempts = []
    candidates = [(spec.get("id", spec["layer"]), spec)]
    if use_alternates:
        for i, alt in enumerate(spec.get("alternates") or [], 1):
            if not isinstance(alt, dict):
                continue
            candidates.append((f"{spec.get('id', spec['layer'])} alt{i}", merge_alternate(spec, alt)))

    first_error = None
    for label, cand in candidates:
        try:
            res = get_driver(cand["driver"])(cand["layer"], cand, ctx)
        except Exception as e:  # noqa: BLE001  try the next endpoint
            attempts.append((label, f"ERROR: {e}"))
            if first_error is None:
                first_error = e
            if len(candidates) > 1:
                log(f"    {label} failed ({str(e)[:90]}), trying the next endpoint")
            continue
        attempts.append((label, "ok"))
        if cand is not spec:
            res.provenance.notes = (res.provenance.notes +
                                    f" | FALLBACK: primary source {spec.get('id', spec['layer'])} failed "
                                    f"({str(first_error)[:120]})").strip(" |")
            log(f"    using alternate endpoint ({label})")
        return res, cand, attempts
    raise first_error if first_error else RuntimeError("no source candidates")


def run_build(ctx: Context, sources: List[dict], out_dir: str,
              formats: Optional[List[str]] = None, combined: bool = True,
              precision: int = 6, fail_fast: bool = False, do_reconcile: bool = True,
              use_alternates: bool = True, log=print) -> dict:
    """Fetch every source, cross-check, then write. Returns the manifest dict."""
    formats = formats or ["kmz"]
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()

    preferred = "county_boundary" if ctx.aoi.kind == "county" else "state_boundary"

    def _rank(s):
        if s["layer"] == preferred:
            return (0, 0)
        if s["layer"] in BOUNDARY_LAYERS:
            return (0, 1)
        return (1, int(s.get("priority", 50)))
    order = sorted(sources, key=_rank)
    results: List[LayerResult] = []
    specs: Dict[str, dict] = {}
    rows: List[dict] = []
    written: List[str] = []

    boundary_expected = any(s["layer"] in BOUNDARY_LAYERS for s in order)

    # ---- phase 1: fetch + normalize + clip ---------------------------------
    for spec in order:
        logical = spec["layer"]
        sid = spec.get("id", logical)
        t0 = time.time()
        if (logical not in BOUNDARY_LAYERS and boundary_expected and ctx.boundary is None
                and ctx.clip and ctx.aoi.kind in ("county", "state", "region")):
            raise RuntimeError(
                f"the boundary layer for {ctx.aoi.describe()} failed, so features cannot be clipped; "
                "refusing to write a pack from the state envelope. Fix the boundary source "
                "(TIGER download) or re-run with --no-clip to accept envelope-scoped output")
        attempts = []
        try:
            log(f"[>] {sid}  ({spec['driver']})")
            res, used_spec, attempts = fetch_with_fallback(spec, ctx, use_alternates, log)
            normalize.apply_to_layer(res, used_spec)
            dup = _dedupe_layer(res)
            dropped = clip_layer(res, ctx)
        except Exception as e:  # keep going; report per layer
            rows.append({"id": sid, "layer": logical, "status": f"ERROR: {e}", "features": 0,
                         "driver": spec["driver"], "attempts": attempts, "fallback": False,
                         "seconds": round(time.time() - t0, 1)})
            log(f"[!] {sid}: {e}")
            if fail_fast:
                raise
            if os.environ.get("OVERLAYBUILDER_DEBUG"):
                traceback.print_exc()
            continue

        if logical in BOUNDARY_LAYERS and ctx.boundary is None and res.features:
            ctx.boundary = _merge_boundary(res)
            bb = bbox_of_geometry(ctx.boundary) if ctx.boundary else None
            if bb:
                ctx.bbox = bb
                ctx.boundary_index = BoundaryIndex(ctx.boundary)
                log(f"[*] boundary set from {logical}: bbox={tuple(round(x, 4) for x in bb)}")

        # a layer key can come from several sources (EIA + OSM): keep both documents
        n_same = sum(1 for r in results if r.logical == logical)
        provider = spec.get("provider") or spec["driver"]
        doc_key = logical if n_same == 0 else f"{logical}__{provider}"
        if doc_key in specs:
            doc_key = f"{doc_key}{n_same}"
        res_spec = dict(used_spec)
        res_spec.setdefault("title", logical if n_same == 0 else f"{logical} ({provider})")
        res.doc_key = doc_key  # type: ignore[attr-defined]
        results.append(res)
        specs[doc_key] = res_spec
        rows.append({"id": sid, "layer": logical, "doc": doc_key, "status": "ok",
                     "features": len(res.features), "dropped_outside_aoi": dropped,
                     "deduped": dup, "server_count": res.server_count, "driver": spec["driver"],
                     "source": res.provenance.source_name, "license": res.provenance.license,
                     "group_by": res.group_by, "seconds": round(time.time() - t0, 1),
                     "fallback": used_spec.get("_alternate_of") is not None,
                     "attempts": attempts if len(attempts) > 1 else None})
        log(f"    {len(res.features)} features ({dropped} outside AOI dropped)  {rows[-1]['seconds']}s")

    # ---- phase 2: cross-source check (stamps xcheck on features) ------------
    rec = None
    if do_reconcile and results:
        try:
            rec = reconcile.reconcile_layers(results, specs)
            if rec["pairs"]:
                reconcile.write_report(os.path.join(out_dir, "reconcile.md"), rec, ctx)
                written.append(os.path.join(out_dir, "reconcile.md"))
        except Exception as e:  # noqa: BLE001
            log(f"[!] reconcile skipped: {e}")

    # ---- phase 3: write --------------------------------------------------------
    for res in results:
        doc_key = res.doc_key  # type: ignore[attr-defined]
        res_spec = specs[doc_key]
        stem = os.path.join(out_dir, doc_key)
        if "kmz" in formats:
            kml, icons = convert.layer_kml(res, res_spec, precision, title=res_spec["title"])
            n = convert.write_kmz(stem + ".kmz", kml, icons)
            written.append(stem + ".kmz")
            for r in rows:
                if r.get("doc") == doc_key:
                    r["placemarks"] = n
        if "geojson" in formats:
            convert.write_geojson(stem + ".geojson", res)
            written.append(stem + ".geojson")

    if combined and results and "kmz" in formats:
        p = os.path.join(out_dir, "ALL.kmz")
        spec_by_logical: Dict[str, dict] = {}
        for k, s in specs.items():
            spec_by_logical.setdefault(s["layer"], s)
        kml, icons = convert.combined_kml(results, spec_by_logical, precision,
                                          title=f"Critical Infrastructure - {ctx.aoi.describe()}")
        convert.write_kmz(p, kml, icons)
        written.append(p)
        rows.append({"id": "ALL", "layer": "ALL", "status": "ok",
                     "features": sum(len(r.features) for r in results)})

    manifest = {
        "tool": "overlaybuilder", "built": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "aoi": {"kind": ctx.aoi.kind, "id": ctx.aoi.id, "name": ctx.aoi.describe(),
                "bbox": ctx.bbox, "states": ctx.aoi.state_abbrs, "fips5": ctx.aoi.fips5},
        "layers": rows, "files": [os.path.relpath(w, out_dir) for w in written],
        "provenance": {r.doc_key: asdict(r.provenance) for r in results},  # type: ignore[attr-defined]
        "seconds": round(time.time() - started, 1),
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, default=str)
    return manifest
