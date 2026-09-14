#!/usr/bin/env python3
"""
seed_le_contacts.py - populate data/le_contacts.csv from HIFLD.

WHY THIS EXISTS
---------------
build_county_pack.py will not invent a sheriff's office or a phone number, so
the LE columns ship empty. This fills them from the one public dataset that
actually carries agency name, address AND telephone joined to a county FIPS
code: HIFLD "Local Law Enforcement Locations", derived from DOJ BJS.

READ THIS BEFORE YOU TRUST A NUMBER
-----------------------------------
HIFLD Open was shut down in August 2025. What this queries is a FROZEN FINAL
SNAPSHOT re-hosted by NASA NCCS - nobody is maintaining it any more. Phone
numbers drift: agencies consolidate, dispatch moves to a regional PSAP, a
non-emergency line gets renumbered.

So every row this writes carries the snapshot's vintage, and the popup shows it:

    LE non-emergency: 651-555-0100  [HIFLD LE Locations (frozen snapshot) 2025]

A number with a year on it can be judged. Treat these as a starting point to
verify, not as verified. When you confirm one, edit the row and put your own
source and the current year in it - the builder shows whatever is there.

WHAT IT WRITES
--------------
One row per county, for agencies whose name looks like a county sheriff
(--match, default "sheriff"). Use --all-agencies to take every law-enforcement
record instead and pick through them by hand.

USAGE
-----
    python3 seed_le_contacts.py --probe            # find the layer, check it answers
    python3 seed_le_contacts.py --state MN
    python3 seed_le_contacts.py --state MN --dry-run
    python3 seed_le_contacts.py --all
    python3 seed_le_contacts.py --state MN --all-agencies

Existing rows are preserved unless --overwrite is given: a row you verified by
hand is never silently replaced by a stale snapshot value.
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Candidate sources, tried in order. The first that answers wins, and the row
# records WHICH one - a pack must never attribute a value to a source that did
# not supply it.
#
# They are not equivalent. HIFLD carries a phone number and a county FIPS, so a
# sheriff joins to a county directly. USGS carries neither phone nor FIPS, but
# it is live and maintained, and a sourced agency NAME with an empty phone is
# still worth more than nothing - the phone simply stays "not in dataset".
SOURCES = [
    {
        "name": "HIFLD LE Locations (frozen snapshot)",
        "url": ("https://maps.nccs.nasa.gov/mapping/rest/services"
                "/hifld_open/law_enforcement/FeatureServer"),
        "layer_re": re.compile(r"^local_law_enforcement", re.I),
        "county_field": "COUNTYFIPS",
        "name_field": "NAME",
        "phone_field": "TELEPHONE",
        "addr_field": "ADDRESS",
        "note": "frozen Aug 2025, unofficial re-host; carries phone numbers",
    },
    {
        "name": "USGS National Map Structures",
        "url": "https://carto.nationalmap.gov/arcgis/rest/services/structures/MapServer",
        "layer_re": re.compile(r"police\s*stations", re.I),
        # Probed on-device 2026-09-14. Layers 17 and 52 are GROUP layers with no
        # fields; 18 and 53 are the Feature Layers and carry:
        #   NAME ADDRESS CITY STATE ZIPCODE ADMINTYPE FCODE LOADDATE ...
        # No phone column and no county FIPS, so the county comes from a spatial
        # match against the same boundaries the builder already downloads.
        "county_field": None,
        "name_field": "NAME",
        "phone_field": None,
        "addr_field": "ADDRESS",
        "note": "live and maintained; agency names and addresses, NO phone numbers, "
                "county assigned by spatial match",
    },
]

# Kept for backwards compatibility with anything importing the old names.
HIFLD_LE = SOURCES[0]["url"]
LAYER_NAME_RE = SOURCES[0]["layer_re"]
SOURCE = SOURCES[0]["name"]
VINTAGE_UNKNOWN = "snapshot year not reported"
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

_SECRET_PARAM = re.compile(r"([?&](?:key|api_key|token)=)[^&\s]+", re.I)


def redact_err(text):
    """Strip credentials out of anything printed - these URLs get pasted around."""
    return _SECRET_PARAM.sub(r"\1<redacted>", str(text))


SSL_CTX = ssl.create_default_context()
UA = {"User-Agent": "atak-statepacks-le/1.0 "
                    "(+https://github.com/NordicDevelopment-org/ATAK-Overlays)"}

HERE = os.path.dirname(os.path.abspath(__file__))
# Written beside the shipped template, NOT over it - see fetch_county_seats.py
CSV_PATH = os.path.join(HERE, "data", "le_contacts.local.csv")
FIELDS = ["geoid", "agency", "phone", "source", "vintage"]

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


def http_json(url, params=None, tries=3, timeout=90):
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    last = None
    for i in range(tries):
        try:
            with urlopen(Request(url, headers=UA), timeout=timeout, context=SSL_CTX) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except (URLError, HTTPError, TimeoutError, OSError, ValueError) as e:
            last = e
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed: {url}\n  {last}")


def probe_sources(log=print):
    """Report every candidate source: reachable? which layer? which fields?

    Prints what is actually there rather than pass/fail, because the useful
    question when one is down is "what does the next one give me".
    """
    any_ok = False
    for src in SOURCES:
        log(f"\n{src['name']}")
        log(f"  {src['url']}")
        log(f"  ({src['note']})")
        try:
            info = http_json(src["url"], {"f": "json"}, tries=1, timeout=30)
        except Exception as e:                      # noqa: BLE001
            log(f"  UNREACHABLE: {e}")
            continue
        layers = info.get("layers") or []
        hits = [l for l in layers if src["layer_re"].search(str(l.get("name") or ""))]
        log(f"  reachable - {len(layers)} layers, {len(hits)} matching")
        for l in hits[:6]:
            log(f"    {l.get('id'):>3}  {l.get('name')}")
        if not hits:
            names = ", ".join(str(l.get("name")) for l in layers[:12])
            log(f"    no match; layers present: {names}")
            continue

        # Examine EVERY match, not just the first. A group layer carries no
        # fields of its own, so stopping at hits[0] reports "MISSING" for
        # columns that are sitting right there on the feature layer below it.
        for hit in hits:
            lid = hit["id"]
            try:
                meta = http_json(f"{src['url']}/{lid}", {"f": "json"}, tries=1,
                                 timeout=30)
            except Exception as e:                  # noqa: BLE001
                log(f"    layer {lid} ({hit.get('name')}): unreadable - {e}")
                continue
            fields = [f["name"] for f in (meta.get("fields") or [])]
            kind = meta.get("type") or ("Group Layer" if hit.get("subLayerIds") else "?")
            if not fields:
                log(f"    layer {lid:>3} {str(hit.get('name'))[:22]:<22} "
                    f"{kind} - no fields of its own, skipping")
                continue
            log(f"    layer {lid:>3} {str(hit.get('name'))[:22]:<22} {kind}")
            log(f"        fields: {', '.join(fields[:18])}"
                f"{' ...' if len(fields) > 18 else ''}")
            # name the column that would serve each role, whatever it is called
            def pick(cands):
                for c in cands:
                    for f in fields:
                        if f.upper() == c:
                            return f
                return None
            county = pick(["COUNTYFIPS", "COUNTY_FIPS", "FIPS", "COUNTY"])
            nm = pick(["NAME", "FACILITYNAME", "FACILITY_NAME", "AGENCYNAME"])
            ph = pick(["TELEPHONE", "PHONE", "PHONENUMBER", "PHONE_NUMBER"])
            log(f"        county join: {county or 'NONE - needs a spatial match'}")
            log(f"        agency name: {nm or 'NONE'}")
            log(f"        phone:       {ph or 'NONE - phone stays not in dataset'}")
            if nm:
                any_ok = True
    return any_ok


def resolve_layer(base=HIFLD_LE, log=print):
    """(layer_id, vintage). The layer is found BY NAME, not by a hardcoded index.

    A re-host can renumber its layers; matching the name the dataset actually
    uses survives that, and fails loudly rather than silently querying whatever
    happens to sit at index 0.
    """
    info = http_json(base, {"f": "json"})
    layers = info.get("layers") or []
    hit = next((l for l in layers if LAYER_NAME_RE.match(str(l.get("name") or ""))), None)
    if hit is None:
        names = ", ".join(str(l.get("name")) for l in layers) or "(none)"
        raise RuntimeError(
            f"no layer matching /^local_law_enforcement/i at {base}\n"
            f"  layers present: {names}")
    vintage = VINTAGE_UNKNOWN
    for key in ("description", "serviceDescription", "copyrightText"):
        m = _YEAR_RE.search(str(info.get(key) or ""))
        if m:
            vintage = m.group(0)
            break
    log(f"[*] layer {hit['id']} = {hit.get('name')!r}   vintage: {vintage}")
    return hit["id"], vintage


def fetch_state(sfp, layer_id, base=HIFLD_LE, log=print):
    """[{geoid, agency, phone, address, type}] for one state, all agencies."""
    out, offset = [], 0
    while True:
        page = http_json(f"{base}/{layer_id}/query", {
            "where": f"COUNTYFIPS LIKE '{sfp}%'",
            "outFields": "NAME,TELEPHONE,ADDRESS,TYPE,COUNTYFIPS",
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": 1000,
        })
        if "error" in page:
            raise RuntimeError(page["error"])
        feats = page.get("features") or []
        for f in feats:
            a = f.get("attributes") or {}
            geoid = str(a.get("COUNTYFIPS") or "").strip()
            if len(geoid) != 5 or not geoid.isdigit() or not geoid.startswith(sfp):
                continue                      # never file a record under a county it is not in
            out.append({
                "geoid": geoid,
                "agency": str(a.get("NAME") or "").strip(),
                "phone": str(a.get("TELEPHONE") or "").strip(),
                "address": str(a.get("ADDRESS") or "").strip(),
                "type": str(a.get("TYPE") or "").strip(),
            })
        if not feats or not page.get("exceededTransferLimit"):
            break
        offset += len(feats)
        if offset > 50000:
            log("    [!] stopping after 50k records")
            break
    return out


def pick_sheriffs(records, match="sheriff"):
    """One agency per county: the best sheriff-looking match.

    A county has many law-enforcement records (city PDs, campus police, a
    sheriff's office). Only the sheriff is the county's primary LE, so filter
    by name and keep the first per county, preferring one with a phone number.
    """
    rx = re.compile(match, re.I)
    by_county = {}
    for r in records:
        if not rx.search(r["agency"]):
            continue
        cur = by_county.get(r["geoid"])
        # prefer a record that actually has a phone number
        if cur is None or (not cur["phone"] and r["phone"]):
            by_county[r["geoid"]] = r
    return by_county


def read_existing(path):
    """({geoid: row}, comment_lines). Comments are preserved across rewrites."""
    if not os.path.exists(path):
        return {}, []
    with open(path, newline="", encoding="utf-8") as fh:
        lines = fh.readlines()
    comments = [l for l in lines if l.lstrip().startswith("#")]
    body = [l for l in lines if not l.lstrip().startswith("#")]
    rows = {}
    for row in csv.DictReader(body):
        g = (row.get("geoid") or "").strip()
        if g:
            rows[g] = row
    return rows, comments


def write_csv(path, rows, comments):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.writelines(comments)
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for g in sorted(rows):
            w.writerow({k: rows[g].get(k, "") for k in FIELDS})


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Seed data/le_contacts.csv from the frozen HIFLD LE snapshot.")
    ap.add_argument("--state", help="two-letter abbreviation, e.g. MN")
    ap.add_argument("--all", action="store_true", help="every state")
    ap.add_argument("--probe", action="store_true", help="resolve the layer and exit")
    ap.add_argument("--match", default="sheriff",
                    help="regex an agency name must match (default: sheriff)")
    ap.add_argument("--all-agencies", action="store_true",
                    help="write every LE record, not just sheriffs (one per county, first wins)")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace rows that are already in the CSV (default: keep yours)")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--endpoint", default=HIFLD_LE, help="override the FeatureServer URL")
    ap.add_argument("--source", choices=["auto", "hifld", "usgs"], default="auto",
                    help="auto (default) tries HIFLD for phone numbers and falls "
                         "back to USGS for names only; hifld or usgs force one")
    ap.add_argument("--usgs-layer", type=int, default=18,
                    help="USGS Police Stations feature layer (18 or 53; 17 and 52 "
                         "are group layers with no fields)")
    a = ap.parse_args(argv)

    if a.probe:
        ok = probe_sources()
        print()
        if not ok:
            print("No law-enforcement source answered. The LE columns stay empty,")
            print("which is correct - an unsourced phone number is worse than a")
            print("blank one, because it fails at the moment someone dials it.")
        return 0 if ok else 2

    layer_id = vintage = None
    use_usgs = a.source == "usgs"
    if a.source in ("auto", "hifld"):
        try:
            layer_id, vintage = resolve_layer(a.endpoint)
        except Exception as e:                      # noqa: BLE001
            if a.source == "hifld":
                print(f"[!] cannot reach the HIFLD layer: {e}", file=sys.stderr)
                print("    Try:  python3 seed_le_contacts.py --probe", file=sys.stderr)
                return 2
            print(f"[*] HIFLD unavailable ({str(e).splitlines()[0]})")
            print("[*] falling back to USGS: agency names and addresses, NO phone "
                  "numbers - the phone column stays 'not in dataset'")
            use_usgs = True

    if a.all:
        targets = sorted(STATE_FIPS)
    elif a.state and a.state.upper() in STATE_FIPS:
        targets = [a.state.upper()]
    else:
        ap.error("give --state XX or --all (or --probe)")

    existing, comments = read_existing(CSV_PATH)
    kept = set(existing)
    added = skipped = failures = 0
    for st in targets:
        sfp = STATE_FIPS[st]
        try:
            if use_usgs:
                raw = fetch_usgs(st, a.usgs_layer)
                vintage = usgs_vintage(raw)
                source_name = f"{USGS['name']} (no phone numbers)"
                print(f"[*] {st}: {len(raw)} USGS law-enforcement points, "
                      f"vintage {vintage}")
                shapes = county_shapes(sfp)
                records, unplaced = [], 0
                for r in raw:
                    geoid = assign_county(r["lon"], r["lat"], shapes)
                    if not geoid:
                        unplaced += 1       # never guessed into a nearby county
                        continue
                    records.append({"geoid": geoid, "agency": r["name"],
                                    "phone": "", "address": r["address"],
                                    "type": r["admintype"]})
                if unplaced:
                    print(f"    {unplaced} point(s) fell outside every county "
                          f"boundary and were dropped, not guessed")
            else:
                records = fetch_state(sfp, layer_id, a.endpoint)
                source_name = SOURCE
        except Exception as e:                      # noqa: BLE001
            failures += 1
            print(f"[!] {st}: {redact_err(e)}", file=sys.stderr)
            continue
        if a.all_agencies:
            chosen = {}
            for r in records:
                chosen.setdefault(r["geoid"], r)
        else:
            chosen = pick_sheriffs(records, a.match)
        for geoid, r in sorted(chosen.items()):
            if geoid in kept and not a.overwrite:
                skipped += 1
                continue
            existing[geoid] = {"geoid": geoid, "agency": r["agency"],
                               "phone": r["phone"], "source": source_name,
                               "vintage": vintage}
            added += 1
        withphone = sum(1 for r in chosen.values() if r["phone"])
        print(f"[*] {st}: {len(records)} LE records -> {len(chosen)} counties "
              f"({withphone} with a phone number)")

    if a.dry_run:
        for g in sorted(existing):
            r = existing[g]
            print(f"  {g}  {r.get('agency','')[:44]:46} {r.get('phone','')}")
        print(f"\n(dry run - {added} new row(s) would be written, "
              f"{skipped} existing kept)")
        return 0

    if failures and not added:
        # Every source refused. Say so and exit non-zero: writing an empty file
        # and reporting success would read as "there are no sheriffs", which is
        # a claim we cannot make.
        print(f"\n[!] no source answered for {failures} state(s); nothing written.",
              file=sys.stderr)
        print("    The LE columns stay empty, which is correct - an unsourced "
              "phone number is worse than a blank one.", file=sys.stderr)
        print("    Try:  python3 seed_le_contacts.py --probe", file=sys.stderr)
        return 2

    write_csv(CSV_PATH, existing, comments)
    print(f"\n{added} row(s) written, {skipped} existing row(s) kept; "
          f"{len(existing)} total in {CSV_PATH}")
    print(f"Source stamped on new rows: {source_name} {vintage}")
    if use_usgs:
        print("USGS carries NO phone numbers, so LE non-emergency stays 'not in")
        print("dataset'. Add one you have verified yourself and the seeder will")
        print("never overwrite it.")
    else:
        print("These are a FROZEN snapshot. Verify any number before you rely on it,")
        print("then edit the row and put your own source and year in it.")
    return 0




# ---------------------------------------------------------------------------
# USGS path: agency names with no county column, so the county is worked out
# from geometry against the same boundaries the pack is built from.
# ---------------------------------------------------------------------------
USGS = SOURCES[1]


def point_in_ring(x, y, ring):
    """Ray-casting point-in-polygon. Ring is [[lon, lat], ...]."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            denom = (yj - yi)
            if denom and x < (xj - xi) * (y - yi) / denom + xi:
                inside = not inside
        j = i
    return inside


def point_in_polygon(x, y, rings):
    """First ring is the outer boundary, the rest are holes.

    A point inside a hole is NOT inside the polygon - a lake or an enclave
    carved out of a county belongs to whatever is inside it, not the county.
    """
    if not rings or not point_in_ring(x, y, rings[0]):
        return False
    return not any(point_in_ring(x, y, h) for h in rings[1:])


def assign_county(x, y, counties):
    """The GEOID of the county containing (x, y), or None.

    `counties` is [(geoid, [polygon, ...])] where each polygon is a list of
    rings. A point outside every county returns None and the record is DROPPED,
    never filed under a nearest guess - a sheriff's office attributed to the
    wrong county is worse than one that is simply absent.
    """
    for geoid, polys in counties:
        for rings in polys:
            if point_in_polygon(x, y, rings):
                return geoid
    return None


def county_shapes(state_fips, log=print):
    """[(geoid, [rings, ...])] for one state, from the same TIGERweb layer the
    builder uses - so an agency lands in the county the pack actually draws."""
    import build_county_pack as bcp                      # noqa: PLC0415
    feats, url, vintage = bcp.fetch_counties(state_fips, log=log)
    out = []
    for f in feats:
        geoid = bcp.county_geoid(f.get("properties"))
        geom = f.get("geometry") or {}
        if not geoid or not geom:
            continue
        c = geom.get("coordinates") or []
        if geom.get("type") == "Polygon":
            polys = [[r for r in c if r]]
        elif geom.get("type") == "MultiPolygon":
            polys = [[r for r in poly if r] for poly in c]
        else:
            continue
        out.append((geoid, polys))
    log(f"    {len(out)} county shapes for the spatial match")
    return out


def fetch_usgs(state_abbr, layer_id, log=print):
    """[{name, address, lon, lat, admintype, loaddate}] for one state."""
    out, offset = [], 0
    while True:
        page = http_json(f"{USGS['url']}/{layer_id}/query", {
            "where": f"STATE='{state_abbr}'",
            "outFields": "NAME,ADDRESS,CITY,STATE,ADMINTYPE,FCODE,LOADDATE",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": 1000,
        })
        if "error" in page:
            raise RuntimeError(page["error"])
        feats = page.get("features") or []
        for f in feats:
            p = f.get("properties") or {}
            g = f.get("geometry") or {}
            if g.get("type") != "Point":
                continue
            c = g.get("coordinates") or []
            if len(c) < 2:
                continue
            out.append({
                "name": str(p.get("NAME") or "").strip(),
                "address": str(p.get("ADDRESS") or "").strip(),
                "city": str(p.get("CITY") or "").strip(),
                "admintype": str(p.get("ADMINTYPE") or "").strip(),
                "loaddate": str(p.get("LOADDATE") or "").strip(),
                "lon": float(c[0]), "lat": float(c[1]),
            })
        if not feats or not page.get("exceededTransferLimit"):
            break
        offset += len(feats)
        if offset > 50000:
            log("    [!] stopping after 50k records")
            break
    return out


def usgs_vintage(records):
    """The newest LOADDATE in the batch, as a year - a real per-record vintage.

    Falls back to a plain label rather than inventing a year.
    """
    import datetime as _dt
    years = []
    for r in records:
        ld = r.get("loaddate")
        if not ld:
            continue
        try:                        # ArcGIS dates come back as epoch milliseconds
            years.append(_dt.datetime.fromtimestamp(
                int(ld) / 1000, _dt.timezone.utc).year)
        except (TypeError, ValueError, OSError, OverflowError):
            m = re.search(r"\b(19|20)\d{2}\b", str(ld))
            if m:
                years.append(int(m.group(0)))
    return str(max(years)) if years else "load date not reported"


if __name__ == "__main__":
    sys.exit(main())
