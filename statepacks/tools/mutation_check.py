#!/usr/bin/env python3
"""Mutation-test the statepacks builders: break one behaviour, run the suite,
put it back. A mutation that leaves every test green means that behaviour is
not actually tested, however many tests mention it.

    python3 statepacks/tools/mutation_check.py          # both files
    python3 statepacks/tools/mutation_check.py seed     # just the seeder
    python3 statepacks/tools/mutation_check.py build    # just the pack builder

Every entry is a rule this project cares about, written as the mistake: "pad a
missing county code into a fake FIPS", "show a missing value instead of
omitting it", "answer a 429 by splitting the tile". Six of these survived when
they were first written - among them "if no sheriff phone, then don't", which
nothing was checking, and OSM_JOBS = 1, which quietly undoes the whole point of
fetching tiles concurrently.

IT REVERTS WITH `git checkout --`, so it refuses to run against uncommitted
changes and restores the file in a finally. It has eaten uncommitted work once;
that is why both guards are here.

A `finally` does not run when the process is KILLED, so a `timeout`, a Ctrl-C
or a closed terminal used to leave a deliberately-broken file in the tree -
which happened, leaving build_county_pack.py with the "__" boundary removed and
a stop-hook asking to commit it. SIGTERM and SIGINT now restore before exiting,
and a run that dies some other way leaves `git checkout -- <file>` as the fix.
This takes minutes: do not wrap it in a short `timeout`.

Add an entry whenever you fix something that a test should have caught. The
question it answers is not "is there a test for this" but "would the test fail
if the behaviour went away".
"""
import re
import signal
import subprocess
import sys

SEED = "statepacks/seed_le_contacts.py"
BUILD = "statepacks/build_county_pack.py"
INV = "statepacks/atak_inventory.py"
DUP = "statepacks/atak_find_dupes.py"
SYM = "statepacks/symbology.py"
EMG = "statepacks/build_emergency_pack.py"
MPK = "statepacks/merge_packs.py"
OSP = "statepacks/osm_pack.py"
RPT = "statepacks/repeater_diagnose.py"
NWR = "statepacks/build_nwr_pack.py"
PWR = "statepacks/build_power_pack.py"
GLY = "statepacks/glyphs.py"
REP = "statepacks/build_repeater_pack.py"

