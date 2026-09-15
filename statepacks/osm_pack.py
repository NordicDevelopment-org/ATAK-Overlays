#!/usr/bin/env python3
"""Build an ATAK pack from OpenStreetMap, given a table of feature classes.

One code path, several packs. build_emergency_pack and build_aviation_pack are
each a class table and a CLI; everything here - the query, the classification,
the popup, the folders, the reporting - is shared. The alternative was copying
400 lines per sector, and this repo has already paid for one duplicated table
(the power pack's fuel styles, which drifted until they were deleted).

None of the network machinery lives here either. Tiling, mirror rotation,
rate-limit back-off, caching, tile-splitting and the wall-clock budget are
seed_le_contacts.fetch_osm, which is the code that survived live Minnesota and
Wisconsin runs. This module chooses tags, folders and symbols.

A PACK SPEC is a dict:
    kind         one word for the filename, e.g. "Emergency" -> MN_Emergency__*
    title        the document name, e.g. "emergency services and health"
    classes      [(layer, [selector, ...], folder label)]
    default_off  {layer, ...} that import switched off
    fields       [(label, tag key, note when the tag is absent)] for the popup

A SELECTOR is a tuple of clauses that must ALL hold; a clause is
(key, op, value) with op "=" or "~". Structured, not a string: a string form
once emitted nwr["amenity=clinic][urgent_care=yes"], which asks Overpass for a
tag whose KEY is that whole text. Valid QL, always empty, and it reads as
"there are no urgent care clinics in Minnesota".
"""
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_county_pack as bcp                             # noqa: E402
import glyphs                                               # noqa: E402
import seed_le_contacts as seed                             # noqa: E402
import symbology                                            # noqa: E402

SOURCE = "OpenStreetMap contributors"
LICENCE = "ODbL 1.0"
SOURCE_URL = "https://www.openstreetmap.org/copyright"

# Fields every pack shows, before its own. Order is reading order.
COMMON_FIELDS = [
    ("Operator", "operator", ""),
    ("Phone", "phone", "not in OpenStreetMap for this feature"),
    ("Website", "website", ""),
]


def class_names(spec):
    return [c[0] for c in spec["classes"]]


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


def build_query(spec, classes=None):
    """One Overpass query covering every class, built FROM the class table.

    From the table, not beside it. The repeater diagnostic shipped a
    hand-written query next to a separate list of what it measured, the two
    disagreed, and a whole live run came back empty.

    The bounding box is a Python .format() placeholder, NOT Overpass Turbo's
    {{bbox}}. fetch_osm formats this per tile with south, west, north, east.
    Turbo syntax survives .format() as the literal text {bbox} and earns
    HTTP 400 from every mirror - it has already cost one full live run.

    `out center tags` and not `out body; >; out skel qt`. The latter is what
    Overpass Turbo suggests for drawing polygons: it returns every member node
    of every way. For a pack of point icons that is hundreds of thousands of
    untagged nodes to carry and discard, and the centre is the only thing a
    pin can be placed at anyway.
    """
    rows = classes if classes is not None else spec["classes"]
    if not rows:
        raise ValueError("no classes selected; nothing to ask for")
    box = "({s:.4f},{w:.4f},{n:.4f},{e:.4f})"
    parts = []
    for _layer, selectors, _label in rows:
        for sel in selectors:
            parts.append(f"  nwr{emit_selector(sel)}{box};")
    query = ("[out:json][timeout:{timeout}];\n(\n" + "\n".join(parts)
             + "\n);\nout center tags;")
    seed.validate_query(query)
    return query


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


def classify(spec, tags):
    """Which class an element belongs to, or None.

    First match wins, in table order, so a feature matching two classes is one
    placemark rather than two. An element matching nothing is DROPPED, not
    guessed at: the query asked for specific things, so an element that is
    none of them came back for a reason nobody predicted, and inventing a
    folder for it would be inventing a value.
    """
    for layer, selectors, _label in spec["classes"]:
        for sel in selectors:
            if matches(tags, sel):
                return layer
    return None


