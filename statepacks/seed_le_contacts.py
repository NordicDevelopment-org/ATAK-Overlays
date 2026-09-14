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
(--match, default "sherr?iff"). Use --all-agencies to take every law-enforcement
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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "atak-statepacks")
# County boundaries change once a year and are only used here to decide which
# county a station falls in. Downloading 87 polygons twice per run (once for the
# bounding box, once for the spatial match) was most of the wall clock.
SHAPES_TTL_DAYS = 30

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


# OSM spells it "Sherriff" in at least one place - "Steele County Sherriff's
# Office and Detention Center", found by the gap report on 2026-09-14. That is
# the same word misspelled by whoever typed it, not a different agency, so the
# default filter tolerates the doubled r. The name is still written out exactly
# as the source has it.
SHERIFF_RX = r"sherr?iff"

# Facility names that are COUNTY-level law enforcement under another word.
# Each was read off a real gap report, never guessed at: Minnesota files
# sheriffs under "<County> Law Enforcement Center", "<County> Jail" and
# "<County> Public Safety Center". They are offered as widenings, with a count
# of how many counties each would actually reach, rather than switched on.
COUNTY_LE_HINTS = ("law enforcement cent", "county jail", "county public safety",
                   "justice cent", "county detention")


def _rank(rec):
    """How good a match is, most significant first. Higher wins.

    Only two preferences, both stated rather than inferred:
      * a name that actually says "sheriff" beats one that does not. With a
        widened --match a county can match on several records, and "Becker
        County Jail" must not outrank "Becker County Sheriff" merely by being
        returned first.
      * a record carrying a phone number beats one that does not, since the
        number is the part that cannot be looked up anywhere else.
    Ties keep the first record seen, which is tile order - deterministic.
    """
    return (1 if re.search(SHERIFF_RX, rec["agency"], re.I) else 0,
            1 if rec.get("phone") else 0)


def pick_sheriffs(records, match=SHERIFF_RX):
    """One agency per county: the best matching record.

    A county has many law-enforcement records (city PDs, campus police, a
    sheriff's office, a jail). Filter by name, then keep the best one per
    county by _rank. The agency is written EXACTLY as the source spells it -
    nothing is relabelled into "X County Sheriff" because it looked like one.
    """
    rx = re.compile(match, re.I)
    by_county = {}
    for r in records:
        if not rx.search(r["agency"]):
            continue
        cur = by_county.get(r["geoid"])
        if cur is None or _rank(r) > _rank(cur):
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
    ap.add_argument("--match", default=SHERIFF_RX,
                    help=f"regex an agency name must match (default: "
                         f"{SHERIFF_RX} - the doubled r is deliberate, the "
                         f"source misspells it in places). Run --gaps to see "
                         f"what a wider one would reach.")
    ap.add_argument("--all-agencies", action="store_true",
                    help="write every LE record, not just sheriffs (one per county, first wins)")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace rows that are already in the CSV (default: keep yours)")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--gaps", action="store_true",
                    help="after writing, report per county WHY it has no "
                         "sheriff: no record in the source at all, or records "
                         "whose names the filter did not match (with their "
                         "actual spellings)")
    ap.add_argument("--gaps-dump", metavar="FILE",
                    help="with --gaps, also write EVERY unmatched county and "
                         "all its agency names to FILE as JSON. The printed "
                         "report samples 20 counties to stay readable; on a "
                         "state nobody has fetched before, the names it does "
                         "not print are the evidence that decides the filter")
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
    ap.add_argument("--osm-timeout", type=int, default=OSM_SERVER_TIMEOUT_S,
                    help=f"seconds the Overpass SERVER is given per tile "
                         f"(default {OSM_SERVER_TIMEOUT_S}); the socket allows "
                         f"{OSM_SOCKET_SLACK}s more for the transfer. A tile "
                         f"that times out on "
                         f"{OSM_TIMEOUTS_BEFORE_SPLIT} mirrors is retried as "
                         f"four quarters, not asked for again unchanged.")
    ap.add_argument("--jobs", type=int, default=OSM_JOBS,
                    help=f"tiles fetched at once, one per mirror (default "
                         f"{OSM_JOBS}). Raising it past the mirror count does "
                         f"nothing: a mirror only takes one of our requests at "
                         f"a time.")
    ap.add_argument("--no-split", action="store_true",
                    help="do not retry a failed tile as quarters")
    ap.add_argument("--refresh-shapes", action="store_true",
                    help=f"redownload the county boundaries used for the "
                         f"spatial match instead of reusing the cached copy "
                         f"(cache expires after {SHAPES_TTL_DAYS} days)")
    ap.add_argument("--deadline", type=int, default=OSM_DEADLINE_S,
                    help=f"wall-clock budget in seconds for one STATE's Overpass "
                         f"fetch, tiles and retries included (default "
                         f"{OSM_DEADLINE_S}); --all gets this budget per state, "
                         f"not in total. Tiles already fetched are cached, so "
                         f"re-running resumes rather than starting over.")
    ap.add_argument("--osm-attempts", type=int, default=1,
                    help="how many times to cycle the Overpass mirrors per tile "
                         "(default 1). Asking a busy mirror the same large "
                         "question again is what made a stuck tile cost "
                         "minutes; splitting it is the retry.")
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
        # Bound OUTSIDE the branch: --gaps reads them unconditionally, and the
        # hifld path never entered the branch that used to define them - so
        # `--source hifld --gaps` died with a NameError after a successful
        # fetch, throwing the whole run away at the last step.
        shapes, cnames, coverage = [], {}, {}
        try:
            if use_osm or use_usgs:
                # Fetched ONCE and handed to both the bounding box and the
                # spatial match. It used to be downloaded twice per run.
                try:
                    shapes, cnames = county_shapes(sfp, refresh=a.refresh_shapes,
                                                   with_names=True)
                except Exception as ex:             # noqa: BLE001
                    # Say which thing failed. Without this it reads as "no LE
                    # source answered", which would be the wrong diagnosis.
                    raise RuntimeError(
                        f"county boundaries could not be fetched, so no agency "
                        f"can be placed in a county: {redact_err(ex)}") from ex
                raw = _fetch_raw(st, use_osm, use_usgs, a, layer_id, shapes=shapes,
                                 gaps_out=coverage)
                if use_osm:
                    # The date the DATA was fetched, which for a cached tile is
                    # not today. Rows carry their own tile's date; this is only
                    # the fallback and the headline.
                    dates = sorted({r.get("loaddate") for r in raw
                                    if r.get("loaddate")})
                    vintage = dates[0] if dates else today_iso()
                    source_name = OSM["name"]
                    withph = sum(1 for r in raw if r.get("phone"))
                    span = (f"fetched {vintage}" if len(dates) < 2
                            else f"fetched {dates[0]} to {dates[-1]}")
                    print(f"[*] {st}: {len(raw)} OSM police features, "
                          f"{withph} with a phone number ({span})")
                else:
                    vintage = usgs_vintage(raw)
                    source_name = f"{USGS['name']} (no phone numbers)"
                    print(f"[*] {st}: {len(raw)} USGS law-enforcement points, "
                          f"vintage {vintage}")
                records, unplaced = [], 0
                for r in raw:
                    geoid = assign_county(r["lon"], r["lat"], shapes)
                    if not geoid:
                        unplaced += 1       # never guessed into a nearby county
                        continue
                    records.append({"geoid": geoid, "agency": r["name"],
                                    "phone": r.get("phone", ""),
                                    "website": r.get("website", ""),
                                    "address": r["address"],
                                    "type": r["admintype"],
                                    # this record's own tile date, not the
                                    # date the CSV happens to be written
                                    "vintage": r.get("loaddate", "")})
                if unplaced:
                    # The Overpass box is a RECTANGLE around the state, so it
                    # necessarily covers slices of the neighbours. Those points
                    # are real, they are just not this state's.
                    print(f"    {unplaced} point(s) fell outside every {st} "
                          f"county and were dropped, not guessed - the query "
                          f"box is a rectangle, so it reaches into the "
                          f"neighbouring states")
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
                               # this row's own fetch date where it has one
                               "vintage": r.get("vintage") or vintage}
            added += 1
        withphone = sum(1 for r in chosen.values() if r["phone"])
        print(f"[*] {st}: {len(records)} LE records -> {len(chosen)} counties "
              f"({withphone} with a phone number)")
        if a.gaps:
            if shapes:
                report_gaps(st, [g for g, _polys in shapes], cnames, records,
                            chosen, a.match,
                            uncovered=coverage.get("uncovered") or (),
                            dump=a.gaps_dump)
            else:
                print("    --gaps needs the county boundaries, which only the "
                      "osm and usgs sources fetch; nothing to report here")
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


