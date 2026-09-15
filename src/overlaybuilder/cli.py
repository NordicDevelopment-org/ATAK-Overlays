"""overlaybuilder CLI.

  overlaybuilder build --aoi county:27025                 # Chisago County, MN - everything
  overlaybuilder build --aoi state:MN --sectors energy    # state-wide energy layers
  overlaybuilder build --aoi region:upper-midwest --layers power_plants substations transmission_lines
  overlaybuilder build --aoi us --layers power_plants     # national
  overlaybuilder build --aoi country:CA --sectors energy  # world tier (OSM); CA = Canada here
  overlaybuilder build --aoi bbox:-93.2,45.3,-92.6,45.8
  overlaybuilder sources --aoi county:27025               # what would build
  overlaybuilder doctor  --aoi county:27025               # probe every endpoint, report dead ones
  overlaybuilder demo                                     # sample pack (synthetic) to test ATAK rendering
  overlaybuilder probe https://host/arcgis/rest/services/X/MapServer   # inspect a server
  overlaybuilder validate                                  # lint the catalog offline
  overlaybuilder list-drivers | list-counties | list-regions

Legacy (still works): overlaybuilder --fips 27025 / --state MN --county Chisago
"""
import argparse
import glob
import json
import os
import sys

from . import __version__, catalog
from .aoi import load_regions, parse_aoi
from .build import run_build
from .drivers import Context, configure_http, known_drivers
from .http import set_max_per_host


def _catalog_dir(arg, require=True):
    """--catalog, else $OVERLAYBUILDER_CATALOG, else the repo's ./catalog.

    The catalog is data, not code: a wheel installed outside a checkout has
    none, and silently resolving zero sources looks like success. Fail loudly
    instead."""
    def _has_yaml(d):
        return os.path.isdir(d) and bool(glob.glob(os.path.join(d, "**", "*.yaml"), recursive=True))

    if arg:                       # an explicit path is authoritative: never fall back past it
        if _has_yaml(arg):
            return arg
        raise SystemExit(f"--catalog {arg} is not a catalog directory (no .yaml files under it)")
    cands = []
    env = os.environ.get("OVERLAYBUILDER_CATALOG")
    if env:
        cands.append(env)
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cands += [os.path.join(here, "catalog"), os.path.join(os.getcwd(), "catalog")]
    for c in cands:
        if os.path.isdir(c) and glob.glob(os.path.join(c, "**", "*.yaml"), recursive=True):
            return c
    if not require:
        return cands[0]
    raise SystemExit(
        "no source catalog found (looked in: " + ", ".join(cands) + ").\n"
        "The catalog ships with the repository, not the wheel. Clone it and either run from the\n"
        "checkout, pass --catalog /path/to/catalog, or set OVERLAYBUILDER_CATALOG.")


def _add_common(ap):
    ap.add_argument("--aoi", help="county:FIPS | state:XX | region:NAME | us | conus | country:XX | bbox:W,S,E,N")
    ap.add_argument("--state", help="(legacy) state abbr or name")
    ap.add_argument("--county", help="(legacy) county name")
    ap.add_argument("--fips", help="(legacy) 5-digit county FIPS")
    ap.add_argument("--layers", nargs="*", help="only these logical layers")
    ap.add_argument("--exclude", nargs="*", help="skip these logical layers")
    ap.add_argument("--sectors", nargs="*", help="only sources whose sector starts with these (energy, water, comm, emergency, transport, chemical, agriculture, mining, base)")
    ap.add_argument("--include-disabled", action="store_true",
                    help="also include sources the catalog ships switched off (candidates whose "
                         "endpoint is documented but unverified)")
    ap.add_argument("--catalog", help="catalog directory (default: repo ./catalog)")
    ap.add_argument("--cache-dir", default=".cache")
    ap.add_argument("--tiger-year", type=int, default=2024)


class AoiError(SystemExit):
    pass


def _resolve_aoi(args):
    try:
        return _resolve_aoi_inner(args)
    except SystemExit:
        raise
    except (ValueError, KeyError, RuntimeError) as e:
        raise AoiError(f"cannot resolve the AOI: {e}") from None