def check_spec(spec):
    """Problems, as a list. Run OFFLINE before any request.

    A live fetch is 6-17 minutes. A class naming a layer symbology does not
    know, or a query Overpass will reject, must fail in microseconds instead.
    """
    problems = []
    for key in ("kind", "title", "classes"):
        if not spec.get(key):
            problems.append(f"spec has no {key}")
    if problems:
        return problems

    names = class_names(spec)
    for layer, selectors, label in spec["classes"]:
        if not selectors:
            problems.append(f"{layer}: no tag selector")
        if not str(label).strip():
            problems.append(f"{layer}: no folder label")
        glyph = symbology.glyph_for(layer)
        if glyph is None:
            problems.append(f"{layer}: symbology has no icon for it")
        elif symbology.colour_for(layer) is None:
            problems.append(f"{layer}: symbology has no colour for it")
        else:
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
                k, op, v = clause
                if op not in ("=", "~"):
                    problems.append(f"{layer}: unknown op {op!r} on {k!r}")
                if not k or not v:
                    problems.append(f"{layer}: empty key or value in {clause!r}")

    dupes = [n for n in names if names.count(n) > 1]
    if dupes:
        problems.append(f"duplicate class name(s): {sorted(set(dupes))}")
    unknown = set(spec.get("default_off") or ()) - set(names)
    if unknown:
        problems.append(f"default_off names unknown class(es): {sorted(unknown)}")

    # Build the real query and hold it to fetch_osm's contract. A check whose
    # all-clear means nothing is why a malformed query once reached a live run.
    try:
        seed.validate_query(build_query(spec))
    except ValueError as ex:
        problems.append(f"query: {ex}")
    return problems


def parse_element(spec, tags, lon, lat, fetched, element):
    """One row, or None to drop it. Missing stays missing."""
    layer = classify(spec, tags)
    if layer is None:
        return None
    street = " ".join(x for x in (tags.get("addr:housenumber"),
                                  tags.get("addr:street")) if x).strip()
    return {
        "layer": layer,
        "name": (tags.get("name") or tags.get("official_name") or "").strip(),
        "operator": (tags.get("operator") or "").strip(),
        "phone": (tags.get("phone") or tags.get("contact:phone") or "").strip(),
        "website": (tags.get("website")
                    or tags.get("contact:website") or "").strip(),
        "address": street,
        "city": (tags.get("addr:city") or "").strip(),
        "osm_type": element.get("type", ""),
        "osm_id": element.get("id", ""),
        "fetched": fetched,
        "lon": lon,
        "lat": lat,
        # Rule 3: the whole source attribute table rides along.
        "tags": dict(tags),
    }


def rows_for(spec, r):
    """(label, value, note) in reading order. Missing stays missing."""
    tags = r.get("tags") or {}
    out = [("Name", r.get("name", ""), "")]
    for label, key, note in COMMON_FIELDS:
        value = r.get(key, "")
        out.append((label, value, "" if value else note))
    for label, key, note in spec.get("fields", []):
        value = str(tags.get(key, "")).strip()
        out.append((label, value, "" if value else note))
    out.append(("Address", " ".join(x for x in (r.get("address", ""),
                                                r.get("city", "")) if x).strip(), ""))
    out.append(("County", r.get("county", ""), ""))
    out.append(("OSM", f"{r.get('osm_type', '')}/{r.get('osm_id', '')}".strip("/"), ""))
    out.append(("Retrieved", r.get("fetched", ""), ""))
    return out