def _shapes_cache_path(state_fips):
    return os.path.join(CACHE_DIR, f"county_shapes_{state_fips}.json")


def county_shapes(state_fips, log=print, refresh=False, ttl_days=SHAPES_TTL_DAYS,
                  with_names=False):
    """[(geoid, [rings, ...])] for one state, from the same TIGERweb layer the
    builder uses - so an agency lands in the county the pack actually draws.

    with_names=True returns (shapes, {geoid: name}) instead. The names ride
    along in the same download and the same cache - a gap report that says
    "27007" instead of "Beltrami County" is a gap report nobody reads.

    Cached on disk for `ttl_days`. This was being downloaded TWICE per run -
    once for the Overpass bounding box, once for the spatial match - and 87
    county polygons is the single largest download the seeder makes. The cache
    is only ever used to decide which county a point falls in; the pack's own
    boundaries are always fetched fresh by the builder, so a month-old copy
    here cannot put a stale boundary in an overlay. --refresh-shapes forces a
    redownload.
    """
    path = _shapes_cache_path(state_fips)
    if not refresh:
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            age = (dt.date.today() - dt.date.fromisoformat(doc["fetched"])).days
            shapes = doc["shapes"]
            # An older cache has no "names" key at all. Serving that as "this
            # state has no county names" is a wrong answer dressed as a cache
            # hit; refetch instead. An empty dict that IS present is a real
            # answer and is kept, so this cannot loop.
            if with_names and "names" not in doc:
                raise KeyError("names")
            if 0 <= age <= ttl_days and shapes:
                log(f"    {len(shapes)} county shapes from the local cache "
                    f"(fetched {doc['fetched']}; --refresh-shapes to redownload)")
                out = [(g, polys) for g, polys in shapes]
                # A cache written before names were stored has none; that is a
                # missing name, not a wrong one, so it stays missing.
                return (out, dict(doc.get("names") or {})) if with_names else out
        except (OSError, ValueError, KeyError, TypeError):
            pass                         # missing, stale or corrupt: refetch
    import build_county_pack as bcp                      # noqa: PLC0415
    feats, url, vintage = bcp.fetch_counties(state_fips, log=log)
    out, names = [], {}
    for f in feats:
        props = f.get("properties") or {}
        geoid = bcp.county_geoid(props)
        geom = f.get("geometry") or {}
        if not geoid or not geom:
            continue
        label = bcp.county_label(props)
        if label:
            names[geoid] = label
        c = geom.get("coordinates") or []
        if geom.get("type") == "Polygon":
            polys = [[r for r in c if r]]
        elif geom.get("type") == "MultiPolygon":
            polys = [[r for r in poly if r] for poly in c]
        else:
            continue
        out.append((geoid, polys))
    log(f"    {len(out)} county shapes for the spatial match")
    if out:
        try:
            _write_json_atomic(path, {"fetched": dt.date.today().isoformat(),
                                      "state": state_fips, "source": url,
                                      "vintage": vintage, "shapes": out,
                                      "names": names})
        except OSError as ex:            # a cache that cannot be written is
            log(f"    (could not cache county shapes: "     # not an error
                f"{redact_err(ex)})")
    return (out, names) if with_names else out


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
# A WALL-CLOCK BUDGET for the whole fetch, not just per request. Nine tiles,
# three attempts, three mirrors and a 120s socket multiply out to 2.7 hours of
# a command that looks hung - which is exactly what it did. Nothing waits past
# the deadline; what was fetched is cached, so the next run resumes.
# A COLD run is nine queries that each take the better part of a minute, plus
# whatever the mirrors are doing that day. 480s was cutting real work off; a
# warm run is instant either way, so the budget only has to be generous enough
# not to fail a run that is working.
OSM_DEADLINE_S = 600
# Tiles are fetched CONCURRENTLY, one in-flight request per mirror. Nine tiles
# one after another is nine round trips of waiting; three at a time across
# three mirrors is three. The cap is per mirror on purpose - the public
# instances ask you not to run several queries at once against one of them, and
# queueing behind yourself is not faster anyway.
OSM_JOBS = 3
# A tile that fails gets SPLIT into quarters and those are fetched, rather than
# the same box being asked for again. Whether the mirror timed out because the
# box was heavy or because it was busy, a quarter of the box is a strictly
# cheaper question - and three of the four quarters usually come back.
# The Overpass query carries its OWN timeout ([timeout:N]) - that is the server
# giving up and answering, which is fast and legible. The socket gets that plus
# slack for the transfer, so we never cut off a server that is about to reply.
OSM_SOCKET_SLACK = 15
# MEASURED, not chosen. Every one of Minnesota's nine tiles is served at 90s.
# At 30s - picked to make a failure arrive sooner - five of the nine time out
# instead, and each of those five then fans out into four more requests, which
# is how a run that used to fetch 517 features earned an HTTP 429. A timeout
# that turns successes into failures is not a faster failure, it is a slower
# one with extra steps. Lower it only against evidence from a real run.
OSM_SERVER_TIMEOUT_S = 90
# A tile that TIMES OUT on two different mirrors is a tile that is too much to
# ask for, not two unlucky mirrors. Stop there and split it. Waiting for the
# third mirror to also time out buys no information and costs a whole timeout.
# A fast failure (504, connection refused) is a mirror problem, not a size
# problem, so it does not count towards this.
OSM_TIMEOUTS_BEFORE_SPLIT = 2
# A cached tile is dated and expires. Without this a months-old cache was
# served silently while every row it produced was stamped with today's date -
# a fetch date invented for data that was not fetched today.
OSM_CACHE_TTL_DAYS = 30
# How long a mirror is left alone after it says 429. Overpass sends Retry-After
# sometimes; when it does not, this is the fallback. A mirror that rate-limits
# ONE tile is rate-limiting the whole run, so the cooldown is shared - without
# it the other eight tiles queue up behind the same refusal.
OSM_COOLDOWN_S = 20
# Rate limiting is the one failure where WAITING is the right answer, so it
# gets its own retry budget. Every other failure is retried by asking a
# different mirror or a smaller box, neither of which helps here.
OSM_RATE_LIMIT_PASSES = 3

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