def _resolve_aoi_inner(args):
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
    set_max_per_host(args.max_per_host)
    ctx = Context(aoi=aoi, tiger_year=args.tiger_year, cache_dir=args.cache_dir,
                  http_cache=not args.no_http_cache, clip=not args.no_clip)
    sources = catalog.resolve_sources(cat, aoi, args.layers, args.sectors, args.exclude,
                                      include_disabled=args.include_disabled)
    if not sources:
        if aoi.kind == "world":
            print("[!] --aoi world has no buildable sources: the Overpass API cannot serve a planet-wide\n"
                  "    query. Build per country (--aoi country:XX), or switch the OSM sources to the\n"
                  "    osm_pbf driver with a Geofabrik planet/continent extract (see docs/ADDING_A_SOURCE.md).")
        else:
            print("[!] no sources matched. Check --layers/--sectors, or add a catalog entry.")
        return 1
    print(f"[*] {len(sources)} source(s): " + ", ".join(s.get("id", s["layer"]) for s in sources))
    out_dir = args.out if args.flat else os.path.join(args.out, aoi.slug)
    manifest = run_build(ctx, sources, out_dir, args.format, not args.no_combined,
                         precision=args.precision, fail_fast=args.fail_fast,
                         do_reconcile=not args.no_reconcile,
                         use_alternates=not args.no_fallbacks, jobs=args.jobs,
                         group_by=args.group_by)

    # sources and the files written are different things: a source that failed is
    # not a missing pack, and a pack is not a source. Keep them in separate blocks.
    packs = [r for r in manifest["layers"] if str(r.get("id", "")).startswith("SECTOR:")
             or r.get("id") == "ALL"]
    srcs = [r for r in manifest["layers"] if r not in packs]

    print("\n--------------------- SOURCES ---------------------")
    for row in srcs:
        extra = ""
        if row.get("dropped_outside_aoi"):
            extra += f"  -{row['dropped_outside_aoi']} outside AOI"
        if row.get("fallback"):
            extra += "  (fallback endpoint)"
        if row.get("source"):
            extra += f"  [{row['source']}]"
        print(f"  {row.get('doc', row['layer']):28} {str(row['features']):>8} feat   {row['status'][:60]}{extra}")
    if packs:
        print("\n---------------------- PACKS ----------------------")
        for row in packs:
            n = row.get("placemarks", row.get("features", 0))
            src_n = len(row.get("docs") or [])
            note = f"   {src_n} source(s)" if src_n else ""
            print(f"  {row.get('doc', row['layer']):38} {str(n):>8} placemarks{note}")
    print("---------------------------------------------------")
    print(f"Output: {os.path.abspath(out_dir)}  ({manifest['seconds']}s)")
    print("ATAK: Import Manager > Local SD > select .kmz (or drop in atak/imports/).")
    print("Toggle a whole sector by its file; layers and classes with the eye button")
    print("in Overlay Manager. Dense layers start hidden.")
    errs = [r for r in manifest["layers"] if str(r["status"]).startswith("ERROR")]
    return 2 if errs and len(errs) == len(sources) else 0


def cmd_sources(args):
    cat = _catalog_dir(args.catalog)
    aoi = _resolve_aoi(args)
    srcs = catalog.resolve_sources(cat, aoi, args.layers, args.sectors, args.exclude,
                                   include_disabled=args.include_disabled)
    print(f"{len(srcs)} source(s) for {aoi.describe()}:")
    for s in srcs:
        off = "" if s.get("enabled", True) else " [off]"
        print(f"  {s['_tier']:8} {s['layer']:22} {s['driver']:12} {s.get('sector',''):22} "
              f"{s.get('source_name', s.get('url', ''))[:60]}{off}")
    return 0


def cmd_doctor(args):
    """Probe every source an AOI would use; report dead endpoints and alternates."""
    from . import doctor
    cat = _catalog_dir(args.catalog)
    aoi = _resolve_aoi(args)
    configure_http(args.cache_dir, False)          # always hit the network for a health check
    ctx = Context(aoi=aoi, tiger_year=args.tiger_year, cache_dir=args.cache_dir)
    sources = catalog.resolve_sources(cat, aoi, args.layers, args.sectors, args.exclude,
                                      include_disabled=args.include_disabled)
    print(f"[*] probing {len(sources)} source(s) for {aoi.describe()}\n")
    rows = doctor.check_sources(sources, ctx, use_alternates=not args.no_fallbacks)
    c = doctor.summarize(rows)
    print(f"\nok {c['ok']}   warn {c['warn']}   auth-required {c['auth']}   "
          f"dead {c['dead']} ({c['recovered']} recoverable via an alternate)   skipped {c['skip']}")
    out = args.out or "doctor.md"
    doctor.write_report(out, rows, ctx)
    print(f"report: {os.path.abspath(out)}")
    unrecoverable = [r for r in rows if not r["probe"].ok and not r["fallback"]]
    if unrecoverable:
        print("\nNo working endpoint for: " + ", ".join(r["id"] for r in unrecoverable))
        print("Edit those entries in catalog/** (or set `enabled: false`) before relying on a pack.")
        return 1
    return 0


