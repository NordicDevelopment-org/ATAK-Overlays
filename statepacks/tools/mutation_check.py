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

Add an entry whenever you fix something that a test should have caught. The
question it answers is not "is there a test for this" but "would the test fail
if the behaviour went away".
"""
import re
import subprocess
import sys

SEED = "statepacks/seed_le_contacts.py"
BUILD = "statepacks/build_county_pack.py"

MUTATIONS = {
    SEED: [
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
    ],
    BUILD: [
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
