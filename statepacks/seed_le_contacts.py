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

SOURCES.append({
    "name": "OpenStreetMap (Overpass)",
    "url": "https://overpass-api.de/api/interpreter",
    "layer_re": None,                  # not an ArcGIS service
    "county_field": None,              # spatial match, same as USGS
    "name_field": "name",
    "phone_field": "phone",            # OSM DOES carry phone / contact:phone
    "addr_field": "addr:street",
    "note": "community-maintained; coverage varies by area, but it is the only "
            "reachable source that carries phone numbers at all",
})

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
    ap.add_argument("--source", choices=["auto", "hifld", "usgs", "osm"],
                    default="auto",
                    help="auto tries OSM first (the only reachable source with "
                         "phone numbers), then USGS for names only. hifld/usgs/osm "
                         "force one.")
    ap.add_argument("--allow-partial", action="store_true",
                    help="accept an incomplete OSM result when some tiles fail. "
                         "Off by default: a partial set leaves some counties "
                         "empty for no reason the pack can show.")
    ap.add_argument("--osm-timeout", type=int, default=90,
                    help="per-tile Overpass timeout in seconds (default 90)")
    ap.add_argument("--raw", action="store_true",
                    help="with --show, print the server's response for one record "
                         "exactly as it arrives, before any field mapping")
    ap.add_argument("--discover", metavar="ARCGIS_ROOT",
                    help="list the services and layers an ArcGIS server publishes, "
                         "e.g. https://feat.gisdata.mn.gov/arcgis/rest/services")
    ap.add_argument("--pattern", default="law|police|sheriff|emergency",
                    help="regex filter for --discover (default: law|police|sheriff|emergency)")
    ap.add_argument("--show", type=int, metavar="N",
                    help="dump the first N raw source records and exit - use this "
                         "when the filter matches nothing, to see how the names "
                         "and fields are actually spelled")
    ap.add_argument("--usgs-layer", type=int, default=18,
                    help="USGS Police Stations feature layer (18 or 53; 17 and 52 "
                         "are group layers with no fields)")
    a = ap.parse_args(argv)

    if a.discover:
        print(f"DISCOVERING {a.discover}")
        print(f"  filter: /{a.pattern}/i\n")
        found = discover_arcgis(a.discover, a.pattern)
        return 0 if found else 1

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
    use_osm = a.source == "osm"
    if a.source == "auto":
        # OSM first: it is the only reachable source that carries a phone number,
        # and the phone is the field that has no other home.
        use_osm = True
    if a.source == "hifld":
        try:
            layer_id, vintage = resolve_layer(a.endpoint)
        except Exception as e:                      # noqa: BLE001
            print(f"[!] cannot reach the HIFLD layer: {redact_err(e)}", file=sys.stderr)
            print("    Try:  python3 seed_le_contacts.py --probe", file=sys.stderr)
            return 2

    if a.all:
        targets = sorted(STATE_FIPS)
    elif a.state and a.state.upper() in STATE_FIPS:
        targets = [a.state.upper()]
    else:
        ap.error("give --state XX or --all (or --probe)")

    if a.show:
        st = targets[0]
        if a.raw:
            # Print what the server sends, before any field mapping. The mapped
            # view showed every USGS attribute as '' - which cannot distinguish
            # "the server sent nothing" from "the mapping looked at wrong keys".
            return dump_raw(st, use_osm, use_usgs, a, layer_id)
        try:
            raw = _fetch_raw(st, use_osm, use_usgs, a, layer_id)
        except Exception as e:                      # noqa: BLE001
            print(f"[!] {st}: {redact_err(e)}", file=sys.stderr)
            return 2
        print(f"\n{len(raw)} record(s) from {st}; first {min(a.show, len(raw))}:\n")
        for r in raw[:a.show]:
            for k, v in r.items():
                print(f"    {k:12} {v!r}")
            print()
        names = [str(r.get("name") or r.get("agency") or "") for r in raw]
        hits = [n for n in names if re.search(a.match, n, re.I)]
        withph = sum(1 for r in raw if str(r.get("phone") or "").strip())
        matched_with_phone = sum(
            1 for r in raw
            if re.search(a.match, str(r.get("name") or r.get("agency") or ""), re.I)
            and str(r.get("phone") or "").strip())
        print(f"records with a phone number : {withph} of {len(raw)}")
        print(f"  ...of those matching /{a.match}/i : {matched_with_phone}")
        print(f"names matching /{a.match}/i : {len(hits)} of {len(names)}")
        for n in hits[:10]:
            print(f"    {n}")
        if not hits:
            print(f"    (none - try a different --match, or --all-agencies)")
        # what distinct values do the classifying columns take?
        for key in ("type", "admintype"):
            vals = sorted({str(r.get(key) or "") for r in raw})
            if any(vals):
                print(f"\ndistinct {key}: {', '.join(v or '(blank)' for v in vals[:12])}")
        return 0

    existing, comments = read_existing(CSV_PATH)
    kept = set(existing)
    added = skipped = failures = 0
    for st in targets:
        sfp = STATE_FIPS[st]
        try:
            if use_osm or use_usgs:
                raw = _fetch_raw(st, use_osm, use_usgs, a, layer_id)
                if use_osm:
                    vintage = today_iso()
                    source_name = OSM["name"]
                    withph = sum(1 for r in raw if r.get("phone"))
                    print(f"[*] {st}: {len(raw)} OSM police features, "
                          f"{withph} with a phone number (fetched {vintage})")
                else:
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
                                    "phone": r.get("phone", ""),
                                    "address": r["address"],
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
        if records and not chosen:
            # Data arrived and every record was discarded. That is a filter
            # problem, not an absence of sheriffs, and saying nothing here reads
            # as "this state has none".
            print(f"    [!] {len(records)} record(s) came back but none matched "
                  f"/{a.match}/i.")
            print(f"        See how the names are actually spelled:")
            print(f"          python3 seed_le_contacts.py --state {st} --show 5")
            print(f"        Then widen it, e.g. --match 'sheriff|county' , "
                  f"or take everything with --all-agencies")

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
    if use_osm:
        print("OpenStreetMap is community-maintained: coverage varies by area and")
        print("a phone number there is as current as whoever last edited it. Every")
        print("row says so and carries the date it was fetched. Verify before you")
        print("rely on a number; your own edits are never overwritten.")
    elif use_usgs:
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
            # The layer's own field list advertises NAME, ADDRESS, LOADDATE in
            # upper case, but the geojson response returns them LOWER case. Reading
            # the advertised spelling produced 448 records with every attribute
            # empty and no error anywhere. Match keys case-insensitively and the
            # question never arises again.
            p = {str(k).lower(): v for k, v in (f.get("properties") or {}).items()}
            g = f.get("geometry") or {}
            if g.get("type") != "Point":
                continue
            c = g.get("coordinates") or []
            if len(c) < 2:
                continue
            out.append({
                "name": str(p.get("name") or "").strip(),
                "phone": "",                      # this dataset has no phone column
                "address": str(p.get("address") or "").strip(),
                "city": str(p.get("city") or "").strip(),
                "admintype": str(p.get("admintype") or "").strip(),
                "loaddate": p.get("loaddate") or "",
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




# ---------------------------------------------------------------------------
# OpenStreetMap via Overpass. The only reachable source that carries a phone
# number. Community-maintained, so coverage varies - which is exactly why every
# row records that it came from OSM and when it was fetched.
# ---------------------------------------------------------------------------
OSM = SOURCES[2]
# overpass.osm.jp is deliberately absent: it serves a certificate that is not
# valid for its own hostname, so every request there fails TLS verification.
# Verified on-device 2026-09-14. Keeping it only wastes a retry.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
# A whole state in one query is what the public mirrors time out on - the same
# query returned 511 features when a mirror was not busy, so it is load, not an
# impossible request. Tiling makes each request small enough to get served, and
# a tile that succeeds is CACHED, so a retry only refetches what actually failed
# instead of starting over.
OSM_TILE_COLS = 3
OSM_TILE_ROWS = 3
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "atak-statepacks")

# ISO 3166-2 subdivision codes are what Overpass indexes US states by.
# A bbox query, NOT an area lookup. `area["ISO3166-2"=...]` makes Overpass
# resolve the state boundary first, and both public mirrors answered that with
# HTTP 504 for Minnesota. A bounding box needs no resolution and is cheap.
#
# The box is computed from the TIGERweb county shapes this script already
# downloads for the spatial match, so it is derived from real boundaries rather
# than a table of envelopes typed in from somewhere. Overpass wants
# (south, west, north, east).
OSM_QUERY = """
[out:json][timeout:{timeout}];
nwr["amenity"="police"]({s:.4f},{w:.4f},{n:.4f},{e:.4f});
out center tags;
"""


def bbox_of_shapes(shapes, pad=0.02):
    """(w, s, e, n) around every county in `shapes`, with a small pad.

    Derived from the boundaries actually downloaded, so no envelope is typed in
    from memory. The pad catches a station sitting right on a border; anything
    genuinely outside the state is dropped later by the spatial match anyway.
    """
    xs, ys = [], []
    for _geoid, polys in shapes:
        for rings in polys:
            for ring in rings:
                for pt in ring:
                    xs.append(pt[0])
                    ys.append(pt[1])
    if not xs:
        raise ValueError("no county geometry to derive a bounding box from")
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def tile_bbox(bbox, cols=OSM_TILE_COLS, rows=OSM_TILE_ROWS):
    """Split (w, s, e, n) into cols x rows boxes, left-to-right, bottom-to-top."""
    w, s_, e, n = bbox
    dx = (e - w) / cols
    dy = (n - s_) / rows
    return [(w + i * dx, s_ + j * dy, w + (i + 1) * dx, s_ + (j + 1) * dy)
            for j in range(rows) for i in range(cols)]


def _tile_cache_path(tile):
    key = "_".join(f"{v:.4f}" for v in tile).replace("-", "m").replace(".", "p")
    return os.path.join(CACHE_DIR, f"osm_police_{key}.json")


def _overpass_tile(tile, mirrors, timeout, attempts, log):
    """One tile, cached on disk. Returns its raw elements.

    A tile that has already been fetched is not fetched again: the public
    mirrors time out under load, and without a cache every retry throws away
    the tiles that did work.
    """
    import urllib.parse
    import urllib.request

    path = _tile_cache_path(tile)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh), True
        except (OSError, ValueError):
            pass                         # a corrupt cache file just gets refetched

    w, s_, e, n = tile
    q = OSM_QUERY.format(timeout=timeout, s=s_, w=w, n=n, e=e)
    last = None
    for attempt in range(attempts):
        for url in mirrors:
            try:
                data = urllib.parse.urlencode({"data": q}).encode()
                req = urllib.request.Request(url, data=data, headers=UA)
                with urllib.request.urlopen(req, timeout=timeout + 30,
                                            context=SSL_CTX) as r:
                    doc = json.loads(r.read().decode("utf-8", "replace"))
                els = doc.get("elements") or []
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(els, fh)
                return els, False
            except Exception as ex:                 # noqa: BLE001
                last = ex
        if attempt < attempts - 1:
            wait = 5 * (attempt + 1)
            log(f"      all mirrors busy; waiting {wait}s before retry "
                f"{attempt + 2}/{attempts}")
            time.sleep(wait)
    raise RuntimeError(str(last))