def cmd_demo(args):
    """Build a synthetic sample pack offline, to check ATAK rendering."""
    from .aoi import parse_aoi
    from .demo import PROV_NOTE, build_demo
    out = args.out or "demo"
    aoi = parse_aoi(args.aoi, cache_dir=args.cache_dir) if args.aoi else None
    print(f"[*] building a SYNTHETIC sample pack in {os.path.abspath(out)}")
    print(f"    {PROV_NOTE}\n")
    m = build_demo(out, precision=args.precision, aoi=aoi, group_by=args.group_by)
    # report where it actually landed, which is not always where --aoi asked for
    print(f"\n[*] placed on {m['placement']}")
    print(f"{m['features_total']} features")
    print(f"Output: {os.path.abspath(out)}")
    if m["packs"]:
        print("Load these into ATAK (Import Manager > Local SD) to check the folder tree,")
        print("eye-toggles, icons, voltage styling and popup layout:")
        for f in m["packs"]:
            print(f"    {f}")
    print("Everything here is SYNTHETIC. Delete it before it can be mistaken for real data.")
    return 0


def cmd_probe(args):
    """Inspect an ArcGIS server/layer: list layers, or a layer's fields + count."""
    from .drivers.base import get_json
    url = args.url.rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise SystemExit(f"probe needs an http(s) URL, got {args.url!r}")
    js = get_json(url, cache=False, tries=1, timeout=45)
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
    for f in js.get("fields") or []:          # null on group layers
        print(f"  {f.get('name'):32} {f.get('type', '').replace('esriFieldType', ''):12} {f.get('alias', '')}")
    try:
        cnt = get_json(url + "/query", {"where": "1=1", "returnCountOnly": "true"},
                       cache=False, tries=1, timeout=45)
        print("count:", cnt.get("count"))
        if args.sample:
            smp = get_json(url + "/query", {"where": "1=1", "outFields": "*", "resultRecordCount": 1,
                                           "returnGeometry": "false"}, cache=False, tries=1, timeout=45)
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
    if not srcs:
        print("  ! the catalog is empty - a lint that finds nothing is not a pass")
        return 1
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
    b.add_argument("--group-by", choices=["sector", "layer", "both"], default="sector",
                   help="how to cut up the KMZ files: sector (default) writes one pack per "
                        "sector, named <AOI>_<Sector>.kmz - e.g. MN_Energy-Electric.kmz - which "
                        "is what ATAK's Import Manager can actually handle; layer writes the "
                        "original one-file-per-source layout; both writes both. GeoJSON is "
                        "always per source.")
    b.add_argument("--flat", action="store_true", help="write into --out directly (no AOI subfolder)")
    b.add_argument("--out", default="overlays")
    b.add_argument("--precision", type=int, default=6, help="coordinate decimals (5 ~ 1 m)")
    b.add_argument("--no-clip", action="store_true", help="keep features outside the AOI boundary")
    b.add_argument("--no-http-cache", action="store_true")
    b.add_argument("--no-reconcile", action="store_true")
    b.add_argument("--no-fallbacks", action="store_true",
                   help="do not try a source's catalog `alternates:` when its endpoint fails")
    b.add_argument("--fail-fast", action="store_true",
                   help="abort on the first source that fails; with --jobs, fetches already in "
                        "flight are not interrupted, so exit can lag by one download")
    b.add_argument("--jobs", "-j", type=int, default=1,
                   help="fetch this many sources at once (default 1). Boundary layers always "
                        "run first; per-host concurrency stays capped (--max-per-host)")
    b.add_argument("--max-per-host", type=int, default=2,
                   help="concurrent requests allowed against one server (default 2)")
    b.set_defaults(fn=cmd_build)

    s = sub.add_parser("sources", help="list the sources that would build for an AOI")
    _add_common(s)
    s.set_defaults(fn=cmd_sources)

    p = sub.add_parser("probe", help="inspect an ArcGIS REST service or layer URL")
    p.add_argument("url")
    p.add_argument("--sample", action="store_true", help="print one record's attributes")
    p.set_defaults(fn=cmd_probe)

    d = sub.add_parser("doctor", help="probe every endpoint an AOI would use and report dead ones")
    _add_common(d)
    d.add_argument("--out", help="report path (default doctor.md)")
    d.add_argument("--no-fallbacks", action="store_true", help="do not test catalog alternates")
    d.set_defaults(fn=cmd_doctor)

    dm = sub.add_parser("demo", help="build a synthetic sample pack offline (checks ATAK rendering)")
    dm.add_argument("--out", help="output directory (default ./demo)")
    dm.add_argument("--precision", type=int, default=6)
    dm.add_argument("--aoi", help="place the sample grid inside this AOI's envelope "
                                  "(e.g. state:MN, county:27025) instead of near Chisago County")
    dm.add_argument("--cache-dir", default=".cache")
    dm.add_argument("--group-by", choices=["sector", "layer", "both"], default="sector",
                    help="same as `build --group-by` (default: sector)")
    dm.set_defaults(fn=cmd_demo)

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
    try:
        return args.fn(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001  a CLI should not show a traceback
        if os.environ.get("OVERLAYBUILDER_DEBUG"):
            raise
        print(f"error: {e}", file=sys.stderr)
        print("(set OVERLAYBUILDER_DEBUG=1 for the full traceback)", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
