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

# HIFLD Open's final snapshot, re-hosted by NASA NCCS. Unofficial and frozen.
HIFLD_LE = ("https://maps.nccs.nasa.gov/mapping/rest/services"
            "/hifld_open/law_enforcement/FeatureServer")
LAYER_NAME_RE = re.compile(r"^local_law_enforcement", re.I)
SOURCE = "HIFLD LE Locations (frozen snapshot)"
VINTAGE_UNKNOWN = "snapshot year not reported"
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

SSL_CTX = ssl.create_default_context()
UA = {"User-Agent": "atak-statepacks-le/1.0 "
                    "(+https://github.com/NordicDevelopment-org/ATAK-Overlays)"}

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "data", "le_contacts.csv")
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
    a = ap.parse_args(argv)

    try:
        layer_id, vintage = resolve_layer(a.endpoint)
    except Exception as e:                          # noqa: BLE001
        print(f"[!] cannot reach the HIFLD layer: {e}", file=sys.stderr)
        print("    Nothing was written. The LE columns stay empty, which is "
              "correct - better than a number nobody can source.", file=sys.stderr)
        return 2
    if a.probe:
        return 0

    if a.all:
        targets = sorted(STATE_FIPS)
    elif a.state and a.state.upper() in STATE_FIPS:
        targets = [a.state.upper()]
    else:
        ap.error("give --state XX or --all (or --probe)")

    existing, comments = read_existing(CSV_PATH)
    kept = set(existing)
    added = skipped = 0
    for st in targets:
        sfp = STATE_FIPS[st]
        try:
            records = fetch_state(sfp, layer_id, a.endpoint)
        except Exception as e:                      # noqa: BLE001
            print(f"[!] {st}: {e}", file=sys.stderr)
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
                               "phone": r["phone"], "source": SOURCE, "vintage": vintage}
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

    write_csv(CSV_PATH, existing, comments)
    print(f"\n{added} row(s) written, {skipped} existing row(s) kept; "
          f"{len(existing)} total in {CSV_PATH}")
    print(f"Source stamped on new rows: {SOURCE} {vintage}")
    print("These are a FROZEN snapshot. Verify any number before you rely on it,")
    print("then edit the row and put your own source and year in it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
