"""overlaybuilder CLI.

  overlaybuilder build --aoi county:27025                 # Chisago County, MN - everything
  overlaybuilder build --aoi state:MN --sectors energy    # state-wide energy layers
  overlaybuilder build --aoi region:upper-midwest --layers power_plants substations transmission_lines
  overlaybuilder build --aoi us --layers power_plants     # national
  overlaybuilder build --aoi country:CA --sectors energy  # world tier (OSM)
  overlaybuilder build --aoi bbox:-93.2,45.3,-92.6,45.8
  overlaybuilder sources --aoi county:27025               # what would build
  overlaybuilder probe https://host/arcgis/rest/services/X/MapServer   # inspect a server
  overlaybuilder validate                                  # lint the catalog offline
  overlaybuilder list-drivers | list-counties | list-regions

Legacy (still works): overlaybuilder --fips 27025 / --state MN --county Chisago
"""
import argparse
import json
import os
import sys

from . import __version__, catalog
from .aoi import load_regions, parse_aoi
from .build import run_build
from .drivers import Context, configure_http, known_drivers


def _catalog_dir(arg):
    if arg:
        return arg
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cand = os.path.join(here, "catalog")
    return cand if os.path.isdir(cand) else "catalog"


def _add_common(ap):
    ap.add_argument("--aoi", help="county:FIPS | state:XX | region:NAME | us | conus | country:XX | bbox:W,S,E,N")
    ap.add_argument("--state", help="(legacy) state abbr or name")
    ap.add_argument("--county", help="(legacy) county name")
    ap.add_argument("--fips", help="(legacy) 5-digit county FIPS")
    ap.add_argument("--layers", nargs="*", help="only these logical layers")
    ap.add_argument("--exclude", nargs="*", help="skip these logical layers")
    ap.add_argument("--sectors", nargs="*", help="only sources whose sector starts with these (energy, water, comm, emergency, transport, base)")
    ap.add_argument("--catalog", help="catalog directory (default: repo ./catalog)")
    ap.add_argument("--cache-dir", default=".cache")
    ap.add_argument("--tiger-year", type=int, default=2024)


def _resolve_aoi(args):
    if args.aoi:
        return parse_aoi(args.aoi, cache_dir=args.cache_dir, county_name=args.county)
    if args.fips:
        return parse_aoi(f"county:{args.fips}", cache_dir=args.cache_dir, county_name=args.county)
    if args.state and args.county:
        from .fips import resolve
        sfp, cfp, cname, abbr = resolve(args.state, args.county, None, cache_dir=args.cache_dir)
        return parse_aoi(f"county:{sfp}{cfp}", cache_dir=args.cache_dir, county_name=cname)
    if args.state:
        return parse_aoi(f"state:{args.state}", cache_dir=args.cache_dir)
    raise SystemExit("provide --aoi (e.g. county:27025, state:MN, region:upper-midwest, us)")


def cmd_build(args):
    cat = _catalog_dir(args.catalog)
    aoi = _resolve_aoi(args)
    print(f"[*] AOI: {aoi.describe()}")
    configure_http(args.cache_dir, not args.no_http_cache)
    ctx = Context(aoi=aoi, tiger_year=args.tiger_year, cache_dir=args.cache_dir,
                  http_cache=not args.no_http_cache, clip=not args.no_clip)
    sources = catalog.resolve_sources(cat, aoi, args.layers, args.sectors, args.exclude)
    if not sources:
        print("[!] no sources matched. Check --layers/--sectors, or add a catalog entry.")
        return 1
    print(f"[*] {len(sources)} source(s): " + ", ".join(s.get("id", s["layer"]) for s in sources))
    out_dir = args.out if args.flat else os.path.join(args.out, aoi.slug)
    manifest = run_build(ctx, sources, out_dir, args.format, not args.no_combined,
                         precision=args.precision, fail_fast=args.fail_fast,
                         do_reconcile=not args.no_reconcile)

    print("\n==================== SUMMARY ====================")
    for row in manifest["layers"]:
        extra = ""
        if row.get("dropped_outside_aoi"):
            extra += f"  -{row['dropped_outside_aoi']} outside AOI"
        if row.get("source"):
            extra += f"  [{row['source']}]"
        print(f"  {row.get('doc', row['layer']):28} {str(row['features']):>8} feat   {row['status'][:60]}{extra}")
    print("=================================================")
    print(f"Output: {os.path.abspath(out_dir)}  ({manifest['seconds']}s)")
    print("ATAK: Import Manager > Local SD > select .kmz (or drop in atak/imports/).")
    print("Toggle sectors/layers/classes with the eye button in Overlay Manager.")
    errs = [r for r in manifest["layers"] if str(r["status"]).startswith("ERROR")]
    return 2 if errs and len(errs) == len(sources) else 0