def fetch_osm(state_abbr, mirrors=None, log=print, bbox=None, timeout=90,
              attempts=3, allow_partial=False):
    """[{name, phone, address, city, admintype, lon, lat}] inside `bbox`.

    Queried as a grid of tiles rather than one statewide request, because the
    public mirrors return 504 for the whole-state box under load. Successful
    tiles are cached, so re-running after a failure only refetches the tiles
    that failed.

    If any tile cannot be fetched, this RAISES rather than returning what it
    has: a partial set would put a sheriff in some counties and none in others,
    with nothing in the pack to say which. Pass allow_partial=True to accept an
    incomplete result knowingly.
    """
    if bbox is None:
        bbox = bbox_of_shapes(county_shapes(STATE_FIPS[state_abbr.upper()], log=log))
    mirrors = mirrors or OVERPASS_MIRRORS
    tiles = tile_bbox(bbox)
    elements, failed, cached = [], [], 0
    for i, tile in enumerate(tiles, 1):
        try:
            els, from_cache = _overpass_tile(tile, mirrors, timeout, attempts, log)
            elements.extend(els)
            cached += 1 if from_cache else 0
            log(f"    tile {i}/{len(tiles)}: {len(els)} feature(s)"
                f"{' (cached)' if from_cache else ''}")
        except Exception as ex:                     # noqa: BLE001
            failed.append((i, tile, ex))
            log(f"    tile {i}/{len(tiles)}: FAILED - {redact_err(ex)}")

    if failed:
        msg = (f"{len(failed)} of {len(tiles)} tiles failed. "
               f"{len(tiles) - len(failed)} succeeded and are CACHED, so running "
               f"this again will only refetch the failures - the public mirrors "
               f"are rate-limited, not broken. Wait a minute and retry.")
        if not allow_partial:
            raise RuntimeError(msg + " Use --allow-partial to accept an "
                                     "incomplete result anyway.")
        log(f"    [!] {msg}")
        log(f"    [!] PROCEEDING WITH A PARTIAL RESULT - some counties will have "
            f"no agency purely because their tile failed, not because none exists.")

    if cached:
        log(f"    {cached} of {len(tiles)} tiles came from the local cache")

    # Tiles overlap at their edges and a way can be returned by two of them.
    seen, out = set(), []
    for el in elements:
        key = (el.get("type"), el.get("id"))
        if key in seen:
            continue
        seen.add(key)
        t = el.get("tags") or {}
        lon, lat = el.get("lon"), el.get("lat")
        if lon is None or lat is None:
            c = el.get("center") or {}
            lon, lat = c.get("lon"), c.get("lat")
        if lon is None or lat is None:
            continue
        street = " ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street")) if x)
        out.append({
            "name": (t.get("name") or t.get("official_name") or "").strip(),
            # phone and contact:phone are both in use; neither is preferred by OSM
            "phone": (t.get("phone") or t.get("contact:phone") or "").strip(),
            "address": street.strip(),
            "city": (t.get("addr:city") or "").strip(),
            # operator:type is how OSM records that an agency is county-run
            "admintype": (t.get("operator:type") or t.get("operator") or "").strip(),
            "loaddate": "",
            "lon": float(lon), "lat": float(lat),
        })
    return out