def _element_key(el):
    """The source's own identity for an element, as a sortable key.

    OSM ids are integers, so this sorts numerically - which is the order
    Overpass itself returns, meaning a whole-tile fetch is already in canonical
    order and the sort changes nothing. A non-integer id (nothing in OSM, but
    the type is not ours to promise) still sorts deterministically rather than
    raising on a mixed comparison.
    """
    i = el.get("id")
    return (str(el.get("type") or ""),
            i if isinstance(i, int) else 0,
            "" if isinstance(i, int) else str(i))


def bboxes_of_shapes(shapes, pad=0.02):
    """[(w, s, e, n)] around every county in `shapes` - normally one box.

    Derived from the boundaries actually downloaded, so no envelope is typed in
    from memory. The pad catches a station sitting right on a border; anything
    genuinely outside the state is dropped later by the spatial match anyway.

    TWO boxes when the geometry straddles the antimeridian. min/max longitude
    is not a bounding box for a state with points on both sides of it: Alaska's
    Aleutian islands sit near +172 and the rest of the state near -130, so
    min/max spans 302 degrees - a single Overpass query for most of the
    northern hemisphere. The condition is detected from the coordinates
    themselves, never from a list of which states are supposed to cross, so it
    is right whichever data actually does.
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
    s_ = max(-90.0, min(ys) - pad)
    n = min(90.0, max(ys) + pad)

    def box(lons):
        return (max(-180.0, min(lons) - pad), s_,
                min(180.0, max(lons) + pad), n)

    if max(xs) - min(xs) <= 180:
        return [box(xs)]
    west = [x for x in xs if x < 0]
    east = [x for x in xs if x >= 0]
    if not west or not east:                 # cannot actually happen, but a
        return [box(xs)]                     # bare min/max is the honest answer
    return [box(west), box(east)]


def bbox_of_shapes(shapes, pad=0.02):
    """The single box around `shapes`.

    Raises when the geometry needs two, rather than returning one of them and
    silently dropping half the state. Callers that can handle both use
    bboxes_of_shapes.
    """
    boxes = bboxes_of_shapes(shapes, pad)
    if len(boxes) != 1:
        raise ValueError(
            "this geometry straddles the antimeridian and needs two bounding "
            "boxes; use bboxes_of_shapes")
    return boxes[0]


def tile_bbox(bbox, cols=OSM_TILE_COLS, rows=OSM_TILE_ROWS):
    """Split (w, s, e, n) into cols x rows boxes, left-to-right, bottom-to-top."""
    w, s_, e, n = bbox
    dx = (e - w) / cols
    dy = (n - s_) / rows
    return [(w + i * dx, s_ + j * dy, w + (i + 1) * dx, s_ + (j + 1) * dy)
            for j in range(rows) for i in range(cols)]


def split_tile(tile):
    """The four quarters of a tile, in a stable order.

    Used as the retry for a tile the mirrors would not serve. Sub-tiles are
    cached under their own keys, and the parent grid is untouched, so an
    existing cache stays valid.
    """
    w, s_, e, n = tile
    mx, my = (w + e) / 2.0, (s_ + n) / 2.0
    return [(w, s_, mx, my), (mx, s_, e, my),
            (w, my, mx, n), (mx, my, e, n)]


def counties_in_tile(shapes, tile):
    """geoids whose extent overlaps `tile` - i.e. who a failed tile costs.

    Bounding-box overlap, deliberately generous: this names who MIGHT be
    affected so a partial result can say so out loud. Nothing is written from
    it, so over-reporting costs nothing and under-reporting would hide a gap.
    """
    w, s_, e, n = tile
    out = []
    for geoid, polys in shapes:
        xs = [p[0] for rings in polys for ring in rings for p in ring]
        ys = [p[1] for rings in polys for ring in rings for p in ring]
        if not xs:
            continue
        if min(xs) <= e and max(xs) >= w and min(ys) <= n and max(ys) >= s_:
            out.append(geoid)
    return sorted(out)


def _tile_cache_path(tile):
    key = "_".join(f"{v:.4f}" for v in tile).replace("-", "m").replace(".", "p")
    return os.path.join(CACHE_DIR, f"osm_police_{key}.json")


class Deadline:
    """A shared wall-clock budget. Every request checks it before starting.

    Without one, a per-request timeout is meaningless: nine tiles times three
    attempts times three mirrors is eighty-one requests, and even a modest
    socket timeout multiplies into hours of silence.
    """

    def __init__(self, seconds):
        self.limit = seconds
        self.start = time.monotonic()

    def left(self):
        return self.limit - (time.monotonic() - self.start)

    def expired(self):
        return self.left() <= 0

    def elapsed(self):
        return time.monotonic() - self.start


class RateLimited(RuntimeError):
    """A mirror told us to slow down (HTTP 429, or 509 on some instances).

    Distinct from every other failure because the correct response is the
    opposite one. Splitting the tile would turn one refused request into four
    against a server that just said there were too many; asking another mirror
    immediately is what got us rate-limited. The only thing that helps is
    waiting, so this is never split and never counted as evidence about the
    size of the box.
    """

    def __init__(self, msg, retry_after=None):
        super().__init__(msg)
        self.retry_after = retry_after


def _retry_after(ex):
    """The server's own Retry-After in seconds, or None.

    Honoured when it is there rather than guessing a back-off over the top of
    it - the server knows when it will serve us again and we do not.
    """
    hdrs = getattr(ex, "headers", None)
    raw = None
    if hdrs is not None:
        try:
            raw = hdrs.get("Retry-After")
        except Exception:                           # noqa: BLE001
            raw = None
    if not raw:
        return None
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return None                      # an HTTP-date form; not worth parsing


def _as_rate_limit(ex):
    """RateLimited when `ex` is a rate-limit refusal, else None."""
    code = getattr(ex, "code", None)
    # 429 is the standard one; some Overpass instances answer 509 Bandwidth
    # Limit Exceeded instead. Both mean the same thing to us.
    if code in (429, 509):
        return RateLimited(f"HTTP {code}: {getattr(ex, 'reason', 'rate limited')}",
                           retry_after=_retry_after(ex))
    return None


def _is_timeout(ex):
    """True when a request ran out of time, as opposed to being refused.

    urllib wraps a socket timeout in URLError, and since 3.10 socket.timeout is
    TimeoutError - so both shapes have to be checked. The distinction matters:
    a timeout says the question was too big, a refusal says the mirror is busy.
    """
    return isinstance(ex, TimeoutError) or isinstance(
        getattr(ex, "reason", None), TimeoutError)


class MirrorsBusy(RuntimeError):
    """Nothing was asked: our own other tiles were holding every mirror.

    Distinct from a server failure because it means the opposite thing. There
    is no evidence the box is too big, so splitting it is wasted work, and
    telling the user the mirrors are rate-limiting them is false - the mirrors
    were never asked.
    """


def _cache_fresh(fetched, ttl_days):
    """Is a cache entry dated `fetched` still inside its TTL?

    An entry with no date is from before tiles carried one. It is not trusted:
    its age cannot be established, and an unknown age must not pass as a fresh
    one.
    """
    if not fetched:
        return False
    try:
        age = (dt.date.today() - dt.date.fromisoformat(fetched)).days
    except ValueError:
        return False
    return 0 <= age <= ttl_days


def _read_bounded(resp, deadline, chunk=65536):
    """Read a response body in chunks, checking the budget between them.

    A socket timeout is a per-read inactivity timer, not a transfer cap: every
    byte that arrives resets it, so a mirror trickling one byte at a time never
    trips it and the wall-clock budget would not bound the run at all. Reading
    in chunks gives the deadline somewhere to be enforced.
    """
    out = []
    while True:
        if deadline is not None and deadline.expired():
            raise TimeoutError("deadline reached while reading the response")
        b = resp.read(chunk)
        if not b:
            return b"".join(out)
        out.append(b)


def _overpass_error(doc):
    """The error inside an HTTP 200, or None.

    Overpass does NOT signal a server-side timeout with an HTTP error. It
    answers 200 with a normal-looking JSON body carrying a "remark" like
    `runtime error: Query timed out in "query" at line 3 after 30 seconds.`
    and an elements list that is empty or truncated. Taking that at face value
    caches "this part of the state has no police stations" forever - a missing
    answer stored as a negative one, which is the exact failure this project
    exists to avoid. Lowering the server timeout to 30s made it likelier, so it
    has to be detected, not hoped about.
    """
    remark = str(doc.get("remark") or doc.get("error") or "").strip()
    if not remark:
        return None
    if re.search(r"tim(ed|e)[ -]?out|timeout", remark, re.I):
        return TimeoutError(f"Overpass: {remark}")
    if re.search(r"error|exceed|abort|refus", remark, re.I):
        return RuntimeError(f"Overpass: {remark}")
    return None                          # a purely informational remark


def _cache_read(path):
    """(elements, fetched_iso) from a tile cache file, or None.

    Accepts the older bare-list payload as well, reporting its date as unknown
    rather than inventing one.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None                      # corrupt or unreadable: just refetch
    if isinstance(doc, list):
        return doc, ""                   # written before tiles carried a date
    if isinstance(doc, dict) and isinstance(doc.get("elements"), list):
        return doc["elements"], str(doc.get("fetched") or "")
    return None