def placemark(spec, r, style_id, visible=True):
    rows = [(a, b, c) for a, b, c in rows_for(spec, r) if b]
    body = "".join(
        f"<tr><td><b>{bcp.esc(a)}</b></td><td>{bcp.esc(b)}"
        + (f" <span style='color:#888'>({bcp.esc(c)})</span>" if c else "")
        + "</td></tr>" for a, b, c in rows)
    extra = "".join(
        f'<Data name="{bcp.esc(k)}"><value>{bcp.esc(str(v))}</value></Data>'
        for k, v in sorted((r.get("tags") or {}).items()))
    foot = (f"<p style='color:#888'>Source: {bcp.esc(SOURCE)} "
            f"({bcp.esc(LICENCE)})<br/>{bcp.esc(SOURCE_URL)}<br/>"
            f"Retrieved {bcp.esc(r.get('fetched', ''))}</p>")
    # An unnamed feature is common in OSM and must not become a blank pin.
    label = r.get("name") or f"(unnamed {r.get('layer', 'feature')})"
    return (f"<Placemark><name>{bcp.esc(label)}</name>"
            + ("" if visible else "<visibility>0</visibility>")
            + f"<styleUrl>#{style_id}</styleUrl>"
            f"<description><![CDATA[<table>{body}</table>{foot}]]></description>"
            f"<ExtendedData>{extra}</ExtendedData>"
            f"<Point><coordinates>{r['lon']:.5f},{r['lat']:.5f},0</coordinates>"
            f"</Point></Placemark>")


def counts(rows):
    out = {}
    for r in rows:
        out[r["layer"]] = out.get(r["layer"], 0) + 1
    return out


def build_kml(spec, state, rows, built, classes=None):
    """One Document, one Folder per class, counts in every folder name."""
    wanted = classes if classes is not None else spec["classes"]
    off = set(spec.get("default_off") or ())
    by = {}
    for r in rows:
        by.setdefault(r["layer"], []).append(r)

    styles, folders, icons = [], [], {}
    for layer, _sel, label in wanted:
        group = sorted(by.get(layer, []),
                       key=lambda r: (r.get("name", "").lower(),
                                      str(r.get("osm_id", ""))))
        style_id = f"s_{layer}"
        icons[f"icons/{layer}.png"] = glyphs.render(
            symbology.glyph_for(layer), symbology.colour_for(layer))
        styles.append(
            f'<Style id="{style_id}"><IconStyle><scale>1.0</scale>'
            f"<Icon><href>icons/{layer}.png</href></Icon></IconStyle>"
            f"<LabelStyle><scale>0.8</scale></LabelStyle></Style>")
        # Visibility goes on the PLACEMARKS as well as the folder. ATAK honours
        # the folder for the tree but draws a placemark whose own visibility is
        # unset, so a folder-only flag imports looking off and renders on.
        hidden = layer in off
        vis = "<visibility>0</visibility>" if hidden else ""
        body = "".join(placemark(spec, r, style_id, visible=not hidden)
                       for r in group)
        # An empty folder is KEPT and says zero. Dropping it would make "none
        # of these here" indistinguishable from "we did not ask".
        folders.append(
            f"<Folder><name>{bcp.esc(label)} ({len(group)})</name>"
            f"<open>0</open>{vis}{body}</Folder>")

    head = (f"<name>{bcp.esc(state)} {bcp.esc(spec['title'])}</name>"
            f"<description><![CDATA[<p>{bcp.esc(SOURCE)} ({bcp.esc(LICENCE)})"
            f"<br/>{bcp.esc(SOURCE_URL)}<br/>Retrieved {bcp.esc(built)}</p>"
            f"<p>OpenStreetMap is contributed, not surveyed. Coverage is "
            f"uneven and a small count means the data is thin, not that the "
            f"place is empty.</p>]]></description>")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            + head + "".join(styles) + "".join(folders)
            + "</Document></kml>"), icons


