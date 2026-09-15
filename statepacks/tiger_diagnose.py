#!/usr/bin/env python3
"""
tiger_diagnose.py - work out exactly which TIGERweb layer to build from.

WHY THIS EXISTS
---------------
The TIGERweb State_County MapServer holds SEVERAL VINTAGES side by side -
"Current", "ACS 2025", "Census 2020", "BAS 2026" and so on - each with its own
States and Counties layer. A flat listing shows "Counties" a dozen times and
tells you nothing about which one you would actually be querying.

This prints the hierarchy (which group each layer sits under), then for every
Counties-looking layer it runs a real query for one state and reports the row
count and the field names. That is enough to pick a layer and know what you are
getting.

Standard library only. Run it on the device that will do the building.

USAGE
-----
    python3 tiger_diagnose.py                 # default service, Minnesota
    python3 tiger_diagnose.py --state TX
    python3 tiger_diagnose.py --service https://.../SomeOther/MapServer
"""
import argparse
import json
import ssl
import sys
import urllib.parse
import urllib.request

SERVICE = ("https://tigerweb.geo.census.gov/arcgis/rest/services"
           "/TIGERweb/State_County/MapServer")
UA = {"User-Agent": "atak-statepacks-diagnose/1.0"}
CTX = ssl.create_default_context()

STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15",
    "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56", "PR": "72",
}
# How many counties each state really has, so a row count can be judged rather
# than just reported. (Census county-equivalents, 2020 vintage.)
EXPECTED = {"MN": 87, "TX": 254, "CA": 58, "WI": 72, "IA": 99, "ND": 53,
            "SD": 66, "MI": 83, "IL": 102, "AK": 29, "LA": 64, "VA": 133,
            "DC": 1, "PR": 78}