def _overpass_tile(tile, mirrors, timeout, attempts, log, deadline=None,
                   locks=None, start=0, ttl_days=OSM_CACHE_TTL_DAYS,
                   cooldowns=None):
    """One tile. Returns (elements, came_from_cache, fetched_iso).

    A tile that has already been fetched is not fetched again: the public
    mirrors time out under load, and without a cache every retry throws away
    the tiles that did work. The cache entry carries the date it was fetched,
    because the row written from it is stamped with that date - not with the
    date the CSV happened to be written.

    Mirrors are tried in an order ROTATED by `start`, and `locks` holds each
    mirror to one in-flight request at a time. Within one pass every mirror is
    first tried WITHOUT waiting; only if all of them are busy with our own
    other tiles does it wait for one. Reporting a tile as failed because we
    were busy ourselves would blame the mirrors for our own scheduling.

    `cooldowns` is shared across tiles: a mirror that answers 429 is left alone
    until its Retry-After (or OSM_COOLDOWN_S) has passed, so one tile's refusal
    is not re-earned by the other eight.
    """
    import urllib.parse
    import urllib.request

    hit = _cache_read(_tile_cache_path(tile))
    if hit and _cache_fresh(hit[1], ttl_days):
        return hit[0], True, hit[1]
    if hit and not hit[1]:
        # Say why a run that was instant yesterday is doing work today.
        log("      (this tile was cached before tiles carried a fetch date; "
            "refetching once so its rows can be stamped with a date they have)")

    # A tile that was served as quarters last time is still fully cached - just
    # under four keys instead of one. Without this the parent gets asked for
    # again on every run, and it is exactly the tile the mirrors would not
    # serve, so every run pays for it. Edge duplicates are dropped downstream.
    quarters = [_cache_read(_tile_cache_path(x)) for x in split_tile(tile)]
    if all(quarters) and all(_cache_fresh(h[1], ttl_days) for h in quarters):
        els = [e for h in quarters for e in h[0]]
        dates = [h[1] for h in quarters if h[1]]
        return els, True, (min(dates) if dates else "")

    if not mirrors:
        raise RuntimeError("no Overpass mirror to ask")
    w, s_, e, n = tile
    q = OSM_QUERY.format(timeout=timeout, s=s_, w=w, n=n, e=e)
    order = list(mirrors)
    k = start % len(order)
    order = order[k:] + order[:k]
    state = {"last": None, "timeouts": 0, "limited": 0, "wait": 0}
    cooldowns = {} if cooldowns is None else cooldowns

    def cool(url):
        """Seconds this mirror still wants to be left alone."""
        return max(0.0, cooldowns.get(url, 0.0) - time.monotonic())

    def ask(url):
        """Returns elements, or None having recorded why not."""
        # The server is told to give up at `timeout`; the socket allows that
        # plus slack to send the answer back.
        want = timeout + OSM_SOCKET_SLACK
        sock = (max(5, min(want, int(deadline.left()))) if deadline is not None
                else want)
        try:
            data = urllib.parse.urlencode({"data": q}).encode()
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=sock, context=SSL_CTX) as r:
                doc = json.loads(
                    _read_bounded(r, deadline).decode("utf-8", "replace"))
            bad = _overpass_error(doc)
            if bad is not None:
                raise bad
            return doc.get("elements") or []
        except Exception as ex:                     # noqa: BLE001
            limited = _as_rate_limit(ex)
            if limited is not None:
                # Do not ask this mirror again until it says it is ready. This
                # is shared with every other tile in the run.
                pause = limited.retry_after or OSM_COOLDOWN_S
                cooldowns[url] = time.monotonic() + pause
                state["limited"] += 1
                state["wait"] = max(state["wait"], pause)
                state["last"] = limited
                return None
            state["last"] = ex
            if _is_timeout(ex):
                state["timeouts"] += 1
            return None

    # Ordinary failures get `attempts` passes; a pass that ended in nothing but
    # rate limiting gets extra ones, because waiting is the only thing that
    # helps it and a different mirror or a smaller box does not.
    for _attempt in range(max(1, attempts) + OSM_RATE_LIMIT_PASSES):
        if _attempt >= max(1, attempts):
            if not state["limited"]:
                break                    # not a rate-limit problem; stop here
            pause = state["wait"] or OSM_COOLDOWN_S
            if deadline is not None and deadline.left() < pause + 5:
                break                    # no budget to wait it out
            log(f"      every mirror is rate-limiting; waiting {pause:.0f}s "
                f"(they asked, and asking again sooner only makes it worse)")
            time.sleep(pause)
            # The pause they asked for has now been served, so stop treating
            # those mirrors as cooling. Only the ones this wait actually
            # covered - another tile may have earned a longer one.
            now = time.monotonic()
            for u in [u for u, until in cooldowns.items() if until - now <= pause]:
                cooldowns.pop(u, None)
            state["limited"], state["wait"] = 0, 0
        # Pass 1: every mirror that is free right now. Pass 2: wait for one,
        # but only for the mirrors we skipped - never re-ask one that answered
        # with a failure, since that is the same question again.
        busy = []
        for phase in (0, 1):
            # A SNAPSHOT. Phase 1 walks what phase 0 put aside, and appending
            # to the list being iterated spins: the loop keeps finding the
            # entry it just added and burns the budget doing nothing.
            for url in (order if phase == 0 else list(busy)):
                if deadline is not None:
                    if deadline.expired():
                        raise TimeoutError("deadline reached")
                if cool(url) > 0:
                    # It asked to be left alone. Honour that rather than
                    # spending the budget re-earning the same refusal.
                    if phase == 0:
                        busy.append(url)
                    continue
                lock = (locks or {}).get(url)
                if lock is not None:
                    if phase == 0:
                        if not lock.acquire(blocking=False):
                            busy.append(url)
                            continue
                    else:
                        wait = (30 if deadline is None
                                else max(1, min(30, deadline.left())))
                        if not lock.acquire(timeout=wait):
                            continue
                        if cool(url) > 0:           # started cooling while we waited
                            lock.release()
                            continue
                try:
                    els = ask(url)
                finally:
                    if lock is not None:
                        lock.release()
                if els is not None:
                    # The cache write is OUTSIDE the request's error handling
                    # on purpose. An unwritable ~/.cache used to discard a
                    # response that had already been fetched correctly and
                    # report it as a mirror failure - losing real data, and
                    # blaming the wrong thing for it.
                    fetched = today_iso()
                    try:
                        _write_cache(_tile_cache_path(tile), els, fetched)
                    except OSError as ex:
                        log(f"      (fetched, but could not cache this tile: "
                            f"{redact_err(ex)})")
                    return els, False, fetched
                if state["timeouts"] >= OSM_TIMEOUTS_BEFORE_SPLIT:
                    # Two mirrors could not answer this box in time. A third
                    # will not tell us anything new; the caller splits it.
                    raise RuntimeError(
                        f"timed out on {state['timeouts']} mirrors at "
                        f"{timeout}s: {redact_err(state['last'])}"
                    ) from state["last"]

    if state["last"] is None:
        # Nothing was ever asked: every mirror was serving one of our own other
        # tiles for the whole wait. That is our scheduling, not a mirror
        # failure, and it must not be reported as one or split as if the box
        # were too big.
        raise MirrorsBusy("every mirror was busy with this run's other tiles")
    if isinstance(state["last"], RateLimited):
        raise state["last"]              # kept as RateLimited: never split
    # EVERY server failure leaves here as a RuntimeError, deliberately. The
    # only TimeoutError this function raises is the shared deadline, above -
    # which is what lets the caller tell "out of budget" (do not split, do not
    # retry) apart from "this box was too much" (split it) with an isinstance.
    raise RuntimeError(redact_err(state["last"])) from state["last"]