def county_index(shapes):
    """[(geoid, bbox, polys)] - each county's bounding box, precomputed.

    A point-in-polygon test walks every vertex of every ring. Minnesota's
    counties carry about 1,900 vertices each, so testing 6,235 features
    against 87 counties unfiltered is on the order of a billion operations in
    pure Python - minutes on a phone, for a question a rectangle answers.

    The bbox is not an approximation of the answer, only of the search: a
    point inside the box still gets the full polygon test, and a point outside
    it cannot possibly be inside the polygon.
    """
    out = []
    for geoid, polys in shapes:
        xs, ys = [], []
        for rings in polys:
            for ring in rings:
                for pt in ring:
                    xs.append(pt[0])
                    ys.append(pt[1])
        if not xs:
            continue
        out.append((geoid, (min(xs), min(ys), max(xs), max(ys)), polys))
    return out


def clip_to_state(rows, state, log=print, refresh=False):
    """Drop features outside the state, and tag the survivors with a county.

    THE BUG THIS EXISTS FOR. The Overpass query is a bounding box, and a box
    around Minnesota necessarily contains slices of Wisconsin, Iowa, both
    Dakotas and Ontario. Nothing clipped them, so every pack carried hundreds
    of features in states it does not claim to cover. seed_le_contacts already
    clipped its own results this way and said so on screen; this path never
    did.

    A point outside every county is DROPPED, never filed under the nearest
    one. A hospital attributed to the wrong state is worse than one absent.
    """
    fips = seed.STATE_FIPS.get(state.upper())
    if not fips:
        raise SystemExit(f"no FIPS code for state {state!r}")
    shapes, names = seed.county_shapes(fips, log=log, refresh=refresh,
                                       with_names=True)
    index = county_index(shapes)
    if not index:
        raise SystemExit(f"no county shapes for {state}; cannot clip")

    # One box around the whole state, tested first. Most out-of-state points
    # fail here and never touch a polygon.
    sw = min(b[0] for _g, b, _p in index)
    ss = min(b[1] for _g, b, _p in index)
    se = max(b[2] for _g, b, _p in index)
    sn = max(b[3] for _g, b, _p in index)

    kept, dropped = [], 0
    for r in rows:
        x, y = r["lon"], r["lat"]
        if not (sw <= x <= se and ss <= y <= sn):
            dropped += 1
            continue
        geoid = None
        for gid, (w, s_, e, n), polys in index:
            if not (w <= x <= e and s_ <= y <= n):
                continue
            if any(seed.point_in_polygon(x, y, rings) for rings in polys):
                geoid = gid
                break
        if geoid is None:
            dropped += 1
            continue
        r["county_geoid"] = geoid
        r["county"] = names.get(geoid, "")
        kept.append(r)

    if dropped:
        log(f"    {dropped} feature(s) fell outside every {state} county and "
            f"were dropped, not guessed - the Overpass query is a RECTANGLE "
            f"around the state, so it reaches into the neighbours.")
    return kept


def report(spec, rows, log=print, classes=None):
    """What came back, per class, including the zeroes.

    `classes` is what was ASKED for. Without it this reported every class in
    the table, so a `--only police` run announced that OpenStreetMap has no
    hospitals in Minnesota - about a query that never mentioned them. A zero
    for something nobody asked is not a finding, it is a lie with a number on
    it.
    """
    asked = classes if classes is not None else spec["classes"]
    asked_names = [c[0] for c in asked]
    got = counts(rows)
    named = sum(1 for r in rows if r.get("name"))
    phoned = sum(1 for r in rows if r.get("phone"))
    log(f"    {len(rows)} feature(s); {named} named, {phoned} with a phone")
    for layer, _sel, label in asked:
        n = got.get(layer, 0)
        log(f"   {'   ' if n else ' ! '}{label:32} {n:6}")

    empty = [lbl for lay, _s, lbl in asked if not got.get(lay)]
    if empty:
        log(f"    [!] {len(empty)} class(es) returned nothing: "
            f"{', '.join(empty)}")
        log(f"        That is OpenStreetMap having no such feature tagged in "
            f"this state, not a failed fetch - a failed fetch raises.")
    skipped = [lbl for lay, _s, lbl in spec["classes"]
               if lay not in asked_names]
    if skipped:
        log(f"    {len(skipped)} class(es) were NOT asked for and say nothing "
            f"either way: {', '.join(skipped)}")


