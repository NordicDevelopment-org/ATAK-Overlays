#!/usr/bin/env python3
"""Emergency services, health and corrections for a state, from OpenStreetMap.

    python3 build_emergency_pack.py --state MN --out ~/atak-packs
    python3 build_emergency_pack.py --state MN --only police,correctional
    python3 build_emergency_pack.py --state MN --counts      # ask, do not build

WHY OSM AND NOT THE CATALOG'S FIRST CHOICE. Every HIFLD source for these
layers pointed at maps.nccs.nasa.gov, which stopped resolving in September
2026 - not blocked, gone from DNS. They are `enabled: false, confidence:
dead` in the catalog and there is nothing to fall back to for most of them.
OSM is the one source for this sector that has actually been fetched live
from this project, twice, for two states.

WHAT THAT COSTS, SAID PLAINLY. OSM is contributed, not surveyed. Coverage is
uneven and nothing here pretends otherwise: the build prints how many
features each class returned so a thin layer looks thin instead of looking
like an answer. Measured for Minnesota police in an earlier run: 517 features
in the state box, 32 carrying a phone number. That is the shape of this data.

The tiling, mirror rotation, rate-limit handling, caching and tile-splitting
are NOT reimplemented here - they are seed_le_contacts.fetch_osm, which is
the code that has survived two live state runs. This file chooses the tags,
the folders and the symbols.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_county_pack as bcp                             # noqa: E402
import glyphs                                               # noqa: E402
import seed_le_contacts as seed                             # noqa: E402
import symbology                                            # noqa: E402

# Each class is (layer, [selectors], folder label).
#
# A SELECTOR is a tuple of clauses that must ALL hold, and a clause is
# (key, op, value) with op "=" or "~". Structured, not a string: the first
# version wrote them as "amenity=clinic][urgent_care=yes" and emitted
# nwr["amenity=clinic][urgent_care=yes"], which asks Overpass for a tag whose
# KEY is that entire string. It is valid QL, it returns nothing, and it looks
# like "there are no urgent care clinics in Minnesota". The repeater
# diagnostic already cost a live run to exactly this mistake.
#
# A "~" value is a POSIX ERE - no inline flags. Write "(?i)" at the front and
# the emitter turns it into Overpass's ,i modifier; it never reaches the
# server as text.
#
# The layer name is not decoration: symbology.py decides icon and colour from
# it, and a class naming a layer symbology does not know fails check_classes
# offline rather than building a folder of invisible placemarks.
CLASSES = [
    ("hospitals", [(("amenity", "=", "hospital"),)],
     "Hospitals"),
    ("urgent_care", [(("amenity", "=", "clinic"), ("urgent_care", "=", "yes")),
                     (("healthcare", "=", "clinic"), ("urgent_care", "=", "yes"))],
     "Urgent care"),
    ("nursing_homes", [(("amenity", "=", "nursing_home"),),
                       (("social_facility", "=", "nursing_home"),),
                       (("social_facility", "=", "assisted_living"),)],
     "Nursing and assisted living"),
    ("ems", [(("emergency", "=", "ambulance_station"),)],
     "EMS stations"),
    ("fire_stations", [(("amenity", "=", "fire_station"),)],
     "Fire stations"),
    ("police", [(("amenity", "=", "police"),)],
     "Law enforcement"),
    ("correctional", [(("amenity", "=", "prison"),)],
     "Correctional facilities"),
    ("eoc", [(("emergency", "=", "disaster_response"),),
             (("office", "=", "government"),
              ("government", "=", "emergency_management"))],
     "Emergency operations"),
    ("government", [(("amenity", "=", "townhall"),),
                    (("amenity", "=", "courthouse"),)],
     "Government and courts"),
    ("schools", [(("amenity", "=", "school"),)],
     "Schools"),
    ("shelters", [(("amenity", "=", "shelter"),
                   ("shelter_type", "~", "^(emergency|disaster|storm|civil)")),
                  (("social_facility", "=", "shelter"),)],
     "Shelters"),
]

SOURCE = "OpenStreetMap contributors"
LICENCE = "ODbL 1.0"
SOURCE_URL = "https://www.openstreetmap.org/copyright"


def class_names():
    return [c[0] for c in CLASSES]


def check_classes():
    """Problems, as a list. Enforced offline, before any network is touched.

    Every class must name a layer symbology knows, that layer must take a
    point icon, and the icon must render. A class that fails any of those
    would otherwise build a folder of invisible placemarks and say nothing.
    """
    problems = []
    for layer, selectors, label in CLASSES:
        if not selectors:
            problems.append(f"{layer}: no tag selector")
        if not label.strip():
            problems.append(f"{layer}: no folder label")
        glyph = symbology.glyph_for(layer)
        if glyph is None:
            problems.append(f"{layer}: symbology has no icon for it")
            continue
        if symbology.colour_for(layer) is None:
            problems.append(f"{layer}: symbology has no colour for it")
        try:
            glyphs.render(glyph, (255, 255, 255))
        except KeyError:
            problems.append(f"{layer}: glyph {glyph!r} does not render")
        for sel in selectors:
            if not sel:
                problems.append(f"{layer}: an empty selector matches everything")
            for clause in sel:
                if len(clause) != 3:
                    problems.append(f"{layer}: malformed clause {clause!r}")
                    continue
                key, op, value = clause
                if op not in ("=", "~"):
                    problems.append(f"{layer}: unknown op {op!r} on {key!r}")
                if not key or not value:
                    problems.append(f"{layer}: empty key or value in {clause!r}")
    dupes = [n for n in class_names() if class_names().count(n) > 1]
    if dupes:
        problems.append(f"duplicate class name(s): {sorted(set(dupes))}")
    return problems


def emit_clause(key, op, value):
    """One Overpass tag filter. Key and value are quoted SEPARATELY.

    ["amenity"="police"] - not ["amenity=police"], which asks for a tag whose
    key is the literal string "amenity=police" and quietly returns nothing.
    """
    if op not in ("=", "~"):
        raise ValueError(f"unknown selector op {op!r} for {key!r}")
    if op == "=":
        return f'["{key}"="{value}"]'
    if value.startswith("(?i)"):
        # POSIX ERE has no inline flags; Overpass spells it as a modifier.
        return f'["{key}"~"{value[4:]}",i]'
    return f'["{key}"~"{value}"]'


def emit_selector(selector):
    if not selector:
        raise ValueError("empty selector matches everything")
    return "".join(emit_clause(*c) for c in selector)


def build_query(classes=None, timeout=None):
    """One Overpass query covering every class, built FROM the class table.

    From the table, not beside it. The repeater diagnostic shipped a
    hand-written query next to a separate list of what it measured, the two
    disagreed, and a whole live run came back empty. Generating one from the
    other is what stops that happening twice.
    """
    rows = classes if classes is not None else CLASSES
    if not rows:
        raise ValueError("no classes selected; nothing to ask for")
    timeout = timeout or seed.OSM_SERVER_TIMEOUT_S
    parts = []
    for _layer, selectors, _label in rows:
        for sel in selectors:
            parts.append(f"  nwr{emit_selector(sel)}({{{{bbox}}}});")
    body = "\n".join(parts)
    return f"[out:json][timeout:{timeout}];\n(\n{body}\n);\nout center tags;"


def matches(tags, selector):
    """Every clause in one selector holds for these tags."""
    for key, op, value in selector:
        actual = tags.get(key)
        if actual is None:
            return False
        if op == "=":
            if actual != value:
                return False
        else:
            flags = re.I if value.startswith("(?i)") else 0
            pattern = value[4:] if value.startswith("(?i)") else value
            if not re.search(pattern, actual, flags):
                return False
    return True


def classify(tags):
    """Which class an element belongs to, or None.

    First match wins, in CLASSES order, so a building tagged both a townhall
    and a shelter files under the earlier class rather than appearing twice.
    An element matching nothing is DROPPED, not guessed at: the query asked
    for eleven specific things, so an element that is none of them came back
    for a reason nobody here predicted, and inventing a folder for it would
    be inventing a value.
    """
    for layer, selectors, _label in CLASSES:
        for sel in selectors:
            if matches(tags, sel):
                return layer
    return None


def parse_element(tags, lon, lat, fetched, element):
    """One row, or None to drop it. Missing stays missing."""
    layer = classify(tags)
    if layer is None:
        return None
    name = (tags.get("name") or tags.get("official_name") or "").strip()
    street = " ".join(x for x in (tags.get("addr:housenumber"),
                                  tags.get("addr:street")) if x).strip()
    return {
        "layer": layer,
        "name": name,
        "operator": (tags.get("operator") or "").strip(),
        "phone": (tags.get("phone") or tags.get("contact:phone") or "").strip(),
        "website": (tags.get("website")
                    or tags.get("contact:website") or "").strip(),
        "address": street,
        "city": (tags.get("addr:city") or "").strip(),
        "emergency": (tags.get("emergency") or "").strip(),
        "beds": (tags.get("beds") or "").strip(),
        "osm_type": element.get("type", ""),
        "osm_id": element.get("id", ""),
        "fetched": fetched,
        "lon": lon,
        "lat": lat,
        # Rule 3: the whole source attribute table rides along.
        "tags": dict(tags),
    }


def rows_for(r):
    """(label, value, note) in reading order. Missing stays missing."""
    return [
        ("Name", r.get("name", ""), ""),
        ("Operator", r.get("operator", ""), ""),
        ("Phone", r.get("phone", ""),
         "" if r.get("phone") else "not in OpenStreetMap for this feature"),
        ("Address", " ".join(x for x in (r.get("address", ""),
                                         r.get("city", "")) if x).strip(), ""),
        ("Website", r.get("website", ""), ""),
        ("Emergency dept", r.get("emergency", ""), ""),
        ("Beds", r.get("beds", ""), ""),
        ("OSM", f"{r.get('osm_type', '')}/{r.get('osm_id', '')}".strip("/"), ""),
        ("Retrieved", r.get("fetched", ""), ""),
    ]


def placemark(r, style_id):
    rows = [(a, b, c) for a, b, c in rows_for(r) if b]
    body = "".join(
        f"<tr><td><b>{bcp.esc(a)}</b></td><td>{bcp.esc(b)}"
        + (f" <span style='color:#888'>({bcp.esc(c)})</span>" if c else "")
        + "</td></tr>"
        for a, b, c in rows)
    extra = "".join(
        f"<Data name=\"{bcp.esc(k)}\"><value>{bcp.esc(str(v))}</value></Data>"
        for k, v in sorted((r.get("tags") or {}).items()))
    foot = (f"<p style='color:#888'>Source: {bcp.esc(SOURCE)} "
            f"({bcp.esc(LICENCE)})<br/>{bcp.esc(SOURCE_URL)}<br/>"
            f"Retrieved {bcp.esc(r.get('fetched', ''))}</p>")
    # An unnamed feature is common in OSM and must not become a blank pin with
    # no way to tell what it is.
    label = r.get("name") or f"(unnamed {r.get('layer', 'feature')})"
    return (f"<Placemark><name>{bcp.esc(label)}</name>"
            f"<styleUrl>#{style_id}</styleUrl>"
            f"<description><![CDATA[<table>{body}</table>{foot}]]></description>"
            f"<ExtendedData>{extra}</ExtendedData>"
            f"<Point><coordinates>{r['lon']:.5f},{r['lat']:.5f},0</coordinates>"
            f"</Point></Placemark>")


def build_kml(state, rows, built, classes=None):
    """One Document, one Folder per class, counts in every folder name."""
    wanted = [c for c in (classes or CLASSES)]
    by = {}
    for r in rows:
        by.setdefault(r["layer"], []).append(r)

    styles, folders, icons = [], [], {}
    for layer, _sel, label in wanted:
        group = sorted(by.get(layer, []),
                       key=lambda r: (r.get("name", "").lower(),
                                      r.get("osm_id", 0)))
        glyph = symbology.glyph_for(layer)
        rgb = symbology.colour_for(layer)
        style_id = f"s_{layer}"
        icons[f"icons/{layer}.png"] = glyphs.render(glyph, rgb)
        styles.append(
            f'<Style id="{style_id}"><IconStyle><scale>1.0</scale>'
            f"<Icon><href>icons/{layer}.png</href></Icon></IconStyle>"
            f"<LabelStyle><scale>0.8</scale></LabelStyle></Style>")
        # An empty folder is kept and says zero. Dropping it would make "no
        # fire stations in this state" indistinguishable from "we did not ask".
        body = "".join(placemark(r, style_id) for r in group)
        folders.append(
            f"<Folder><name>{bcp.esc(label)} ({len(group)})</name>"
            f"<open>0</open>{body}</Folder>")

    head = (f"<name>{bcp.esc(state)} emergency services and health</name>"
            f"<description><![CDATA[<p>{bcp.esc(SOURCE)} ({bcp.esc(LICENCE)})"
            f"<br/>{bcp.esc(SOURCE_URL)}<br/>Retrieved {bcp.esc(built)}</p>"
            f"<p>OpenStreetMap is contributed, not surveyed. Coverage is "
            f"uneven and a small count means the data is thin, not that the "
            f"place is empty.</p>]]></description>")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            + head + "".join(styles) + "".join(folders)
            + "</Document></kml>"), icons


def counts(rows):
    by = {}
    for r in rows:
        by[r["layer"]] = by.get(r["layer"], 0) + 1
    return by


def report(rows, log=print):
    """What came back, per class, including the zeroes."""
    got = counts(rows)
    named = sum(1 for r in rows if r.get("name"))
    phoned = sum(1 for r in rows if r.get("phone"))
    log(f"    {len(rows)} feature(s); {named} named, {phoned} with a phone")
    for layer, _sel, label in CLASSES:
        n = got.get(layer, 0)
        mark = "   " if n else " ! "
        log(f"   {mark}{label:32} {n:6}")
    empty = [lbl for lay, _s, lbl in CLASSES if not got.get(lay)]
    if empty:
        log(f"    [!] {len(empty)} class(es) returned nothing: "
            f"{', '.join(empty)}")
        log(f"        That is OpenStreetMap having no such feature tagged in "
            f"this state, not a failed fetch - a failed fetch raises.")


def build_state(state, out_dir, log=print, mirrors=None, deadline_s=None,
                only=None, today=None, allow_partial=False):
    problems = check_classes()
    if problems:
        # Offline, before a single request. A class with no icon would build a
        # folder of invisible pins and a live run costs 6-17 minutes.
        raise SystemExit("class table is wrong:\n  " + "\n  ".join(problems))

    chosen = CLASSES
    if only:
        want = {s.strip() for s in only if s.strip()}
        unknown = want - set(class_names())
        if unknown:
            raise SystemExit(
                f"unknown class(es): {', '.join(sorted(unknown))}\n"
                f"known: {', '.join(class_names())}")
        chosen = [c for c in CLASSES if c[0] in want]

    query = build_query(chosen)
    log(f"[*] {state}: asking OpenStreetMap for "
        f"{len(chosen)} class(es) of feature")
    rows = seed.fetch_osm(
        state, mirrors=mirrors, log=log, query=query,
        # The cache namespace is derived from the query, so changing the class
        # table invalidates the tiles rather than serving answers to a question
        # nobody is asking any more. That mistake already hid one fix.
        prefix="emergency_" + bcp.safe(
            "_".join(sorted(c[0] for c in chosen)))[:48],
        parse=parse_element,
        allow_partial=allow_partial,
        deadline_s=deadline_s or seed.OSM_DEADLINE_S,
        partial_note=("the classes above are undercounted because a tile "
                      "failed, not because the features do not exist"))
    if only:
        keep = {c[0] for c in chosen}
        rows = [r for r in rows if r["layer"] in keep]

    report(rows, log=log)
    built = today or dt.date.today().isoformat()
    kml, icons = build_kml(state, rows, built, classes=chosen)
    stamp = bcp.edition(built, kml, icons)
    path = os.path.join(out_dir, f"{state}_Emergency__{stamp}.kmz")
    size = bcp.write_kmz(path, kml, icons)
    log(f"[*] {state}: {len(rows)} feature(s) -> {os.path.basename(path)} "
        f"({size // 1024} KB)")
    return {"state": state, "path": path, "features": len(rows),
            "counts": counts(rows)}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Emergency services, health and corrections from OSM.")
    ap.add_argument("--state", default="MN")
    ap.add_argument("--out", default=os.path.expanduser("~/atak-packs"))
    ap.add_argument("--only", default="",
                    help="comma-separated class names; default is all. "
                         "Known: " + ", ".join(class_names()))
    ap.add_argument("--deadline", type=int, default=None,
                    help="wall-clock budget in seconds for the whole fetch")
    ap.add_argument("--allow-partial", action="store_true",
                    help="write a pack even if a tile never landed. Off by "
                         "default: a partial fetch looks like thin data.")
    ap.add_argument("--check", action="store_true",
                    help="verify the class table offline and exit")
    ap.add_argument("--query", action="store_true",
                    help="print the Overpass query and exit")
    a = ap.parse_args(argv)

    if a.check:
        problems = check_classes()
        for p in problems:
            print(f"  [!] {p}")
        print(f"{len(problems)} problem(s)")
        return 1 if problems else 0
    if a.query:
        print(build_query())
        return 0

    only = [s for s in a.only.split(",") if s.strip()] if a.only else None
    build_state(a.state, a.out, only=only, deadline_s=a.deadline,
                allow_partial=a.allow_partial)
    return 0


if __name__ == "__main__":
    sys.exit(main())
