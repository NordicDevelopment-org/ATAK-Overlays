#!/usr/bin/env python3
"""
fetch_county_seats.py - populate data/county_seats.csv from Wikidata.

WHY THIS EXISTS
---------------
build_county_pack.py will not invent a county seat. There is no authoritative
federal machine-readable list of them, so the seat column ships empty and this
script fills it from a source that can be cited and dated: Wikidata, which
records a county's seat as property P36 (capital) and its 5-digit FIPS code as
P882.

Wikidata is community-maintained, not a government register. That is written
into every row it produces, so the popup shows exactly what it is:

    County seat: Center City  [Wikidata (community-maintained) 2026-09-14]

If that is not good enough for your use, edit data/county_seats.csv by hand and
put your own source and vintage in the row - the builder reads whatever is
there and shows it verbatim.

USAGE
-----
    python3 fetch_county_seats.py --state MN        # one state
    python3 fetch_county_seats.py --all             # every state
    python3 fetch_county_seats.py --state MN --dry-run   # print, write nothing

Existing rows for other states are preserved; rows for a state you re-fetch are
replaced. Requires only the standard library.
"""
import argparse
import csv
import datetime as dt
import json
import os
import ssl
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SPARQL = "https://query.wikidata.org/sparql"
SSL_CTX = ssl.create_default_context()
UA = {"User-Agent": "atak-statepacks-seats/1.0 (+https://github.com/NordicDevelopment-org/ATAK-Overlays)",
      "Accept": "application/sparql-results+json"}

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "data", "county_seats.csv")
SOURCE = "Wikidata (community-maintained)"

# P31/P279* Q28575 = "county of the United States" and its subclasses
# P882 = FIPS 6-4 county code (5 digits)   P36 = capital (the county seat)
QUERY = """
SELECT ?fips ?seatLabel WHERE {
  ?county wdt:P882 ?fips .
  ?county wdt:P36 ?seat .
  FILTER(STRSTARTS(?fips, "%s"))
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""

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


def fetch_state(sfp, timeout=90):
    """[(geoid, seat)] for one state FIPS prefix."""
    url = SPARQL + "?" + urlencode({"query": QUERY % sfp, "format": "json"})
    with urlopen(Request(url, headers=UA), timeout=timeout, context=SSL_CTX) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    out = {}
    for b in data.get("results", {}).get("bindings", []):
        fips = (b.get("fips", {}).get("value") or "").strip()
        seat = (b.get("seatLabel", {}).get("value") or "").strip()
        # skip unresolved labels (Wikidata returns the Q-id when no English label)
        if len(fips) == 5 and seat and not seat.startswith("Q"):
            out.setdefault(fips, seat)
    return sorted(out.items())


def read_existing(path):
    rows = {}
    if not os.path.exists(path):
        return rows, []
    comments = []
    with open(path, newline="", encoding="utf-8") as fh:
        lines = fh.readlines()
    comments = [l for l in lines if l.startswith("#")]
    body = [l for l in lines if not l.startswith("#")]
    for row in csv.DictReader(body):
        g = (row.get("geoid") or "").strip()
        if g:
            rows[g] = row
    return rows, comments


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fill data/county_seats.csv from Wikidata.")
    ap.add_argument("--state", help="two-letter abbreviation, e.g. MN")
    ap.add_argument("--all", action="store_true", help="every state")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    a = ap.parse_args(argv)

    if a.all:
        targets = sorted(STATE_FIPS)
    elif a.state and a.state.upper() in STATE_FIPS:
        targets = [a.state.upper()]
    else:
        ap.error("give --state XX or --all")

    today = dt.date.today().isoformat()
    existing, comments = read_existing(CSV_PATH)
    added = 0
    for st in targets:
        sfp = STATE_FIPS[st]
        try:
            pairs = fetch_state(sfp)
        except Exception as e:                      # noqa: BLE001
            print(f"[!] {st}: {e}", file=sys.stderr)
            continue
        for geoid, seat in pairs:
            existing[geoid] = {"geoid": geoid, "seat": seat,
                               "source": SOURCE, "vintage": today}
            added += 1
        print(f"[*] {st}: {len(pairs)} county seats")

    if a.dry_run:
        for g in sorted(existing):
            print(f"  {g}  {existing[g]['seat']}")
        print(f"\n(dry run - {added} rows would be written to {CSV_PATH})")
        return 0

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as fh:
        fh.writelines(comments)
        w = csv.DictWriter(fh, fieldnames=["geoid", "seat", "source", "vintage"])
        w.writeheader()
        for g in sorted(existing):
            w.writerow({k: existing[g].get(k, "") for k in w.fieldnames})
    print(f"\n{added} row(s) written; {len(existing)} total in {CSV_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