def cmd_sources(args):
    cat = _catalog_dir(args.catalog)
    aoi = _resolve_aoi(args)
    srcs = catalog.resolve_sources(cat, aoi, args.layers, args.sectors, args.exclude)
    print(f"{len(srcs)} source(s) for {aoi.describe()}:")
    for s in srcs:
        print(f"  {s['_tier']:8} {s['layer']:24} {s['driver']:12} {s.get('sector',''):24} {s.get('source_name', s.get('url', ''))[:70]}")
    return 0


def cmd_probe(args):
    """Inspect an ArcGIS server/layer: list layers, or a layer's fields + count."""
    from .drivers.base import get_json
    url = args.url.rstrip("/")
    js = get_json(url, cache=False)
    if "error" in js:
        print("error:", js["error"])
        return 1
    if "layers" in js and "fields" not in js:
        print(f"service: {js.get('mapName') or js.get('serviceDescription', '')[:80]}")
        for l in js.get("layers", []):
            print(f"  [{l['id']:>3}] {l.get('name')}  {l.get('geometryType', '')}")
        for t in js.get("tables", []):
            print(f"  [{t['id']:>3}] {t.get('name')}  (table)")
        return 0
    print(f"layer: {js.get('name')}  type={js.get('geometryType')}  maxRecordCount={js.get('maxRecordCount')}")
    print(f"pagination={((js.get('advancedQueryCapabilities') or {}).get('supportsPagination'))}  "
          f"oid={js.get('objectIdField')}")
    for f in js.get("fields", []):
        print(f"  {f.get('name'):32} {f.get('type', '').replace('esriFieldType', ''):12} {f.get('alias', '')}")
    try:
        cnt = get_json(url + "/query", {"where": "1=1", "returnCountOnly": "true"}, cache=False)
        print("count:", cnt.get("count"))
        if args.sample:
            smp = get_json(url + "/query", {"where": "1=1", "outFields": "*", "resultRecordCount": 1,
                                           "returnGeometry": "false"}, cache=False)
            print(json.dumps((smp.get("features") or [{}])[0].get("attributes"), indent=1)[:3000])
    except Exception as e:  # noqa: BLE001
        print("count/sample failed:", e)
    return 0


def cmd_validate(args):
    cat = _catalog_dir(args.catalog)
    problems = catalog.validate(cat)
    srcs = catalog.all_sources(cat)
    print(f"{len(srcs)} sources in {cat}")
    for p in problems:
        print("  !", p)
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # legacy invocation without a subcommand
    if argv and argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"):
        argv = ["build"] + argv
    ap = argparse.ArgumentParser(prog="overlaybuilder",
                                 description="Build ATAK critical-infrastructure overlays (KMZ) from public GIS, for any AOI.")
    ap.add_argument("--version", action="version", version=f"overlaybuilder {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    b = sub.add_parser("build", help="fetch sources and write KMZ/GeoJSON")
    _add_common(b)
    b.add_argument("--format", nargs="*", default=["kmz"], choices=["kmz", "geojson"])
    b.add_argument("--no-combined", action="store_true", help="skip ALL.kmz")
    b.add_argument("--flat", action="store_true", help="write into --out directly (no AOI subfolder)")
    b.add_argument("--out", default="overlays")
    b.add_argument("--precision", type=int, default=6, help="coordinate decimals (5 ~ 1 m)")
    b.add_argument("--no-clip", action="store_true", help="keep features outside the AOI boundary")
    b.add_argument("--no-http-cache", action="store_true")
    b.add_argument("--no-reconcile", action="store_true")
    b.add_argument("--fail-fast", action="store_true")
    b.set_defaults(fn=cmd_build)

    s = sub.add_parser("sources", help="list the sources that would build for an AOI")
    _add_common(s)
    s.set_defaults(fn=cmd_sources)

    p = sub.add_parser("probe", help="inspect an ArcGIS REST service or layer URL")
    p.add_argument("url")
    p.add_argument("--sample", action="store_true", help="print one record's attributes")
    p.set_defaults(fn=cmd_probe)

    v = sub.add_parser("validate", help="lint the catalog offline")
    v.add_argument("--catalog")
    v.set_defaults(fn=cmd_validate)

    for name in ("list-drivers", "list-counties", "list-regions"):
        l = sub.add_parser(name)
        l.add_argument("--catalog")
        l.set_defaults(fn=None, which=name)

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0
    if args.fn is None:
        cat = _catalog_dir(args.catalog)
        if args.which == "list-drivers":
            print("drivers:", ", ".join(known_drivers()))
        elif args.which == "list-counties":
            cs = catalog.list_counties(cat)
            print(f"catalog counties ({len(cs)}):")
            for c in cs:
                print("  ", c)
        else:
            for k, v in load_regions(os.path.join(cat, "regions.yaml")).items():
                print(f"  {k:18} {' '.join(v)}")
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