MUTATIONS = {
    SEED: [
        ("let an invalid query reach the network",
         "    validate_query(query or OSM_QUERY)", "    pass"),
        ("cache an Overpass remark error as an empty tile",
         "            if bad is not None:\n                raise bad",
         "            if False:\n                raise bad"),
        ("lose a good response when the cache write fails",
         "                    except OSError as ex:",
         "                    except ZeroDivisionError as ex:"),
        ("cut the socket at the server's own timeout",
         "want = timeout + OSM_SOCKET_SLACK", "want = timeout"),
        ("fetch tiles one at a time after all",
         "OSM_JOBS = 3", "OSM_JOBS = 1"),
        ("unwire --jobs and --no-split",
         "jobs=args.jobs,\n                         shapes=shapes, "
         "split=not args.no_split,", "shapes=shapes,"),
        ("unwire --refresh-shapes",
         "county_shapes(sfp, refresh=a.refresh_shapes,",
         "county_shapes(sfp, refresh=False,"),
        ("wait on a busy mirror instead of skipping it",
         "                    if phase == 0:\n"
         "                        if not lock.acquire(blocking=False):",
         "                    if False:\n"
         "                        if not lock.acquire(blocking=False):"),
        ("report our own scheduling as a mirror failure",
         "raise MirrorsBusy(", "raise RuntimeError("),
        ("serve a tile cache entry regardless of age",
         "if hit and _cache_fresh(hit[1], ttl_days):", "if hit:"),
        ("let the record order depend on how the fetch was carved up",
         "elements.sort(key=lambda p: _element_key(p[0]))", "    pass"),
        ("report a never-fetched county as 'not in the source'",
         'uncovered=coverage.get("uncovered") or ()', "uncovered=()"),
        ("answer a 429 by splitting the tile",
         "if k in bad and not isinstance(bad[k], (TimeoutError, MirrorsBusy,\n"
         "                                                     RateLimited))]",
         "if k in bad and not isinstance(bad[k], (TimeoutError, MirrorsBusy))]"),
        ("re-earn one tile's 429 with every other tile",
         "                if cool(url) > 0:", "                if False:"),
        ("lower the server timeout that was measured to work",
         "OSM_SERVER_TIMEOUT_S = 90", "OSM_SERVER_TIMEOUT_S = 30"),
        ("read a city PD's name as if it were the county's",
         "    return bool(county_name) and county_name.lower() in agency.lower()",
         "    return True"),
        ("call one county's own name a vocabulary term",
         "def discover_terms(unmatched, match, county_names, min_counties=2,",
         "def discover_terms(unmatched, match, county_names, min_counties=1,"),
        ("print every window onto one term as a separate finding",
         "        if any(n == m and (g in k or k in g) for k, m in kept):",
         "        if False:"),
        ("only ever test Minnesota's vocabulary against another state",
         "    found = discover_terms(\n"
         '        {g: [r["agency"] for r in by_county[g]] for g in unmatched},'
         "\n        match, names)",
         "    found = []"),
        ("require the second f, and lose Clark County WI",
         'SHERIFF_RX = r"sherr?if"', 'SHERIFF_RX = r"sherr?iff"'),
        ("call a record with no name at all widenable",
         "    unmatched = sorted(g for g in geoids if g not in chosen and named(g))",
         "    unmatched = sorted(g for g in geoids if g not in chosen and by_county.get(g))"),
        ("miss a two-edit typo, which is the only kind there is",
         "def near_misses(unmatched, chosen, county_names, min_len=6, limit=2):",
         "def near_misses(unmatched, chosen, county_names, min_len=6, limit=1):"),
        ("report a city PD's typo as the county's",
         "            if not is_county_level(a, county_names.get(geoid, \"\")):\n"
         "                continue\n"
         "            for w in re.split",
         "            if False:\n"
         "                continue\n"
         "            for w in re.split"),
        ("serve one query's cached tiles to a different query",
         'return os.path.join(CACHE_DIR, f"osm_{prefix}_{key}.json")',
         'return os.path.join(CACHE_DIR, f"osm_police_{key}.json")'),
        ("ignore the caller's query and always ask about police",
         "    q = (query or OSM_QUERY).format(timeout=timeout, s=s_, w=w, n=n, e=e)",
         "    q = OSM_QUERY.format(timeout=timeout, s=s_, w=w, n=n, e=e)"),
        ("drop the caller's parse and hand back police rows",
         "        if parse is not None:", "        if False:"),
        ("unwire --gaps-dump",
         "                            dump=a.gaps_dump)", "                            dump=None)"),
    ],
    REP: [
        ("ship a frequency in the tone column as if it were a tone",
         '    if re.match(r"^\\d{2,3}\\.\\d$", t):\n'
         '        return "unrecognised", "not a CTCSS tone - looks like a frequency"',
         '    if False:\n        return "unrecognised", ""'),
        ("let a 4x join fan-out through as 2080 real repeaters",
         "        if key in seen:\n            continue", "        if False:\n            continue"),
        ("compute a missing input frequency from the usual band offset",
         '         "" if in_mhz else "not in the source; not computed from an offset"),',
         '         ""),'),
        ("call a town centroid a tower location",
         '    src_note = ("approximate - this is the centre of the town, not the tower"\n'
         '                if "centroid" in src.lower() else "")',
         '    src_note = ""'),
    ],
    GLY: [
        ("skip size normalization, so glyphs clip and sit unevenly",
         "    subs = normalize(GLYPHS[name]())", "    subs = GLYPHS[name]()"),
        ("normalize to an extent that clips the outline",
         "GLYPH_EXTENT = 1.66", "GLYPH_EXTENT = 1.95"),
        ("stretch a glyph to fill the box, losing its aspect ratio",
         "    longest = max(w, h)", "    longest = min(w, h)"),
        ("fall back to a circle for a glyph nobody defined",
         '        raise KeyError(f"unknown glyph {name!r}. Known: '
         "{', '.join(glyph_names())}\")",
         '        subs = GLYPHS["bolt"]()'),
    ],
    PWR: [
        ("point an icon at a server the field tablet cannot reach",
         '                   f"<Icon><href>icons/{name}.png</href></Icon></IconStyle>"',
         '                   f"<Icon><href>http://maps.google.com/mapfiles/kml/'
         'shapes/electronics.png</href></Icon></IconStyle>"'),
        ("reference icons without putting them in the zip",
         "    size = bcp.write_kmz(path, kml, icons)", "    size = bcp.write_kmz(path, kml)"),
        ("merge nameplate and summer capacity into one number",
         '        ("Nameplate capacity", f"{nameplate:,.1f} MW" if nameplate is not None else ""),\n'
         '        ("Max summer capacity", f"{summer:,.1f} MW" if summer is not None else ""),',
         '        ("Capacity", f"{nameplate:,.1f} MW" if nameplate is not None else ""),'),
        ("turn an unparseable capacity into zero",
         "    except (TypeError, ValueError):\n        return None",
         "    except (TypeError, ValueError):\n        return 0.0"),
        ("let 519 solar sites import switched on",
         '    hidden = fuel not in DEFAULT_ON', "    hidden = False"),
        ("replace EIA's reporting period with the build date",
         'f"EIA reporting period: {bcp.esc(str(p.get(\'Period\') or \'not stated\'))}<br/>"',
         'f"EIA reporting period: {bcp.esc(meta[\'built\'])}<br/>"'),
    ],
    NWR: [
        ("find the first bracket instead of the cclData assignment",
         r'    m = re.search(r"var\s+cclData\s*=\s*(\[.*\])\s*;?\s*$", text,',
         r'    m = re.search(r"(\[.*\])", text,'),
        ("print the site twice when the source repeats itself",
         "    if nm and loc and nm.lower() != loc.lower():",
         "    if nm and loc:"),
        ("place a transmitter that has no coordinate",
         "    except (TypeError, ValueError):\n"
         "        return None                       # no coordinate, no point. Not guessed.",
         "    except (TypeError, ValueError):\n        lat, lon = 0.0, 0.0"),
        ("hide an out-of-service transmitter among the working ones",
         '        status = str(s.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"',
         '        status = "NORMAL"'),
    ],
    RPT: [
        ("stop collapsing whitespace-only duplicates",
         "        k = _key(f, squash=True)", "        k = _key(f)"),
        ("rewrite the kept record with the squashed value",
         "    if isinstance(value, str):\n"
         '        return re.sub(r"\\s+", " ", value).strip()',
         "    if isinstance(value, str):\n        return value"),
        ("call one coordinate in two towns a normal two-entry site",
         "        (two_towns if len(cities) > 1 else same_town).append(",
         "        (same_town if True else two_towns).append("),
        ("drop the contested entries instead of reporting them",
         "    kept, n_marked = mark_contested(kept)",
         "    kept = [f for f in kept if True]; n_marked = 0"),
        ("keep the disputed position out of the popup",
         '        ("Position disputed",', '        ("_unused",'),
        ("anchor the query on a parent key the data does not carry",
         '    "communication:amateur_radio:repeater:frequency_out",\n'
         '    "communication:amateur_radio:repeater:frequency_in",',
         '    "communication:amateur_radio:repeater",'),
        ("let a generic key anchor a statewide query",
         'TOO_GENERIC = ("name", "official_name",', 'TOO_GENERIC = ("zzz",'),
        ("keep one cache namespace across a changed query",
         '    h = hashlib.sha1(query.encode("utf-8")).hexdigest()[:8]\n'
         "    return f\"repeaters{'_deep' if deep else ''}_{h}\"",
         '    return "repeaters"'),
    ],
    INV: [
        ("print a progress line long enough to wrap a phone terminal",
         '            print(f"\\r  reading {i}/{total}   ", end="", file=sys.stderr,',
         '            print(f"\\r  reading {i}/{total} {os.path.basename(path)[:40]:<40}", end="", file=sys.stderr,'),
        ("open every file in the folder to answer a filtered name query",
         "             and (not only or only.lower() in n.lower())]",
         "             ]"),
        ("stop folding a trailing state suffix, so two spellings never match",
         '    n = re.sub(r",\\s*[A-Za-z]{2}\\s*$", "", n).lower()',
         "    n = n.lower()"),
        ("read every file with no sign of progress, so it looks hung",
         "    show = sys.stderr.isatty()", "    show = False"),
        ("spray a progress line into redirected output",
         "    show = sys.stderr.isatty()", "    show = True"),
        ("stop noticing a pack whose placemarks are all inside another",
         "    for inner, outer, n in contained_in(rows):",
         "    for inner, outer, n in []:"),
        ("compare placemark names raw, so 'Aitkin' never matches 'Aitkin County'",
         '    n = re.sub(r"\\b(county|co|parish|borough|city|of|the)\\b", " ", n)',
         "    n = n"),
        ("report identical packs as contained, which deletes the layer",
         "            if outer[\"pm_names\"] <= a:          # identical, not contained\n"
         "                continue",
         "            pass"),
        ("read one underscore as the version boundary",
         '        ident, _, edition = stem.partition("__")',
         '        ident, _, edition = stem.partition("_")'),
        ("call the inventory's findings a licence to delete",
         '    log("  Nothing here was changed. Removal lines are printed, not run.")',
         "    pass"),
        ("count placemarks in quick mode, which never opened the file",
         "    if not deep:\n        return out", "    if False:\n        return out"),
    ],
    DUP: [
        ("sweep only the overlays folder, which is the bug it exists for",
         'ROOTS = ["/storage/emulated/0/atak", "/sdcard/atak"]',
         'ROOTS = ["/storage/emulated/0/atak/overlays"]'),
        ("drop a stale edition from the report when a byte-copy exists",
         '        if "__" not in f["name"] or f["sha"] in first_of_sha:',
         '        if "__" not in f["name"] or f["sha"] in first_of_sha '
         'or f["sha"] in same_bytes:'),
        ("call two files of the same name an edition pair",
         '        if "__" not in f["name"] or f["sha"] in first_of_sha:',
         '        if f["sha"] in first_of_sha:'),
        ("follow a symlinked root twice",
         "                real = os.path.realpath(path)",
         "                real = path"),
    ],
    OSP: [
        ("skip the clip, so the pack carries the neighbouring states",
         "    rows = clip_to_state(rows, state, log=log)", "    pass"),
        ("file a point outside every county under the last one tested",
         "        if geoid is None:\n            dropped += 1\n            continue",
         "        if geoid is None:\n            geoid = index[0][0]"),
        ("clip after counting, so the report includes the neighbours",
         "    rows = clip_to_state(rows, state, log=log)\n"
         "    report(spec, rows, log=log, classes=chosen)",
         "    report(spec, rows, log=log, classes=chosen)\n"
         "    rows = clip_to_state(rows, state, log=log)"),
        ("trust the bounding box instead of the polygon",
         "            if any(seed.point_in_polygon(x, y, rings) for rings in polys):",
         "            if True:"),
    ],
    MPK: [
        ("write a half-filled label, which reads as a real one",
         "        if v is None or v == \"\":\n            return None",
         "        if v is None or v == \"\":\n            values[key] = \"\"; continue"),
        ("sort folders lexicographically, putting 115 kV before 69 kV",
         '    m = re.match(r"\\s*(-?\\d+(?:\\.\\d+)?)", label)\n'
         '    return (0, float(m.group(1)), "") if m else (1, 0.0, label.lower())',
         "    return (0, 0.0, label.lower())"),
        ("drop features missing the folder field instead of naming them",
         '    return value.strip() or missing', "    return value.strip()"),
        ("claim one provenance for a merge of several sources",
         "    provenance = []\n    for p in live:",
         "    provenance = [live[0][\"description\"]]\n    for p in []:"),
        ("scan a list per placemark instead of hashing ids",
         "        kept = {id(pm) for pm in placemarks}", "        kept = placemarks"),
    ],
    EMG: [
        ("set visibility on the folder only, so it imports off and renders on",
         "        body = \"\".join(placemark(r, style_id, visible=not off) for r in group)",
         "        body = \"\".join(placemark(r, style_id) for r in group)"),
        ("import every layer switched on, however dense",
         '    DEFAULT_OFF = {"schools", "government"}', "    DEFAULT_OFF = set()"),
        ("report a zero for every class, including ones nobody asked for",
         "    asked = classes if classes is not None else CLASSES",
         "    asked = CLASSES"),
        ("stop naming the classes that were skipped",
         "    skipped = [lbl for lay, _s, lbl in CLASSES if lay not in asked_names]",
         "    skipped = []"),
        ("write the bounding box in Overpass Turbo syntax, which every mirror 400s",
         '    box = "({s:.4f},{w:.4f},{n:.4f},{e:.4f})"', '    box = "({{bbox}})"'),
        ("stop validating the query in the offline check",
         "        seed.validate_query(build_query())", "        pass"),
        ("quote key and value as one string, the bug that returns nothing",
         '        return f\'["{key}"="{value}"]\'',
         '        return f\'["{key}={value}"]\''),
        ("let (?i) reach the Overpass server as literal text",
         '        return f\'["{key}"~"{value[4:]}",i]\'',
         '        return f\'["{key}"~"{value}"]\''),
        ("accept an empty selector, which matches the whole bounding box",
         '    if not selector:\n        raise ValueError("empty selector matches everything")',
         "    if False:\n        pass"),
        ("treat a multi-clause selector as satisfied by its first clause",
         "    for key, op, value in selector:", "    for key, op, value in selector[:1]:"),
        ("file an unmatched element under the first class instead of dropping it",
         "    return None\n\n\ndef parse_element(", "    return CLASSES[0][0]\n\n\ndef parse_element("),
        ("drop empty folders, so nothing found looks like nothing asked",
         "        folders.append(", "        if not group:\n            continue\n        folders.append("),
        ("skip the offline class check before a 17-minute live run",
         "    problems = check_classes()\n    if problems:", "    problems = []\n    if problems:"),
    ],
    SYM: [
        ("give nuclear the same symbol as everything else",
         '    "nuclear":        ("trefoil",  (255, 240, 60)),',
         '    "nuclear":        ("bolt",     (255, 240, 60)),'),
        ("give two sectors the same colour",
         '    "Communications":     (170, 230, 140),   # green',
         '    "Communications":     (255, 209, 64),    # green'),
        ("hang a point icon on a line layer",
         '    "pipelines":           (None,         "Energy - Oil & Gas", "line"),',
         '    "pipelines":           ("pipeline",   "Energy - Oil & Gas", "line"),'),
        ("stop checking that the table covers the catalog",
         "    missing = catalog_layers(path) - set(LAYERS)",
         "    missing = set()  # catalog_layers(path) - set(LAYERS)"),
        ("name a glyph that was never drawn",
         '    "dams":                ("dam",        "Water",              "point"),',
         '    "dams":                ("dam_icon",   "Water",              "point"),'),
    ],
    BUILD: [
        ("stamp the filename with the date alone, as it was",
         '    return f"{safe(str(built).replace(\'-\', \'_\'))}_{h.hexdigest()[:6]}"',
         '    return safe(str(built).replace("-", "_"))'),
        ("digest only the kml, leaving a changed icon invisible",
         "    for name, data in sorted((icons or {}).items()):\n"
         "        h.update(name.encode(\"utf-8\"))\n"
         "        h.update(data)",
         "    pass"),
        ("pad a missing county code into a fake FIPS",
         "    return None\n\n\ndef county_label(props):",
         '    return (sfp or "00") + (cfp or "000")\n\n\ndef county_label(props):'),
        ("append ' County' to every name",
         '    for key in ("NAMELSAD", "NAME"):\n        v = p.get(key)\n'
         "        if v:\n            return str(v).strip()",
         '    for key in ("NAMELSAD", "NAME"):\n        v = p.get(key)\n'
         '        if v:\n            return str(v).strip() + " County"'),
        ("stop stripping XML-illegal control characters",
         '    s = _XML_ILLEGAL.sub("", str(s))', "    s = str(s)"),
        ("stop paging on the server's own limit flag",
         "                if not batch or not more:", "                if True:"),
        ("hardcode the TIGER vintage",
         '    m = re.search(r"/(\\d+)/?$", url)',
         '    return "2024"\n    m = re.search(r"/(\\d+)/?$", url)'),
        ("show a missing value instead of omitting it",
         "    present = [(label, sv) for label, sv in rows if sv]",
         "    present = list(rows)"),
        ("drop the grey provenance tag",
         '            line += f\' <font color="{GREY}">[{esc(tag)}]</font>\'',
         "            pass"),
        ("stop bolding the value",
         '        line = f"{esc(label)}: <b>{esc(value)}</b>"',
         '        line = f"{esc(label)}: {esc(value)}"'),
        ("use the projected area instead of ALAND",
         "        def area(label, *keys):\n            for key in keys:",
         '        def area(label, *keys):\n'
         '            keys = ("SHAPE__AREA",) + tuple(keys)\n'
         "            for key in keys:"),
        ("ignore the .local.csv overlay",
         '    for name in (filename, f"{stem}.local.csv"):',
         "    for name in (filename,):"),
        ("drop the __ identity/version boundary from filenames",
         'f"{state_abbr}_Counties__{safe(stamp)}.kmz"',
         'f"{state_abbr}_Counties_{safe(stamp)}.kmz"'),
        ("let one county name become the pack's descriptor",
         '    if len(uniq) < 2:\n        return ""',
         '    if len(uniq) < 0:\n        return ""'),
    ],
}