def _write_json_atomic(path, payload):
    """Write a cache entry atomically: temp file, then rename.

    Tiles are fetched in parallel and a run can be interrupted mid-write. A
    half-written file would be read back as a tile with no police stations in
    it - indistinguishable from a county that genuinely has none.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _write_cache(path, els, fetched=None):
    """One tile's elements and the date they were fetched, atomically.

    The date is the point: the CSV row built from this tile is stamped with
    it, so a row can never claim a fetch date the data does not have.
    """
    _write_json_atomic(path, {"fetched": fetched or today_iso(),
                              "elements": els})


def _run_tiles(items, mirrors, timeout, attempts, log, clock, jobs, locks,
               cooldowns=None):
    """Fetch `items` ([(key, tile)]) concurrently. Returns (ok, bad).

    ok  = {key: (elements, from_cache, fetched_iso)}
    bad = {key: exception}

    Results are keyed, never appended, so the caller can reassemble them in
    tile order. Completion order depends on which mirror answers first, and a
    non-deterministic record order would make `pick_sheriffs` pick a different
    agency from one run to the next.
    """
    ok, bad = {}, {}
    speak = threading.Lock()

    def say(msg):
        with speak:
            log(msg)

    def work(i, key, tile):
        return _overpass_tile(tile, mirrors, timeout, attempts, say,
                              deadline=clock, locks=locks, start=i,
                              cooldowns=cooldowns)

    pool = ThreadPoolExecutor(max_workers=max(1, jobs))
    try:
        futures = {}
        for i, (key, tile) in enumerate(items):
            futures[pool.submit(work, i, key, tile)] = (key, tile)
        # Report as they land, not in submission order: waiting on tile 1 to
        # print tile 2 is how a fetch that is working looks stalled.
        for fut in as_completed(futures):
            key, tile = futures[fut]
            try:
                ok[key] = fut.result()
            except Exception as ex:                 # noqa: BLE001
                bad[key] = ex
                if isinstance(ex, TimeoutError):
                    say(f"    {key}: OUT OF TIME after {clock.elapsed():.0f}s")
                else:
                    say(f"    {key}: FAILED - {redact_err(ex)}")
                continue
            els, from_cache, _fetched = ok[key]
            say(f"    {key}: {len(els)} feature(s)"
                f"{' (cached)' if from_cache else ''}"
                f"   [{clock.elapsed():.0f}s used, {max(0, clock.left()):.0f}s left]")
    except BaseException:
        # Ctrl-C must actually stop. Without cancel_futures the pool drains
        # every queued tile on the way out, so the interrupt appears to hang
        # and the mirrors get hit for work nobody is waiting for any more.
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return ok, bad


def fetch_osm(state_abbr, mirrors=None, log=print, bbox=None,
              timeout=OSM_SERVER_TIMEOUT_S,
              attempts=1, allow_partial=False, deadline_s=OSM_DEADLINE_S,
              jobs=OSM_JOBS, shapes=None, split=True, gaps_out=None):
    """[{name, phone, address, city, admintype, lon, lat}] inside `bbox`.

    Queried as a grid of tiles rather than one statewide request, because the
    public mirrors return 504 for the whole-state box under load. Tiles are
    fetched CONCURRENTLY (one in-flight request per mirror), every tile that
    lands is CACHED, and a tile the mirrors will not serve is SPLIT into
    quarters and retried small instead of asked for again unchanged.

    The whole fetch runs against ONE wall-clock budget (`deadline_s`), per
    state - `--all` gets this budget for each state, not in total. It stops new
    requests, and the response body is read in chunks with the budget checked
    between them, so a mirror that trickles bytes cannot outlive it either. The
    overrun is bounded by one chunk read, not by `timeout`.

    If any tile is still missing at the end, this RAISES rather than returning
    what it has: a partial set would put a sheriff in some counties and none in
    others, with nothing in the pack to say which. Pass allow_partial=True to
    accept an incomplete result knowingly - the counties it costs are named.
    """
    if bbox is None:
        if shapes is None:
            shapes = county_shapes(STATE_FIPS[state_abbr.upper()], log=log)
        boxes = bboxes_of_shapes(shapes)
    else:
        boxes = [bbox]
    if len(boxes) > 1:
        log(f"    this state straddles the antimeridian: {len(boxes)} bounding "
            f"boxes, not one")
    mirrors = mirrors or OVERPASS_MIRRORS
    tiles = [t for b in boxes for t in tile_bbox(b)]
    clock = Deadline(deadline_s)
    locks = {u: threading.Lock() for u in mirrors}
    # Shared across every tile: one tile's 429 protects the other eight.
    cooldowns = {}
    log(f"    {len(tiles)} tile(s), {min(jobs, len(mirrors))} at a time, "
        f"{deadline_s}s budget for all of them (cached tiles are free)")

    items = [(f"tile {i}/{len(tiles)}", t) for i, t in enumerate(tiles, 1)]
    ok, bad = _run_tiles(items, mirrors, timeout, attempts, log, clock, jobs,
                         locks, cooldowns)

    # A tile the mirrors would not serve is retried SMALLER, not again. Only
    # real failures are split - running out of budget is not a tile the mirrors
    # refused, and splitting it would just spend a budget that is already gone.
    parts = {}
    # Split only what a SERVER refused ON ITS MERITS. Running out of budget is
    # not evidence the box is too big; neither is our own scheduling holding
    # the mirrors; and a 429 is the opposite of evidence - splitting one
    # refused request into four against a server that just said "too many"
    # makes the problem it is reporting worse.
    retry = [(k, t) for k, t in items
             if k in bad and not isinstance(bad[k], (TimeoutError, MirrorsBusy,
                                                     RateLimited))]
    if split and retry and not clock.expired():
        log(f"    retrying {len(retry)} failed tile(s) as quarters - a smaller "
            f"box is a cheaper question than the same one again")
        sub = [(f"{k} q{j}", q)
               for k, t in retry for j, q in enumerate(split_tile(t), 1)]
        sok, sbad = _run_tiles(sub, mirrors, timeout, attempts, log, clock,
                               jobs, locks, cooldowns)
        for k, _t in retry:
            quarters = [f"{k} q{j}" for j in range(1, 5)]
            if all(q in sok for q in quarters):
                parts[k] = [sok[q] for q in quarters]
                bad.pop(k, None)
            else:
                # a partly-fetched tile is not a fetched tile: keep the failure
                bad[k] = sbad.get(next((q for q in quarters if q in sbad), ""),
                                  bad[k])

    failed = [(k, t) for k, t in items if k in bad]
    # A tile satisfied through the split path lives in `parts`, and its
    # quarters can just as well have come off disk - counting only whole-tile
    # keys under-reports the cache every time a split has ever happened.
    cached = (sum(1 for k, _ in items if k in ok and ok[k][1])
              + sum(1 for k in parts if all(p[1] for p in parts[k])))

    # Every county under a tile we could not fetch. Returned to the caller, not
    # just logged: without it the gap report calls these counties "no
    # law-enforcement record at all - not in the source", which is a statement
    # about the source that this run has no evidence for.
    uncovered = sorted({g for _k, t in failed
                        for g in counties_in_tile(shapes or [], t)})
    if isinstance(gaps_out, dict):
        gaps_out["uncovered"] = uncovered
        gaps_out["failed_tiles"] = len(failed)

    if failed:
        out_of_time = sum(1 for k, _ in failed if isinstance(bad[k], TimeoutError))
        busy = sum(1 for k, _ in failed if isinstance(bad[k], MirrorsBusy))
        limited = sum(1 for k, _ in failed if isinstance(bad[k], RateLimited))
        why = []
        if out_of_time:
            why.append(f"{out_of_time} ran out of the {deadline_s}s budget - "
                       f"raise it with --deadline")
        if busy:
            why.append(f"{busy} never reached a mirror because this run's own "
                       f"other tiles were holding them - lower --jobs")
        if limited:
            why.append(f"{limited} were RATE-LIMITED (HTTP 429) - the mirrors "
                       f"are asking for a pause, not refusing the query. Wait "
                       f"a few minutes and re-run; what succeeded is cached, "
                       f"and --jobs 1 asks for less at a time")
        msg = (f"{len(failed)} of {len(tiles)} tiles failed"
               + (f" ({'; '.join(why)})" if why else "")
               + f". {len(tiles) - len(failed)} succeeded and are CACHED, so running "
               f"this again will only refetch the failures.")
        if uncovered:
            msg += (f" {len(uncovered)} count"
                    f"{'y' if len(uncovered) == 1 else 'ies'} sit under those "
                    f"tiles and would come back empty: "
                    f"{', '.join(uncovered[:12])}"
                    f"{' ...' if len(uncovered) > 12 else ''}.")
        if not allow_partial:
            raise RuntimeError(msg + " Use --allow-partial to accept an "
                                     "incomplete result anyway.")
        log(f"    [!] {msg}")
        log(f"    [!] PROCEEDING WITH A PARTIAL RESULT - the counties named above "
            f"have no agency because their tile failed, not because none exists.")

    if cached:
        log(f"    {cached} of {len(tiles)} tiles came from the local cache")

    # Collect with each element's own fetch date, then sort into a CANONICAL
    # order by (type, id). Tile order is not enough: the same box served as
    # four quarters yields a different sequence than the same box served whole,
    # and a tile read back from four cache files yields a third. pick_sheriffs
    # breaks ties on first-seen, so those three would pick different agencies
    # from identical data. (type, id) is the source's own identity and does not
    # depend on how the fetch happened to be carved up.
    elements = []
    for k, _t in items:
        if k in parts:
            for els, _c, fetched in parts[k]:
                elements.extend((e, fetched) for e in els)
        elif k in ok:
            elements.extend((e, ok[k][2]) for e in ok[k][0])
    elements.sort(key=lambda p: _element_key(p[0]))

    # Tiles overlap at their edges and a way can be returned by two of them.
    seen, out = set(), []
    for el, fetched in elements:
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
            # same two spellings for the website. A county sheriff page is a
            # real, checkable destination; it is not a substitute for a phone
            # number, but it is better than an empty field.
            "website": (t.get("website") or t.get("contact:website") or "").strip(),
            "address": street.strip(),
            "city": (t.get("addr:city") or "").strip(),
            # operator:type is how OSM records that an agency is county-run
            "admintype": (t.get("operator:type") or t.get("operator") or "").strip(),
            # The date THIS element's tile was fetched, which is what the row
            # is stamped with. A cached tile is not "fetched today" just
            # because the CSV is written today.
            "loaddate": fetched,
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


def suggest_widening(unmatched, match, hints=COUNTY_LE_HINTS):
    """[(pattern, counties_it_would_reach)] for the filter terms that help.

    Derived from the agency names that actually came back unmatched, so it can
    only ever suggest a term that reaches a real county in THIS state's data.
    A term already in `match` is skipped - there is no point recommending what
    is already switched on.
    """
    out = []
    for h in hints:
        if re.search(re.escape(h), match, re.I):
            continue
        rx = re.compile(h, re.I)
        n = sum(1 for agencies in unmatched.values()
                if any(rx.search(a) for a in agencies))
        if n:
            out.append((h, n))
    return sorted(out, key=lambda t: (-t[1], t[0]))


# Tokens that carry no vocabulary on their own. A phrase made only of these
# describes nothing; one containing them alongside a real word is fine.
_FILLER = frozenset(("the", "of", "and", "at", "for", "a", "an", "county",
                     "co", "dept", "department", "office", "offices"))


def is_county_level(agency, county_name):
    """Does this agency name say it belongs to the county, in the county's own
    words?

    There is no reliable way to spot a MUNICIPAL force from its name. City
    names are unbounded, and "police department" is not the discriminator it
    looks like - Nassau County and Baltimore County both run one. Testing the
    negative gets both answers wrong.

    So test the positive, the way the rest of this builder does: read the
    descriptor back out of TIGER's own NAME rather than pluralising a word
    from a table (README §8, "Fifteen states do not call them counties"). An
    agency is county-level when it carries the county's own name - "Waukesha
    County ...", "Acadia Parish ...", "Nome Census Area ..." - and that costs
    nothing to get right in Louisiana or Puerto Rico.

    The cost is conservative: "Prairie Justice Center" really is Nobles
    County's, and does not say so. It is skipped as a SOURCE of candidate
    terms, which is a missed suggestion, not a wrong one - and the report
    still prints it among the unmatched names for a human to read.
    """
    return bool(county_name) and county_name.lower() in agency.lower()


def discover_terms(unmatched, match, county_names, min_counties=2,
                   max_terms=8):
    """[(phrase, counties_it_would_reach)] mined from THIS state's own names.

    `COUNTY_LE_HINTS` is Minnesota's vocabulary, read off Minnesota's gap
    report. Another state files its county agency under whatever word that
    state uses, and a fixed hint list cannot propose a word nobody has looked
    at yet - so on the first run for a new state `suggest_widening` can only
    test Minnesota's hypotheses against that state's data. This reads the
    phrases that actually came back unmatched and counts what each would
    reach, so a state's own naming can surface itself.

    Suggestions only, never applied: the report prints them and the maintainer
    decides, exactly as with the curated hints.

    A phrase is offered only when it
      * reaches at least `min_counties` counties - a phrase reaching exactly
        one is that county's own name ("dane county jail"), not vocabulary;
      * is not already in `match`;
      * never appears in a municipal name anywhere in this state's unmatched
        records. Widening onto a city PD would relabel it as the county's
        primary LE, which is the one mistake this seeder must not make.
    """
    # Only county-level records are read, and only they count toward a term's
    # reach. A city PD's words never become a candidate, and a city PD never
    # inflates a real term's count into looking like vocabulary.
    county_level = {
        geoid: [a for a in agencies
                if is_county_level(a, county_names.get(geoid, ""))]
        for geoid, agencies in unmatched.items()
    }
    counties_with = {}
    for agencies in county_level.values():
        for a in agencies:
            toks = [t for t in re.split(r"[^a-z0-9']+", a.lower()) if len(t) > 1]
            grams = {" ".join(toks[i:i + n])
                     for n in (2, 3) for i in range(len(toks) - n + 1)}
            for g in grams:
                if not all(t in _FILLER for t in g.split()):
                    counties_with.setdefault(g, set())
    for geoid, agencies in county_level.items():
        for a in agencies:
            low = a.lower()
            for g in counties_with:
                if g in low:
                    counties_with[g].add(geoid)
    out = [(g, len(cs)) for g, cs in counties_with.items()
           if len(cs) >= min_counties
           and not re.search(re.escape(g), match, re.I)]
    # Longest phrase wins a tie so the report offers "law enforcement center"
    # rather than the vaguer "law enforcement" at the same reach.
    out.sort(key=lambda t: (-t[1], -len(t[0]), t[0]))
    # One phrase per vocabulary term. "public safety building" already reaches
    # every county "public safety" does, so listing both - and "safety
    # building", and "county public" - pads the report with four spellings of
    # one finding. Where a shorter phrase reaches no further than a longer one
    # already kept, it is the same term seen through a smaller window; the
    # longer is kept because it is the narrower match.
    kept = []
    for g, n in out:
        if any(n == m and (g in k or k in g) for k, m in kept):
            continue
        kept.append((g, n))
    return kept[:max_terms]


def dump_gaps(path, state, unmatched_by_county, names, match):
    """Write every unmatched county and all its agency names, uncapped.

    The printed report samples 20 counties so it stays scannable. On a state
    nobody has fetched before, the names it does not print are exactly the
    evidence that decides the filter, so `--gaps-dump` writes the lot to a
    file that can be read or sent on whole.
    """
    payload = {
        "state": state,
        "filter": match,
        "fetched": today_iso(),
        "counties": [
            {"geoid": g, "name": names.get(g, ""),
             "agencies": sorted({a for a in agencies if a})}
            for g, agencies in sorted(unmatched_by_county.items())
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return path


def report_gaps(state, geoids, names, records, chosen, match, log=print,
                uncovered=(), dump=None):
    """Say exactly WHY each county came back without a sheriff.

    "52 of 87" is a number, not a diagnosis. A county with no row is either a
    county whose agencies are all named something this filter does not match -
    which is fixable by widening it - or a county with no law-enforcement
    record in the source at all, which is not. Those two need opposite
    responses, so the report separates them and prints what the unmatched
    records are actually called.
    """
    # `geoids` is the authoritative county list, from the boundaries. Deriving
    # it from the records instead would make "no record at all" impossible to
    # report - a county with no records is not in the records - and the total
    # would silently shrink to however many counties happened to have one.
    geoids = sorted(geoids)
    by_county = {}
    for r in records:
        by_county.setdefault(r["geoid"], []).append(r)

    def label(g):
        return f"{g} {names.get(g, '')}".strip()

    # A county whose tile could not be fetched is NOT evidence about the
    # source. Calling it "no law-enforcement record at all - not in the source"
    # states something this run never established, and points at the wrong fix.
    lost = set(uncovered or ())
    unmatched = sorted(g for g in geoids if g not in chosen and by_county.get(g))
    empty = sorted(g for g in geoids
                   if g not in chosen and not by_county.get(g) and g not in lost)
    notfetched = sorted(g for g in geoids
                        if g not in chosen and not by_county.get(g) and g in lost)
    withphone = sum(1 for r in chosen.values() if r.get("phone"))
    withsite = sum(1 for r in chosen.values() if r.get("website"))

    log(f"\nGAP REPORT for {state} - {len(geoids)} count"
        f"{'y' if len(geoids) == 1 else 'ies'}")
    # The filter goes on its own line: a long regex in the middle of a label
    # pushes every number out of its column and the report stops being
    # scannable, which is the only thing it is for.
    log(f"  filter: /{match}/i")
    # "written" would be a lie: the write loop skips counties already in the
    # CSV unless --overwrite is given, and this is coverage, not a write count.
    log(f"  counties with an agency               : {len(chosen)}")
    log(f"    ...of those carrying a phone number : {withphone}")
    log(f"    ...of those carrying a website      : {withsite}")
    log(f"  have records, none matched the filter : {len(unmatched)}"
        f"{'  <- widening --match may fix these' if unmatched else ''}")
    for g in unmatched[:20]:
        got = ", ".join(sorted({r["agency"] for r in by_county[g] if r["agency"]})[:4])
        log(f"      {label(g):34s} {got}")
    if len(unmatched) > 20:
        log(f"      ... and {len(unmatched) - 20} more")
    helps = suggest_widening(
        {g: [r["agency"] for r in by_county[g]] for g in unmatched}, match)
    if helps:
        total = len({g for g in unmatched
                     if any(re.search(h, a, re.I)
                            for h, _n in helps for a in
                            [r["agency"] for r in by_county[g]])})
        log(f"    these terms would reach {total} of those "
            f"{len(unmatched)} counties:")
        for h, n in helps:
            log(f"      +{n:<3d} {h}")
        wider = "|".join([match] + [h for h, _n in helps])
        log(f"    python3 seed_le_contacts.py --state {state} --gaps \\")
        log(f"        --match '{wider}'")
    # The curated hints are Minnesota's words. On a state nobody has fetched
    # before they may all miss, and "no suggestions" would then read as "no
    # filter fixes this" when the truth is that nobody has looked at this
    # state's vocabulary yet. These come from the names in front of us.
    found = discover_terms(
        {g: [r["agency"] for r in by_county[g]] for g in unmatched},
        match, names)
    found = [(t, n) for t, n in found
             if not any(re.search(re.escape(t), h, re.I)
                        or re.search(re.escape(h), t, re.I) for h, _n in helps)]
    if found:
        log(f"    phrases in {state}'s own unmatched names, not in the curated")
        log("    list - read them before using them, they are not vetted:")
        for t, n in found:
            log(f"      +{n:<3d} {t}")
        wider = "|".join([match] + [t for t, _n in found])
        log(f"    python3 seed_le_contacts.py --state {state} --gaps \\")
        log(f"        --match '{wider}'")
    if dump and unmatched:
        where = dump_gaps(dump, state,
                          {g: [r["agency"] for r in by_county[g]]
                           for g in unmatched}, names, match)
        log(f"    all {len(unmatched)} unmatched counties written to {where}")
    if notfetched:
        log(f"  NOT FETCHED - their tile failed        : {len(notfetched)}"
            f"  <- unknown, not absent; re-run to fill")
        for i in range(0, min(len(notfetched), 12), 3):
            log("      " + "  ".join(f"{label(g):26s}" for g in notfetched[i:i + 3]))
        if len(notfetched) > 12:
            log(f"      ... and {len(notfetched) - 12} more")
    log(f"  no law-enforcement record at all      : {len(empty)}"
        f"{'  <- not in the source; no filter fixes this' if empty else ''}")
    for i in range(0, min(len(empty), 24), 3):
        log("      " + "  ".join(f"{label(g):26s}" for g in empty[i:i + 3]))
    if len(empty) > 24:
        log(f"      ... and {len(empty) - 24} more")
    log("")


def today_iso():
    """Fetch date, for sources that carry no vintage of their own.

    OSM has no dataset vintage - it is edited continuously - so the honest
    stamp is when this copy was taken, not a year invented for it.
    """
    import datetime as _dt
    return _dt.date.today().isoformat()


def _fetch_raw(state_abbr, use_osm, use_usgs, args, layer_id, shapes=None,
               gaps_out=None):
    """Whichever source is selected, in one place, returning one record shape."""
    if use_osm:
        if shapes is None and getattr(args, "refresh_shapes", False):
            # --show went straight to fetch_osm, which fetches its own shapes
            # with refresh defaulted off - so --refresh-shapes did nothing on
            # the one path people use when they suspect the cache.
            shapes = county_shapes(STATE_FIPS[state_abbr.upper()], refresh=True)
        return fetch_osm(state_abbr, allow_partial=args.allow_partial,
                         timeout=args.osm_timeout, attempts=args.osm_attempts,
                         deadline_s=args.deadline, jobs=args.jobs,
                         shapes=shapes, split=not args.no_split,
                         gaps_out=gaps_out)
    if use_usgs:
        return fetch_usgs(state_abbr, args.usgs_layer)
    return fetch_state(STATE_FIPS[state_abbr], layer_id, args.endpoint)




def dump_raw(state_abbr, use_osm, use_usgs, args, layer_id):
    """Print the server's own response for a few records, unmapped.

    When every mapped field comes back empty, the mapping and the response are
    indistinguishable from the outside. This shows which it is.
    """
    import urllib.parse
    if use_osm:
        # The same tiled, cached, deadline-bounded path the real fetch uses -
        # NOT a fresh whole-state query. A statewide box is what the mirrors
        # 504 on, and it would ignore tiles already sitting in the cache.
        shapes = county_shapes(STATE_FIPS[state_abbr.upper()],
                               refresh=getattr(args, "refresh_shapes", False))
        tiles = [t for b in bboxes_of_shapes(shapes) for t in tile_bbox(b)]
        clock = Deadline(getattr(args, "deadline", OSM_DEADLINE_S))
        w, s_, e, n = tiles[0]
        print("QUERY (one tile; the fetch runs this over "
              f"{len(tiles)} of them):\n"
              f"{OSM_QUERY.format(timeout=args.osm_timeout, s=s_, w=w, n=n, e=e)}")
        for i, tile in enumerate(tiles, 1):
            try:
                els, from_cache = _overpass_tile(
                    tile, OVERPASS_MIRRORS, args.osm_timeout, 1, print,
                    deadline=clock)
            except TimeoutError:
                print(f"  [!] out of time after {clock.elapsed():.0f}s "
                      f"({i - 1} tile(s) tried). Raise it with --deadline.")
                return 2
            except Exception as ex:                 # noqa: BLE001
                print(f"  [!] tile {i}/{len(tiles)} -> {redact_err(ex)}")
                continue
            if not els:
                print(f"  tile {i}/{len(tiles)}: 0 element(s)"
                      f"{' (cached)' if from_cache else ''} - trying the next one")
                continue
            print(f"  tile {i}/{len(tiles)}: {len(els)} element(s)"
                  f"{' (cached)' if from_cache else ''}\n")
            for el in els[:args.show]:
                print(json.dumps(el, indent=2)[:1500])
                print()
            return 0
        print("  [!] no tile returned an element")
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
