#!/usr/bin/env python3
"""What does OpenStreetMap actually carry for amateur and GMRS repeaters?

    python3 repeater_diagnose.py --state MN
    python3 repeater_diagnose.py --state MN --dump ~/atak-packs/MN_repeaters_raw.json

READ THIS BEFORE BUILDING A PACK. A repeater is only useful in the field if it
carries the frequency you listen on, the frequency you transmit on, and the
tone that opens it. Whether OSM has those for Minnesota is a question with an
answer, and this asks for it rather than assuming one.

It does NOT write a pack and does NOT decide anything. It reports:

  * how many repeater-ish objects came back, and how many carry coordinates
  * EVERY tag key present on them, with a count - so the real tagging scheme
    is read off the data instead of guessed at
  * per-field coverage for the fields a pack would need
  * a few whole records, verbatim, so the shape is visible

WHY THE QUERY LOOKS LIKE THAT. The keys below are CANDIDATES, not a schema.
OSM documents `communication:amateur_radio:repeater:*`, but documentation and
what mappers typed are different things, and GMRS may have no established
tagging at all. Each candidate is a plain indexed key lookup, which is cheap;
the key histogram then tells us what is really in use. A key-REGEX query would
ask the question more directly and is far too slow to run against a state.

Frequencies and tones are kept as STRINGS, start to finish. "023N" is a DCS
code whose leading zero and N suffix are load-bearing, and reading it as a
number turns it into 23.0. The same mistake in the other half of this repo
turns 146.94 MHz into 146.94 Hz.
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_le_contacts as sle                              # noqa: E402

# Candidate anchors. Each is an indexed key lookup; the histogram in the report
# is what actually establishes the scheme. A candidate returning nothing is a
# result, not a failure - it is how "GMRS is not tagged in OSM" gets shown
# rather than assumed.
REPEATER_QUERY = """
[out:json][timeout:{timeout}];
(
  nwr["communication:amateur_radio:repeater"]({s:.4f},{w:.4f},{n:.4f},{e:.4f});
  nwr["amateur_radio:repeater"]({s:.4f},{w:.4f},{n:.4f},{e:.4f});
  nwr["communication:amateur_radio"]({s:.4f},{w:.4f},{n:.4f},{e:.4f});
  nwr["amateur_radio"]({s:.4f},{w:.4f},{n:.4f},{e:.4f});
  nwr["repeater"]({s:.4f},{w:.4f},{n:.4f},{e:.4f});
  nwr["communication:gmrs"]({s:.4f},{w:.4f},{n:.4f},{e:.4f});
  nwr["gmrs"]({s:.4f},{w:.4f},{n:.4f},{e:.4f});
);
out center tags;
"""

# What a pack would need, and every spelling worth looking for. Written from
# the OPERATOR's side: the repeater's output is what you listen to, its input
# is what you transmit on. Naming them "out" and "in" in the popup would put
# the burden of that translation on someone holding a radio.
WANTED = [
    ("listen (repeater output)", [
        "communication:amateur_radio:repeater:frequency_out",
        "amateur_radio:repeater:frequency_out",
        "frequency_out", "repeater:frequency_out", "frequency"]),
    ("transmit (repeater input)", [
        "communication:amateur_radio:repeater:frequency_in",
        "amateur_radio:repeater:frequency_in",
        "frequency_in", "repeater:frequency_in"]),
    ("offset / shift", [
        "communication:amateur_radio:repeater:shift",
        "amateur_radio:repeater:shift", "shift", "repeater:shift", "offset"]),
    ("tone (CTCSS / DCS)", [
        "communication:amateur_radio:repeater:tone",
        "amateur_radio:repeater:tone", "tone", "repeater:tone",
        "ctcss", "ctcss_frequency", "dcs"]),
    ("callsign", [
        "communication:amateur_radio:callsign", "amateur_radio:callsign",
        "callsign", "ref:callsign"]),
    ("mode", [
        "communication:amateur_radio:repeater:mode",
        "amateur_radio:repeater:mode", "mode", "repeater:mode"]),
    ("name", ["name", "official_name"]),
    ("operator / sponsor", ["operator", "sponsor", "club"]),
]


def keep_everything(tags, lon, lat, fetched, el):
    """Diagnostic parse: drop nothing, interpret nothing, convert nothing.

    The whole point is to see what is there, so every tag rides along exactly
    as the source spells it, as a string.
    """
    return {"type": el.get("type"), "id": el.get("id"),
            "lon": lon, "lat": lat, "loaddate": fetched,
            "tags": {str(k): str(v) for k, v in tags.items()}}


def first_present(tags, keys):
    for k in keys:
        if tags.get(k):
            return k, tags[k]
    return None, None


def report(state, rows, log=print):
    log(f"\nREPEATER SOURCE REPORT for {state} - OpenStreetMap via Overpass")
    log(f"  objects returned                      : {len(rows)}")
    if not rows:
        log("")
        log("  Nothing came back for any candidate tag. That is an ANSWER, not")
        log("  a failure: it means OSM has no repeater tagging here that these")
        log("  keys reach. Before concluding OSM is empty, re-run with --dump")
        log("  and check a county you KNOW has a repeater.")
        return
    withpos = sum(1 for r in rows if r["lon"] is not None)
    log(f"    ...carrying coordinates             : {withpos}")

    keys = Counter(k for r in rows for k in r["tags"])
    log(f"\n  EVERY tag key present, most common first ({len(keys)} distinct).")
    log("  This is the real scheme; the query's keys were only candidates.")
    for k, n in keys.most_common(40):
        log(f"      {n:5d}  {k}")
    if len(keys) > 40:
        log(f"      ... and {len(keys) - 40} more distinct keys")

    log("\n  COVERAGE of the fields a pack would need:")
    for label, cands in WANTED:
        hits = Counter()
        for r in rows:
            k, _v = first_present(r["tags"], cands)
            if k:
                hits[k] += 1
        total = sum(hits.values())
        pct = (100.0 * total / len(rows)) if rows else 0.0
        log(f"      {label:28s} {total:5d} of {len(rows)}  ({pct:.0f}%)")
        for k, n in hits.most_common():
            log(f"          via {k}  x{n}")
        if not hits:
            log("          no candidate key present on any object")

    usable = sum(1 for r in rows
                 if r["lon"] is not None
                 and first_present(r["tags"], WANTED[0][1])[0])
    log(f"\n  objects with coordinates AND a listen frequency: {usable}")
    log("  That is the honest ceiling for a pack built from OSM alone.")

    log("\n  SAMPLE - three objects, verbatim:")
    for r in rows[:3]:
        log(f"      {r['type']}/{r['id']}  {r['lon']},{r['lat']}")
        for k, v in sorted(r["tags"].items()):
            log(f"          {k} = {v}")
    log("")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Ask OSM what repeater data exists for a state. Reports "
                    "only; writes no pack and decides nothing.")
    ap.add_argument("--state", default="MN", help="two-letter abbreviation")
    ap.add_argument("--dump", metavar="FILE",
                    help="write every object and all its tags to FILE as JSON")
    ap.add_argument("--deadline", type=int, default=sle.OSM_DEADLINE_S,
                    help=f"wall-clock budget for the fetch (default "
                         f"{sle.OSM_DEADLINE_S}). The first fetch of a state "
                         f"has nothing cached and is the slow one.")
    ap.add_argument("--jobs", type=int, default=sle.OSM_JOBS)
    ap.add_argument("--osm-timeout", type=int, default=sle.OSM_SERVER_TIMEOUT_S)
    ap.add_argument("--allow-partial", action="store_true",
                    help="report on what came back even if some tiles failed. "
                         "For a diagnostic this is usually what you want: a "
                         "partial answer about the tagging still answers the "
                         "question, and nothing is being written.")
    a = ap.parse_args(argv)
    state = a.state.strip().upper()

    rows = sle.fetch_osm(
        state, log=print, timeout=a.osm_timeout, jobs=a.jobs,
        deadline_s=a.deadline, allow_partial=a.allow_partial,
        # A DIFFERENT question about the same boxes: its own cache namespace,
        # or this reads the police tiles already on the device.
        query=REPEATER_QUERY, prefix="repeaters", parse=keep_everything)

    report(state, rows)
    if a.dump:
        with open(a.dump, "w", encoding="utf-8") as fh:
            json.dump({"state": state, "source": "OpenStreetMap (Overpass)",
                       "licence": "ODbL 1.0 - (c) OpenStreetMap contributors",
                       "fetched": sle.today_iso(), "objects": rows},
                      fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        print(f"  all {len(rows)} objects and their tags written to {a.dump}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
