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

WHY THE QUERY LOOKS LIKE THAT. The keys are CANDIDATES, not a schema. OSM
documents `communication:amateur_radio:repeater:*`, but documentation and what
mappers typed are different things, and GMRS may have no established tagging at
all. Each candidate is a plain indexed key lookup, which is cheap; the key
histogram then tells us what is really in use.

They are LEAF keys, and that distinction already cost one real run. Overpass's
`nwr["k"]` matches an object carrying EXACTLY key k, so anchoring on the parent
`communication:amateur_radio:repeater` walks past a repeater whose only tag is
`communication:amateur_radio:repeater:frequency_out`. The first Minnesota run
returned 1 object from 8 tiles for that reason, and the query is now GENERATED
from the key list so the two cannot drift apart again.

If a normal run still comes back thin, `--deep` re-asks with a key REGEX, which
matches `...:repeater:whatever` without anyone predicting "whatever". It is the
definitive question and much slower, because a key regex cannot use the tag
index. Use it to settle thin-data-vs-wrong-guess, not as the default.

Frequencies and tones are kept as STRINGS, start to finish. "023N" is a DCS
code whose leading zero and N suffix are load-bearing, and reading it as a
number turns it into 23.0. The same mistake in the other half of this repo
turns 146.94 MHz into 146.94 Hz.
"""
import argparse
import hashlib
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_le_contacts as sle                              # noqa: E402

# Keys specific enough to ANCHOR a query on. Generic keys are deliberately
# absent: nwr["name"] over Minnesota returns most of the state.
#
# These are LEAF keys, and that distinction is the whole bug this file shipped
# with. Overpass's nwr["k"] matches an object carrying EXACTLY key k. The
# documented scheme puts the data in communication:amateur_radio:repeater:
# frequency_out, so the parent key communication:amateur_radio:repeater does
# not exist on it, and a query anchored on the parent walks straight past every
# repeater in the state. The first MN run returned 1 feature from 8 tiles
# because of this, not because OSM is empty.
ANCHOR_KEYS = [
    # amateur, documented hierarchy
    "communication:amateur_radio:repeater:frequency_out",
    "communication:amateur_radio:repeater:frequency_in",
    "communication:amateur_radio:repeater:tone",
    "communication:amateur_radio:repeater:shift",
    "communication:amateur_radio:callsign",
    "communication:amateur_radio",
    # amateur, shorter spellings in use
    "amateur_radio:repeater:frequency_out",
    "amateur_radio:repeater:frequency_in",
    "amateur_radio:repeater",
    "amateur_radio",
    "repeater:frequency_out",
    "repeater",
    # bare, used by mappers who did not follow a scheme
    "frequency_out",
    "frequency_in",
    # GMRS. No established scheme is known; asking and getting nothing is how
    # that gets shown rather than assumed.
    "communication:gmrs",
    "gmrs",
]

# Generic keys that must never anchor a query, however useful they are in the
# report. A test holds this list against ANCHOR_KEYS.
TOO_GENERIC = ("name", "official_name", "operator", "sponsor", "club", "mode",
               "tone", "shift", "offset", "frequency", "callsign", "ctcss",
               "dcs", "ctcss_frequency", "ref:callsign")


def build_query(keys=None, deep=False):
    """The Overpass query, built FROM the key list rather than beside it.

    The first version of this file wrote the query by hand next to a separate
    list of the fields the report measures, and the two disagreed. Generating
    one from the other is what stops that recurring.

    `deep` swaps exact-key lookups for a key REGEX, which matches
    communication:amateur_radio:repeater:whatever without anyone having to
    predict "whatever". It is the definitive question and it is much slower,
    because a key regex cannot use the tag index - so it is opt-in, for
    settling whether a thin result means thin data or a wrong guess.
    """
    keys = list(keys or ANCHOR_KEYS)
    # TOO_GENERIC was a comment pretending to be a guard: nothing read it, so
    # emptying it changed nothing and the mutation harness said so. Now it
    # refuses to build the query, because nwr["name"] over Minnesota is not a
    # slow query, it is most of the state coming back.
    bad = [k for k in keys if k in TOO_GENERIC]
    if bad:
        raise ValueError(
            f"too generic to anchor an Overpass query on: {', '.join(bad)}. "
            f"These keys are on millions of objects; anchoring on one asks for "
            f"the whole state.")
    box = "({s:.4f},{w:.4f},{n:.4f},{e:.4f})"
    if deep:
        lines = [f'  nwr[~"amateur_radio|repeater|gmrs"~"."]{box};']
    else:
        lines = [f'  nwr["{k}"]{box};' for k in keys]
    body = "\n".join(lines)
    return "[out:json][timeout:{timeout}];\n(\n" + body + "\n);\nout center tags;\n"


REPEATER_QUERY = build_query()


def cache_prefix(query, deep=False):
    """A cache namespace that changes when the QUERY changes.

    Tiles are keyed by bounding box plus this prefix. Editing the query while
    keeping the prefix would serve the old question's answers to the new one -
    the same trap as reading police tiles for repeaters, one level finer, and
    the one that would have hidden this very fix behind a stale cache.
    """
    h = hashlib.sha1(query.encode("utf-8")).hexdigest()[:8]
    return f"repeaters{'_deep' if deep else ''}_{h}"


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


# One dense box per state, for the --deep spot check. A key-regex query cannot
# use the tag index, so asking about a whole state takes an hour; asking about
# the metro area where mappers are most active answers "is this tagging used
# here at all" in minutes. Absence over the densest part of a state is the
# strongest cheap evidence there is - it is not proof of absence statewide, and
# the report says so rather than implying otherwise.
METRO_BOX = {
    "MN": "-93.55,44.75,-92.95,45.15",      # Twin Cities
    "WI": "-88.15,42.90,-87.80,43.15",      # Milwaukee
}


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


def report(state, rows, log=print, deep=False):
    log(f"\nREPEATER SOURCE REPORT for {state} - OpenStreetMap via Overpass")
    log(f"  objects returned                      : {len(rows)}")
    if not rows:
        log("")
        if deep:
            log("  Nothing came back for a key REGEX matching any key containing")
            log("  amateur_radio, repeater or gmrs. No spelling anyone failed to")
            log("  predict can hide from that, so within the area actually")
            log("  fetched this is not a guess: OSM has no repeater tagging here.")
        else:
            log("  Nothing came back for any candidate tag. That is an ANSWER, not")
            log("  a failure: it means OSM has no repeater tagging here that these")
            log("  keys reach. --deep re-asks with a key regex, which no unguessed")
            log("  spelling defeats.")
        log("  A tile that FAILED above is unanswered, not empty - check the")
        log("  partial-result line before reading this as total absence.")
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
    ap.add_argument("--deep", action="store_true",
                    help="re-ask with a key REGEX instead of exact keys. "
                         "Matches any key containing amateur_radio, repeater "
                         "or gmrs, so it cannot be defeated by a spelling "
                         "nobody predicted. MUCH slower - a key regex cannot "
                         "use the tag index. Its own cache namespace, so it "
                         "neither reads nor poisons the normal run's tiles.")
    ap.add_argument("--bbox", metavar="W,S,E,N",
                    help="ask about this box instead of the whole state. "
                         "Pairs with --deep. The grid stays a fixed 3x3 (see "
                         "README section 11), so this is still 9 requests - but "
                         "9 SMALL ones. Measured over the Twin Cities: 390s, "
                         "against an hour for the statewide version on mirrors "
                         "that are already refusing.")
    ap.add_argument("--allow-partial", action="store_true",
                    help="report on what came back even if some tiles failed. "
                         "For a diagnostic this is usually what you want: a "
                         "partial answer about the tagging still answers the "
                         "question, and nothing is being written.")
    a = ap.parse_args(argv)
    state = a.state.strip().upper()

    bbox = None
    if a.bbox:
        try:
            bbox = tuple(float(x) for x in a.bbox.split(","))
            if len(bbox) != 4:
                raise ValueError("need four numbers")
        except ValueError as ex:
            raise SystemExit(f"--bbox wants W,S,E,N in degrees: {ex}")
    query = build_query(deep=a.deep)
    # The namespace is derived FROM the query, so editing the query can never
    # serve the old question's cached answers to the new one.
    prefix = cache_prefix(query, deep=a.deep)
    print(f"    asking about {1 if a.deep else len(ANCHOR_KEYS)} "
          f"{'key pattern' if a.deep else 'candidate keys'}"
          f"  (cache namespace {prefix})")
    if a.deep:
        print("    --deep: key-regex query, no tag index, expect this to be slow")

    rows = sle.fetch_osm(
        state, log=print, timeout=a.osm_timeout, jobs=a.jobs,
        deadline_s=a.deadline, allow_partial=a.allow_partial,
        bbox=bbox, query=query, prefix=prefix, parse=keep_everything,
        partial_note="a failed tile is an UNANSWERED area, not an empty one. "
                     "The count below is a floor, and re-running fills it in.")

    report(state, rows, deep=a.deep)
    if not a.deep and len(rows) < 5:
        print("  THIN. Before concluding OSM has nothing here, re-ask without")
        print("  guessing at key names. Over ONE DENSE BOX first - a key regex")
        print("  cannot use the tag index, so statewide is an hour and a metro")
        print("  is minutes, and either one answers 'is this tagging used':")
        print(f"      python3 repeater_diagnose.py --state {state} --deep \\")
        print(f"          --bbox {METRO_BOX.get(state, 'W,S,E,N')} "
              f"--deadline 900 --allow-partial")
        print("")
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