def get(url, params=None, timeout=90):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find the right TIGERweb county layer.")
    ap.add_argument("--service", default=SERVICE)
    ap.add_argument("--state", default="MN")
    ap.add_argument("--detail", action="store_true",
                    help="also fetch one county's geometry from each candidate and "
                         "count vertices, to compare generalization levels")
    a = ap.parse_args(argv)
    st = a.state.upper()
    if st not in STATE_FIPS:
        print(f"unknown state {a.state!r}", file=sys.stderr)
        return 1
    sfp = STATE_FIPS[st]

    print(f"SERVICE  {a.service}")
    try:
        info = get(a.service, {"f": "json"})
    except Exception as e:                          # noqa: BLE001
        print(f"  UNREACHABLE: {e}")
        return 2

    desc = (info.get("serviceDescription") or info.get("description") or "").strip()
    print(f"  mapName: {info.get('mapName')!r}")
    if desc:
        print(f"  description: {desc[:300]}")
    layers = info.get("layers") or []
    byid = {l["id"]: l for l in layers}
    print(f"  {len(layers)} layers\n")

    # ---- hierarchy: which group does each layer belong to? -----------------
    def group_of(l):
        """Walk up parentLayerId to the top-level group name."""
        seen, cur = set(), l
        while True:
            p = cur.get("parentLayerId", -1)
            if p is None or p < 0 or p in seen or p not in byid:
                return cur["name"] if cur is not l else "(top level)"
            seen.add(p)
            cur = byid[p]

    print("LAYER TREE")
    for l in layers:
        kind = "GROUP" if l.get("subLayerIds") else "     "
        pid = l.get("parentLayerId", -1)
        par = byid[pid]["name"] if pid in byid else "-"
        print(f"  {l['id']:>3}  {kind}  {str(l['name'])[:26]:<26}  under: {par}")

    # ---- test every Counties-looking layer ---------------------------------
    cands = [l for l in layers
             if "count" in str(l.get("name", "")).lower() and not l.get("subLayerIds")]
    print(f"\nTESTING {len(cands)} county layer(s) with a real query "
          f"for {st} (expect {EXPECTED.get(st, '?')} counties)\n")

    results = []
    for l in cands:
        lid = l["id"]
        grp = group_of(l)
        base = f"{a.service}/{lid}"
        line = f"  layer {lid:>3}  (under {grp})"
        try:
            cnt = get(f"{base}/query", {
                "where": f"STATE='{sfp}'", "returnCountOnly": "true", "f": "json"})
            n = cnt.get("count")
            if "error" in cnt:
                print(f"{line}  WHERE STATE='{sfp}' rejected: "
                      f"{cnt['error'].get('message')}")
                continue
        except Exception as e:                      # noqa: BLE001
            print(f"{line}  count failed: {e}")
            continue

        try:
            samp = get(f"{base}/query", {
                "where": f"STATE='{sfp}'",
                "outFields": "*", "returnGeometry": "false",
                "f": "json", "resultRecordCount": 1})
            fields = [f["name"] for f in (samp.get("fields") or [])]
            attrs = (samp.get("features") or [{}])[0].get("attributes", {})
        except Exception as e:                      # noqa: BLE001
            print(f"{line}  {n} rows, sample failed: {e}")
            continue

        exp = EXPECTED.get(st)
        verdict = "OK" if (exp is None or n == exp) else f"!! expected {exp}"
        print(f"{line}  {n} rows  {verdict}")
        print(f"       fields: {', '.join(fields[:14])}"
              f"{' ...' if len(fields) > 14 else ''}")
        have = [k for k in ("GEOID", "NAME", "BASENAME", "AREALAND", "AREAWATER",
                            "STATE", "COUNTY") if k in fields]
        missing = [k for k in ("GEOID", "NAME", "AREALAND", "AREAWATER") if k not in fields]
        print(f"       builder needs: have {have}"
              + (f"  MISSING {missing}" if missing else ""))
        if attrs:
            show = {k: attrs.get(k) for k in ("GEOID", "NAME", "BASENAME",
                                              "AREALAND", "AREAWATER") if k in attrs}
            print(f"       sample: {show}")
        results.append((lid, grp, n, not missing))
        print()

    # ---- optional: how much boundary detail does each layer actually carry? --
    if a.detail and results:
        print("=" * 62)
        print("BOUNDARY DETAIL (vertices in one county's outline)\n")
        print("  A map service repeats the same counties at several")
        print("  GENERALIZATION levels for different zoom scales. More vertices")
        print("  means a more faithful outline - which is what you want on a")
        print("  tactical map, at the cost of file size.\n")
        probe_geoid = f"{sfp}001"
        rows = []
        for lid, grp, n, _ok in results:
            try:
                d = get(f"{a.service}/{lid}/query", {
                    "where": f"GEOID='{probe_geoid}'",
                    "outFields": "NAME", "returnGeometry": "true",
                    "outSR": "4326", "f": "geojson"})
                feats = d.get("features") or []
                if not feats:
                    print(f"  layer {lid:>3}  (no feature {probe_geoid})")
                    continue
                g = feats[0].get("geometry") or {}
                c = g.get("coordinates") or []
                if g.get("type") == "Polygon":
                    rings = [r for r in c if r]
                elif g.get("type") == "MultiPolygon":
                    rings = [r for poly in c for r in poly if r]
                else:
                    rings = []
                verts = sum(len(r) for r in rings)
                nm = (feats[0].get("properties") or {}).get("NAME", "?")
                rows.append((verts, lid, grp, len(rings), nm))
            except Exception as e:                  # noqa: BLE001
                print(f"  layer {lid:>3}  detail check failed: {e}")
        for verts, lid, grp, nrings, nm in sorted(rows, reverse=True):
            print(f"  layer {lid:>3}  {verts:>6} vertices  {nrings} ring(s)  "
                  f"{nm}  (under {grp})")
        if rows:
            best = max(rows)
            print(f"\n  MOST DETAILED: layer {best[1]} ({best[0]} vertices, "
                  f"under {best[2]})")
            print(f"  Use --endpoint {a.service}/{best[1]} for the sharpest outlines.")
        print()

    good = [r for r in results if r[3] and (EXPECTED.get(st) in (None, r[2]))]
    print("=" * 62)
    if good:
        lid, grp, n, _ = good[0]
        print(f"USE THIS:  --endpoint {a.service}/{lid}")
        print(f"           ({n} {st} counties, group: {grp})")
        if len(good) > 1:
            print(f"other usable layers: {', '.join(str(r[0]) for r in good[1:])}")
    else:
        print("No layer returned the expected county count with the fields the")
        print("builder needs. Paste this whole output back and we will pick one.")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