def build_state(spec, state, out_dir, log=print, mirrors=None, deadline_s=None,
                only=None, today=None, allow_partial=False):
    problems = check_spec(spec)
    if problems:
        raise SystemExit("class table is wrong:\n  " + "\n  ".join(problems))

    chosen = spec["classes"]
    if only:
        want = {s.strip() for s in only if s.strip()}
        unknown = want - set(class_names(spec))
        if unknown:
            raise SystemExit(
                f"unknown class(es): {', '.join(sorted(unknown))}\n"
                f"known: {', '.join(class_names(spec))}")
        chosen = [c for c in spec["classes"] if c[0] in want]

    log(f"[*] {state}: asking OpenStreetMap for {len(chosen)} class(es)")
    rows = seed.fetch_osm(
        state, mirrors=mirrors, log=log, query=build_query(spec, chosen),
        # The cache namespace includes which classes were asked for, so
        # changing the table invalidates the tiles rather than serving answers
        # to a question nobody is asking any more.
        prefix=bcp.safe(spec["kind"]).lower() + "_" + bcp.safe(
            "_".join(sorted(c[0] for c in chosen)))[:48],
        parse=lambda t, lon, lat, f, el: parse_element(spec, t, lon, lat, f, el),
        allow_partial=allow_partial,
        deadline_s=deadline_s or seed.OSM_DEADLINE_S,
        partial_note=("the classes above are undercounted because a tile "
                      "failed, not because the features do not exist"))
    if only:
        keep = {c[0] for c in chosen}
        rows = [r for r in rows if r["layer"] in keep]

    # The box is a rectangle; the state is not. Clip before anything counts
    # the results, or every number below includes the neighbours.
    rows = clip_to_state(rows, state, log=log)
    report(spec, rows, log=log, classes=chosen)
    built = today or dt.date.today().isoformat()
    kml, icons = build_kml(spec, state, rows, built, classes=chosen)
    stamp = bcp.edition(built, kml, icons)
    path = os.path.join(out_dir, f"{state}_{bcp.safe(spec['kind'])}__{stamp}.kmz")
    size = bcp.write_kmz(path, kml, icons)
    log(f"[*] {state}: {len(rows)} feature(s) -> {os.path.basename(path)} "
        f"({size // 1024} KB)")
    return {"state": state, "path": path, "features": len(rows),
            "counts": counts(rows)}


def add_arguments(ap, spec):
    ap.add_argument("--state", default="MN")
    ap.add_argument("--out", default=os.path.expanduser("~/atak-packs"))
    ap.add_argument("--only", default="",
                    help="comma-separated class names; default is all. "
                         "Known: " + ", ".join(class_names(spec)))
    ap.add_argument("--deadline", type=int, default=None,
                    help="wall-clock budget in seconds for the whole fetch")
    ap.add_argument("--allow-partial", action="store_true",
                    help="write a pack even if a tile never landed. Off by "
                         "default: a partial fetch looks like thin data.")
    ap.add_argument("--check", action="store_true",
                    help="verify the class table offline and exit")
    ap.add_argument("--query", action="store_true",
                    help="print the Overpass query and exit")
    return ap


def run(spec, argv=None, ap=None):
    a = ap.parse_args(argv)
    if a.check:
        problems = check_spec(spec)
        for p in problems:
            print(f"  [!] {p}")
        print(f"{len(problems)} problem(s)")
        return 1 if problems else 0
    if a.query:
        print(build_query(spec))
        return 0
    only = [s for s in a.only.split(",") if s.strip()] if a.only else None
    build_state(spec, a.state, a.out, only=only, deadline_s=a.deadline,
                allow_partial=a.allow_partial)
    return 0
