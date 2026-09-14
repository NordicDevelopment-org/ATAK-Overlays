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
    return (1 if re.search(r"sheriff", rec["agency"], re.I) else 0,
            1 if rec.get("phone") else 0)


def pick_sheriffs(records, match="sheriff"):
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
    ap.add_argument("--match", default="sheriff",
                    help="regex an agency name must match (default: sheriff)")
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
    ap.add_argument("--osm-timeout", type=int, default=30,
                    help=f"seconds the Overpass SERVER is given per tile "
                         f"(default 30); the socket allows "
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
                    help=f"wall-clock budget in seconds for the WHOLE Overpass "
                         f"fetch, tiles and retries included (default "
                         f"{OSM_DEADLINE_S}). Tiles already fetched are cached, "
                         f"so re-running resumes rather than starting over.")
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
                raw = _fetch_raw(st, use_osm, use_usgs, a, layer_id, shapes=shapes)
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
                                    "type": r["admintype"]})
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
                               "vintage": vintage}
            added += 1
        withphone = sum(1 for r in chosen.values() if r["phone"])
        print(f"[*] {st}: {len(records)} LE records -> {len(chosen)} counties "
              f"({withphone} with a phone number)")
        if a.gaps:
            report_gaps(st, [g for g, _polys in shapes], cnames, records,
                        chosen, a.match)
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
            log(f"    (could not cache county shapes: {ex})")   # not an error
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
OSM_DEADLINE_S = 480
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
# A tile that TIMES OUT on two different mirrors is a tile that is too much to
# ask for, not two unlucky mirrors. Stop there and split it. Waiting for the
# third mirror to also time out buys no information and costs a whole timeout.
# A fast failure (504, connection refused) is a mirror problem, not a size
# problem, so it does not count towards this.
OSM_TIMEOUTS_BEFORE_SPLIT = 2

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


def _is_timeout(ex):
    """True when a request ran out of time, as opposed to being refused.

    urllib wraps a socket timeout in URLError, and since 3.10 socket.timeout is
    TimeoutError - so both shapes have to be checked. The distinction matters:
    a timeout says the question was too big, a refusal says the mirror is busy.
    """
    return isinstance(ex, TimeoutError) or isinstance(
        getattr(ex, "reason", None), TimeoutError)


def _overpass_tile(tile, mirrors, timeout, attempts, log, deadline=None,
                   locks=None, start=0):
    """One tile, cached on disk. Returns (elements, came_from_cache).

    A tile that has already been fetched is not fetched again: the public
    mirrors time out under load, and without a cache every retry throws away
    the tiles that did work.

    Mirrors are tried in an order ROTATED by `start`, so concurrent tiles do
    not all queue behind the same mirror, and `locks` holds each mirror to one
    in-flight request at a time. There is no sleep between attempts: rotating
    to another mirror IS the retry, and sleeping only spends the budget.
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

    # A tile that was served as quarters last time is still fully cached - just
    # under four keys instead of one. Without this the parent gets asked for
    # again on every run, and it is exactly the tile the mirrors would not
    # serve, so every run pays for it. Edge duplicates are dropped downstream.
    quarters = [_tile_cache_path(q) for q in split_tile(tile)]
    if all(os.path.exists(q) for q in quarters):
        try:
            els = []
            for q in quarters:
                with open(q, encoding="utf-8") as fh:
                    els.extend(json.load(fh))
            return els, True
        except (OSError, ValueError):
            pass

    if not mirrors:
        raise RuntimeError("no Overpass mirror to ask")
    w, s_, e, n = tile
    q = OSM_QUERY.format(timeout=timeout, s=s_, w=w, n=n, e=e)
    order = list(mirrors)
    if order:
        k = start % len(order)
        order = order[k:] + order[:k]
    last, timeouts = None, 0
    # One pass over the mirrors by default. A second pass would ask the same
    # large question of the same busy mirrors; splitting the tile is the retry
    # that actually changes the question.
    for _attempt in range(max(1, attempts)):
        for url in order:
            # The server is told to give up at `timeout`; the socket allows
            # that plus slack to send the answer back.
            want = timeout + OSM_SOCKET_SLACK
            if deadline is not None:
                if deadline.expired():
                    raise TimeoutError("deadline reached")
                # never wait longer than the budget has left
                sock = max(5, min(want, int(deadline.left())))
            else:
                sock = want
            lock = (locks or {}).get(url)
            if lock is not None:
                # If this mirror is already serving one of our tiles, move on
                # to the next one rather than queueing behind ourselves. On the
                # LAST pass, wait for a mirror instead: reporting a tile as
                # failed because our own other tiles were busy would be a lie.
                if _attempt >= max(1, attempts) - 1:
                    wait = (30 if deadline is None
                            else max(1, min(30, deadline.left())))
                    if not lock.acquire(timeout=wait):
                        continue
                elif not lock.acquire(blocking=False):
                    continue
            try:
                data = urllib.parse.urlencode({"data": q}).encode()
                req = urllib.request.Request(url, data=data, headers=UA)
                with urllib.request.urlopen(req, timeout=sock, context=SSL_CTX) as r:
                    doc = json.loads(r.read().decode("utf-8", "replace"))
                els = doc.get("elements") or []
                _write_cache(path, els)
                return els, False
            except Exception as ex:                 # noqa: BLE001
                last = ex
                if _is_timeout(ex):
                    timeouts += 1
            finally:
                if lock is not None:
                    lock.release()
            if timeouts >= OSM_TIMEOUTS_BEFORE_SPLIT:
                # Two mirrors could not answer this box in time. A third will
                # not tell us anything new; the caller splits it instead.
                raise RuntimeError(
                    f"timed out on {timeouts} mirrors at {timeout}s: "
                    f"{redact_err(last)}") from last
    if last is None:
        # every mirror was busy on every pass and nothing was ever attempted
        raise RuntimeError("no mirror was free to take this tile")
    # EVERY failure leaves here as a RuntimeError, deliberately. The only
    # TimeoutError this function raises is the shared deadline, above - which
    # is what lets the caller tell "out of budget" (do not split, do not retry)
    # apart from "this box was too much" (split it) with a plain isinstance.
    raise RuntimeError(redact_err(last)) from last


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


def _write_cache(path, els):
    """One tile's elements, atomically."""
    _write_json_atomic(path, els)