TESTS = ["tests/test_statepacks.py", "tests/test_kmz.py"]


def failures():
    out = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-q"],
                         capture_output=True, text=True).stdout
    last = out.splitlines()[-1] if out else ""
    m = re.search(r"(\d+) failed", last)
    return int(m.group(1)) if m else 0


def check(path, entries):
    if subprocess.run(["git", "diff", "--quiet", "--", path]).returncode != 0:
        print(f"REFUSING: {path} has uncommitted changes - this script reverts "
              f"with 'git checkout --' and would delete them.", file=sys.stderr)
        return 1
    print(f"\n{path}")
    original = open(path, encoding="utf-8").read()
    survivors = 0

    # A finally does not run on SIGTERM. Without this, a killed run leaves the
    # file broken on purpose and says nothing about it.
    def restore_and_die(signum, _frame):
        open(path, "w", encoding="utf-8").write(original)
        print(f"\n  interrupted ({signal.Signals(signum).name}) - {path} "
              f"restored", file=sys.stderr)
        sys.exit(130)

    previous = [(sig, signal.signal(sig, restore_and_die))
                for sig in (signal.SIGTERM, signal.SIGINT)]
    try:
        for desc, old, new in entries:
            if old not in original:
                print(f"  {desc:52s} !! TARGET NOT FOUND - update this entry")
                survivors += 1
                continue
            open(path, "w", encoding="utf-8").write(original.replace(old, new, 1))
            n = failures()
            open(path, "w", encoding="utf-8").write(original)
            flag = "   <-- SURVIVES, so nothing tests this" if n == 0 else ""
            survivors += n == 0
            print(f"  {desc:52s} {n} test(s) fail{flag}")
    finally:
        open(path, "w", encoding="utf-8").write(original)
        for sig, handler in previous:
            signal.signal(sig, handler)
    return survivors


def main(argv):
    want = argv[1] if len(argv) > 1 else ""
    targets = [(p, e) for p, e in MUTATIONS.items()
               if not want or want in p]
    if not targets:
        print(f"nothing matches {want!r}; try 'seed' or 'build'", file=sys.stderr)
        return 2
    bad = sum(check(p, e) for p, e in targets)
    print(f"\n{bad} mutation(s) survived" if bad
          else "\nevery mutation is caught by at least one test")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