def discover_arcgis(root, pattern="", log=print):
    """List an ArcGIS server's services and their layers, filtered by `pattern`.

    For finding what a STATE GIS server actually publishes without guessing at
    service names. Walks the catalog the server advertises; nothing is assumed
    about what exists.
    """
    rx = re.compile(pattern, re.I) if pattern else None
    try:
        root_doc = http_json(root, {"f": "json"}, tries=2, timeout=60)
    except Exception as e:                          # noqa: BLE001
        log(f"  UNREACHABLE {root}: {redact_err(e)}")
        return []

    folders = root_doc.get("folders") or []
    services = list(root_doc.get("services") or [])
    log(f"  {len(services)} service(s) at the root, {len(folders)} folder(s)")
    for f in folders:
        try:
            sub = http_json(f"{root}/{f}", {"f": "json"}, tries=1, timeout=60)
            services.extend(sub.get("services") or [])
        except Exception as e:                      # noqa: BLE001
            log(f"  folder {f}: {redact_err(e)}")
    log(f"  {len(services)} service(s) total\n")

    found = []
    checked = 0
    for svc in services:
        nm, typ = svc.get("name", ""), svc.get("type", "")
        if typ not in ("MapServer", "FeatureServer"):
            continue
        # Do NOT skip a service because its own name does not match. A service
        # called "mn_structures" can hold a law-enforcement LAYER, and filtering
        # at the service level means never looking inside it. The filter applies
        # to the service name OR any of its layer names.
        checked += 1
        url = f"{root}/{nm}/{typ}"
        try:
            meta = http_json(url, {"f": "json"}, tries=1, timeout=45)
        except Exception as e:                      # noqa: BLE001
            log(f"  {nm} ({typ}): unreadable - {redact_err(e)}")
            continue
        layers = meta.get("layers") or []
        svc_hit = bool(rx and rx.search(nm))
        hits = [l for l in layers if rx and rx.search(str(l.get("name", "")))]
        if rx and not svc_hit and not hits:
            continue                    # nothing in this service matches; stay quiet
        log(f"  {nm} ({typ}) - {len(layers)} layer(s)")
        for l in layers:
            hit = rx and rx.search(str(l.get("name", "")))
            mark = "  <-- MATCH" if hit else ""
            log(f"      {str(l.get('id')):>3}  {l.get('name')}{mark}")
            if hit or not rx:
                found.append((url, l.get("id"), l.get("name")))
    log(f"\n  inspected {checked} service(s); {len(found)} matching layer(s)")
    if not found:
        log("  Nothing matched the filter. To see everything this server has:")
        log(f"    python3 seed_le_contacts.py --discover {root} --pattern ''")
    return found