def _run_tiles(items, mirrors, timeout, attempts, log, clock, jobs, locks):
    """Fetch `items` ([(key, tile)]) concurrently. Returns (ok, bad).

    ok  = {key: (elements, from_cache)}
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
                              deadline=clock, locks=locks, start=i)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
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
            els, from_cache = ok[key]
            say(f"    {key}: {len(els)} feature(s)"
                f"{' (cached)' if from_cache else ''}"
                f"   [{clock.elapsed():.0f}s used, {max(0, clock.left()):.0f}s left]")
    return ok, bad


def fetch_osm(state_abbr, mirrors=None, log=print, bbox=None, timeout=30,
              attempts=1, allow_partial=False, deadline_s=OSM_DEADLINE_S,
              jobs=OSM_JOBS, shapes=None, split=True):
    """[{name, phone, address, city, admintype, lon, lat}] inside `bbox`.

    Queried as a grid of tiles rather than one statewide request, because the
    public mirrors return 504 for the whole-state box under load. Tiles are
    fetched CONCURRENTLY (one in-flight request per mirror), every tile that
    lands is CACHED, and a tile the mirrors will not serve is SPLIT into
    quarters and retried small instead of asked for again unchanged.

    The whole fetch runs against ONE wall-clock budget (`deadline_s`). It stops
    new requests; a request already in flight can still overrun it by up to
    `timeout` seconds, because a socket read cannot be cancelled from here.

    If any tile is still missing at the end, this RAISES rather than returning
    what it has: a partial set would put a sheriff in some counties and none in
    others, with nothing in the pack to say which. Pass allow_partial=True to
    accept an incomplete result knowingly - the counties it costs are named.
    """
    if bbox is None:
        if shapes is None:
            shapes = county_shapes(STATE_FIPS[state_abbr.upper()], log=log)
        bbox = bbox_of_shapes(shapes)
    mirrors = mirrors or OVERPASS_MIRRORS
    tiles = tile_bbox(bbox)
    clock = Deadline(deadline_s)
    locks = {u: threading.Lock() for u in mirrors}
    log(f"    {len(tiles)} tile(s), {min(jobs, len(mirrors))} at a time, "
        f"{deadline_s}s budget for all of them (cached tiles are free)")

    items = [(f"tile {i}/{len(tiles)}", t) for i, t in enumerate(tiles, 1)]
    ok, bad = _run_tiles(items, mirrors, timeout, attempts, log, clock, jobs, locks)

    # A tile the mirrors would not serve is retried SMALLER, not again. Only
    # real failures are split - running out of budget is not a tile the mirrors
    # refused, and splitting it would just spend a budget that is already gone.
    parts = {}
    retry = [(k, t) for k, t in items
             if k in bad and not isinstance(bad[k], TimeoutError)]
    if split and retry and not clock.expired():
        log(f"    retrying {len(retry)} failed tile(s) as quarters - a smaller "
            f"box is a cheaper question than the same one again")
        sub = [(f"{k} q{j}", q)
               for k, t in retry for j, q in enumerate(split_tile(t), 1)]
        sok, sbad = _run_tiles(sub, mirrors, timeout, attempts, log, clock,
                               jobs, locks)
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
    cached = sum(1 for k, _ in items if k in ok and ok[k][1])

    if failed:
        out_of_time = sum(1 for k, _ in failed if isinstance(bad[k], TimeoutError))
        msg = (f"{len(failed)} of {len(tiles)} tiles failed"
               + (f" ({out_of_time} ran out of the {deadline_s}s budget; raise it "
                  f"with --deadline)" if out_of_time else "")
               + f". {len(tiles) - len(failed)} succeeded and are CACHED, so running "
               f"this again will only refetch the failures - the public mirrors "
               f"are rate-limited, not broken. Wait a minute and retry.")
        if shapes:
            hit = sorted({g for k, t in failed for g in counties_in_tile(shapes, t)})
            msg += (f" {len(hit)} count{'y' if len(hit) == 1 else 'ies'} sit under "
                    f"those tiles and would come back empty: "
                    f"{', '.join(hit[:12])}{' ...' if len(hit) > 12 else ''}.")
        if not allow_partial:
            raise RuntimeError(msg + " Use --allow-partial to accept an "
                                     "incomplete result anyway.")
        log(f"    [!] {msg}")
        log(f"    [!] PROCEEDING WITH A PARTIAL RESULT - the counties named above "
            f"have no agency because their tile failed, not because none exists.")

    if cached:
        log(f"    {cached} of {len(tiles)} tiles came from the local cache")

    # Reassemble in TILE ORDER, not completion order. Threads finish in
    # whatever order the mirrors answer, and pick_sheriffs keeps the first
    # match per county - so an unordered list would pick a different agency
    # from one run to the next with no data having changed.
    elements = []
    for k, _t in items:
        if k in parts:
            for els, _c in parts[k]:
                elements.extend(els)
        elif k in ok:
            elements.extend(ok[k][0])

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
            # same two spellings for the website. A county sheriff page is a
            # real, checkable destination; it is not a substitute for a phone
            # number, but it is better than an empty field.
            "website": (t.get("website") or t.get("contact:website") or "").strip(),
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


def report_gaps(state, geoids, names, records, chosen, match, log=print):
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

    unmatched = sorted(g for g in geoids if g not in chosen and by_county.get(g))
    empty = sorted(g for g in geoids if g not in chosen and not by_county.get(g))
    withphone = sum(1 for r in chosen.values() if r.get("phone"))
    withsite = sum(1 for r in chosen.values() if r.get("website"))

    log(f"\nGAP REPORT for {state} - {len(geoids)} count"
        f"{'y' if len(geoids) == 1 else 'ies'}")
    # The filter goes on its own line: a long regex in the middle of a label
    # pushes every number out of its column and the report stops being
    # scannable, which is the only thing it is for.
    log(f"  filter: /{match}/i")
    log(f"  matched and written                   : {len(chosen)}")
    log(f"    ...of those carrying a phone number : {withphone}")
    log(f"    ...of those carrying a website      : {withsite}")
    log(f"  have records, none matched the filter : {len(unmatched)}"
        f"{'  <- widening --match may fix these' if unmatched else ''}")
    for g in unmatched[:20]:
        got = ", ".join(sorted({r["agency"] for r in by_county[g] if r["agency"]})[:4])
        log(f"      {label(g):34s} {got}")
    if len(unmatched) > 20:
        log(f"      ... and {len(unmatched) - 20} more")
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


def _fetch_raw(state_abbr, use_osm, use_usgs, args, layer_id, shapes=None):
    """Whichever source is selected, in one place, returning one record shape."""
    if use_osm:
        return fetch_osm(state_abbr, allow_partial=args.allow_partial,
                         timeout=args.osm_timeout, attempts=args.osm_attempts,
                         deadline_s=args.deadline, jobs=args.jobs,
                         shapes=shapes, split=not args.no_split)
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
        shapes = county_shapes(STATE_FIPS[state_abbr.upper()])
        tiles = tile_bbox(bbox_of_shapes(shapes))
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