def today_iso():
    """Fetch date, for sources that carry no vintage of their own.

    OSM has no dataset vintage - it is edited continuously - so the honest
    stamp is when this copy was taken, not a year invented for it.
    """
    import datetime as _dt
    return _dt.date.today().isoformat()


def _fetch_raw(state_abbr, use_osm, use_usgs, args, layer_id):
    """Whichever source is selected, in one place, returning one record shape."""
    if use_osm:
        return fetch_osm(state_abbr, allow_partial=args.allow_partial,
                         timeout=args.osm_timeout)
    if use_usgs:
        return fetch_usgs(state_abbr, args.usgs_layer)
    return fetch_state(STATE_FIPS[state_abbr], layer_id, args.endpoint)




def dump_raw(state_abbr, use_osm, use_usgs, args, layer_id):
    """Print the server's own response for a few records, unmapped.

    When every mapped field comes back empty, the mapping and the response are
    indistinguishable from the outside. This shows which it is.
    """
    import urllib.parse
    import urllib.request
    if use_osm:
        shapes = county_shapes(STATE_FIPS[state_abbr.upper()])
        w, s_, e, n = bbox_of_shapes(shapes)
        q = OSM_QUERY.format(timeout=180, s=s_, w=w, n=n, e=e)
        print(f"QUERY:\n{q}")
        for url in OVERPASS_MIRRORS:
            try:
                data = urllib.parse.urlencode({"data": q}).encode()
                req = urllib.request.Request(url, data=data, headers=UA)
                with urllib.request.urlopen(req, timeout=210, context=SSL_CTX) as r:
                    doc = json.loads(r.read().decode("utf-8", "replace"))
                els = doc.get("elements") or []
                print(f"{url.split('/')[2]}: {len(els)} element(s)\n")
                for el in els[:args.show]:
                    print(json.dumps(el, indent=2)[:1500])
                    print()
                return 0
            except Exception as ex:                 # noqa: BLE001
                print(f"  [!] {url.split('/')[2]} -> {redact_err(ex)}")
        return 2

    base = USGS["url"] if use_usgs else HIFLD_LE
    lid = args.usgs_layer if use_usgs else layer_id
    where = (f"STATE='{state_abbr}'" if use_usgs
             else f"COUNTYFIPS LIKE '{STATE_FIPS[state_abbr]}%'")
    for fmt, fields in (("geojson", "NAME,ADDRESS,CITY,STATE,ADMINTYPE,FCODE,LOADDATE"),
                        ("geojson", "*"),
                        ("json", "*")):
        url = f"{base}/{lid}/query"
        params = {"where": where, "outFields": fields, "returnGeometry": "true",
                  "outSR": "4326", "f": fmt, "resultRecordCount": args.show}
        print(f"\n--- f={fmt}  outFields={fields} ---")
        print(f"    {redact_err(url + '?' + urllib.parse.urlencode(params))}")
        try:
            doc = http_json(url, params, tries=1, timeout=60)
        except Exception as ex:                     # noqa: BLE001
            print(f"    FAILED: {redact_err(ex)}")
            continue
        if "error" in doc:
            print(f"    server error: {doc['error']}")
            continue
        items = doc.get("features") or []
        print(f"    {len(items)} feature(s)")
        for it in items[:args.show]:
            print(json.dumps(it, indent=2)[:1200])
    print("\nCompare the three: if outFields=* returns populated attributes and the")
    print("explicit field list does not, the server is not honouring outFields and")
    print("the fix is to request * and map client-side.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
