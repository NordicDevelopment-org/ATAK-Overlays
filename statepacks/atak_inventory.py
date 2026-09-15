#!/usr/bin/env python3
"""What is in the ATAK overlays folder, and what is wrong with it.

    python3 atak_inventory.py                      # the default ATAK folder
    python3 atak_inventory.py --dir /some/path
    python3 atak_inventory.py --quick              # names and sizes only
    python3 atak_inventory.py --json out.json      # machine-readable

`atak-list.sh` answers "what files are there". This answers the question you
actually have when a pack looks wrong on the tablet: what is inside each one,
where did it come from, and is anything here fighting with anything else.

It reads. It never deletes, moves or rewrites anything - it prints the
`atak-remove.sh` line and leaves the decision to you.

WHAT IT FLAGS, and why each one is worth a line:

  * TWO EDITIONS OF ONE PACK. "__" separates a pack's identity from its
    version, so MN_Counties__Current_2026_09_14 supersedes an older
    MN_Counties__*. Two live editions means ATAK draws the state twice.
  * A PRE-"__" ANCESTOR. A file with one underscore carries no version, so
    atak-install.sh can never retire it - it is invisible to the mechanism
    that exists to prevent exactly this. That happened for real:
    MN_Counties_Current_2026_09_14.kmz sat next to the double-underscore
    edition for a day, drawing every county twice.
  * NO PROVENANCE. A placemark with no source, licence or retrieval date is
    a dot on a map you cannot check. Rule 4 of this project exists because
    that is worse than no dot.
  * NO PLACEMARKS. A KMZ that parses but draws nothing is a failed build that
    got installed anyway.
"""
import argparse
import json
import os
import re
import sys
import time
import zipfile

DEFAULT_DIR = "/storage/emulated/0/atak/overlays"

# Text that means a document says where it came from. Matched case-insensitively
# against doc.kml. Deliberately broad: the question is "does this file tell you
# anything about its origin", not "was it built by this repo".
PROVENANCE_HINTS = ("source", "licence", "license", "retrieved", "fetched",
                    "openstreetmap", "census", "tiger", "attribution")


def human(n):
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}G"


def family_of(name):
    """(identity, edition). "__" is the boundary; one underscore is not it.

    MN_Counties__Current_2026_09_14.kmz -> ("MN_Counties", "Current_2026_09_14")
    MN_Counties_Current_2026_09_14.kmz  -> ("MN_Counties_Current_2026_09_14", "")
    The second has no version, which is the whole problem with it.
    """
    stem = re.sub(r"\.(kmz|kml)$", "", name, flags=re.I)
    if "__" in stem:
        ident, _, edition = stem.partition("__")
        return ident, edition
    return stem, ""


def normal_name(raw):
    """Fold a placemark name to its identity.

    "Aitkin", "Aitkin County" and "AITKIN CO." are one county written three
    ways. Comparing them raw finds nothing, which is how 87 duplicate
    boundaries sat on the map looking fine to every check in this file.
    """
    n = re.sub(r"<[^>]+>", " ", raw or "").strip()
    # A trailing ", MN" is how one builder writes what another leaves off.
    # Bounded on purpose: a comma, two letters, end of string. Dropping any
    # trailing two-letter token would eat real names.
    n = re.sub(r",\s*[A-Za-z]{2}\s*$", "", n).lower()
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    n = re.sub(r"\b(county|co|parish|borough|city|of|the)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def contained_in(rows, min_names=1):
    """(inner, outer, n) for every pack whose placemarks are all in another.

    Strict containment only, and never both ways: two packs with identical
    name sets are the DUPLICATE case and are reported there. This is the
    asymmetric case - a small pack wholly swallowed by a big one, which is
    what a per-county file is next to a whole-state pack.
    """
    named = [r for r in rows if r.get("pm_names")]
    out = []
    for inner in named:
        a = inner["pm_names"]
        if len(a) < min_names:
            continue
        for outer in named:
            if outer is inner or not a <= outer["pm_names"]:
                continue
            if outer["pm_names"] <= a:          # identical, not contained
                continue
            out.append((inner["name"], outer["name"], len(a)))
            break
    return out


def inspect_kmz(path, deep=True):
    """Read one overlay. Returns a dict; never raises on a bad file."""
    out = {"name": os.path.basename(path), "bytes": 0, "mtime": "",
           "mtime_raw": 0.0, "placemarks": None, "folders": [],
           "provenance": None, "pm_names": set(), "error": None}
    try:
        st = os.stat(path)
        out["bytes"] = st.st_size
        # Display string is minute-precision on purpose - nobody needs
        # seconds in a listing. "Newest edition" is decided on mtime_raw,
        # the untruncated float, because two builds inside one minute is not
        # a hypothetical: it happened repeatedly in the session that found
        # this bug.
        out["mtime_raw"] = st.st_mtime
        out["mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
    except OSError as ex:                                   # noqa: BLE001
        out["error"] = f"cannot stat: {ex}"
        return out
    if not deep:
        return out
    try:
        if path.lower().endswith(".kml"):
            with open(path, "rb") as fh:
                doc = fh.read()
        else:
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".kml")]
                if not names:
                    out["error"] = "no .kml inside this .kmz"
                    return out
                # doc.kml is the convention; take it if present, else the first.
                pick = next((n for n in names if n.lower().endswith("doc.kml")),
                            names[0])
                doc = z.read(pick)
    except (zipfile.BadZipFile, OSError, KeyError) as ex:    # noqa: BLE001
        out["error"] = f"unreadable: {ex}"
        return out
    text = doc.decode("utf-8", "replace")
    out["placemarks"] = text.count("<Placemark")
    # Placemark names, normalised, so one pack can be recognised as already
    # being inside another. 87 one-county files and a single 87-county pack
    # are not duplicates by filename and never will be - they are duplicates
    # by content, and content is the only place to see it.
    out["pm_names"] = {normal_name(n) for n in
                       re.findall(r"<Placemark>.*?<name>([^<]{1,80})</name>",
                                  text, re.S)}
    out["pm_names"].discard("")
    # Folder names are the eye-toggle tree in Overlay Manager, so they are what
    # a person actually sees. Only the first few matter for a listing.
    out["folders"] = re.findall(r"<Folder>\s*<name>([^<]{1,60})</name>", text)[:8]
    low = text.lower()
    out["provenance"] = sorted({h for h in PROVENANCE_HINTS if h in low})
    return out


def scan(directory, deep=True, only=None):
    """Every overlay in one directory.

    `only` is a substring filter applied to the FILENAME before anything is
    opened. It matters: answering a question about 87 small county files
    should not unzip a 9.2 MB camera export to do it. Over a phone's storage
    layer that is the difference between a second and a minute.
    """
    try:
        entries = sorted(os.listdir(directory))
    except OSError as ex:                                    # noqa: BLE001
        raise SystemExit(f"cannot read {directory}: {ex}")
    files = [os.path.join(directory, n) for n in entries
             if n.lower().endswith((".kmz", ".kml"))
             and os.path.isfile(os.path.join(directory, n))
             and (not only or only.lower() in n.lower())]
    if not deep:
        return [inspect_kmz(p, deep=False) for p in files]
    # Opening and unzipping 100+ files off a phone's shared storage is slow
    # enough to look like a hang, and a tool that looks hung gets killed
    # halfway. Progress goes to stderr so `| grep` still works.
    # Only to a terminal. A \r progress line redirected to a file is 100 lines
    # of overwritten junk in the middle of someone's saved output.
    #
    # And it has to be SHORT. The first version printed the filename too, ran
    # past 60 characters, and wrapped on a phone - at which point \r returns to
    # the start of the wrapped row rather than the line, so every update was
    # left on screen as a smear instead of a counter ticking in place. Keep it
    # inside the narrowest terminal anyone runs this in.
    show = sys.stderr.isatty()
    rows, total = [], len(files)
    for i, path in enumerate(files, 1):
        if show:
            print(f"\r  reading {i}/{total}   ", end="", file=sys.stderr,
                  flush=True)
        rows.append(inspect_kmz(path, deep=True))
    if show:
        print("\r" + " " * 24 + "\r", end="", file=sys.stderr, flush=True)
    return rows


def find_problems(rows):
    """[(severity, name, what, suggested_fix)] - reported, never acted on."""
    problems = []
    for inner, outer, n in contained_in(rows):
        problems.append((
            "CONTAINED", inner,
            f"all {n} of its placemark(s) are already in {outer} - "
            f"whatever it draws is being drawn twice",
            f"delete it and keep {outer}, which carries the provenance, "
            f"or say why this copy is different"))

    fams = {}
    for r in rows:
        ident, edition = family_of(r["name"])
        fams.setdefault(ident, []).append((edition, r))

    for ident, members in sorted(fams.items()):
        versioned = [(e, r) for e, r in members if e]
        if len(versioned) > 1:
            # Raw float mtime, not the minute-truncated display string - a
            # string tie used to hand "newest" to whichever file sorted first
            # alphabetically, which for date-stamped names is the OLDER one.
            # Verified: two builds run seconds apart in one session compared
            # equal under the old string key.
            newest = max(versioned, key=lambda p: p[1]["mtime_raw"])
            times = {r["mtime_raw"] for _e, r in versioned}
            if len(times) < len(versioned):
                # Still tied even at full precision - real, not hypothetical:
                # unzip and some copy tools give every extracted file the
                # archive's own recorded time, so a whole build's files can
                # share one identical mtime. Guessing which is newer here
                # would be exactly the invented value rule 1 forbids, so this
                # says the file system cannot answer it rather than picking.
                names = ", ".join(r["name"] for _e, r in versioned)
                problems.append((
                    "DUPLICATE", ident,
                    f"{len(versioned)} editions with IDENTICAL timestamps, "
                    f"so file time cannot say which is newer: {names}",
                    "check the edition string in each filename by hand, or "
                    "delete all and rebuild one"))
                continue
            for e, r in versioned:
                if r is not newest[1]:
                    problems.append((
                        "DUPLICATE", r["name"],
                        f"an older edition of {ident}; "
                        f"{newest[1]['name']} is newer",
                        f"atak-remove.sh '{r['name']}'"))
        # A pre-"__" file whose whole stem starts with a versioned family's
        # identity is that family without a version - the case nothing retires.
        for e, r in members:
            if e:
                continue
            for other, _m in fams.items():
                if other == ident or not other:
                    continue
                if ident.startswith(other + "_") and any(
                        ed for ed, _rr in fams[other]):
                    problems.append((
                        "STALE", r["name"],
                        f"looks like a pre-'__' edition of {other}. It carries "
                        f"no version, so nothing will ever retire it and ATAK "
                        f"draws this pack twice",
                        f"atak-remove.sh '{r['name']}'"))
                    break

    for r in rows:
        if r["error"]:
            problems.append(("BROKEN", r["name"], r["error"],
                             "rebuild it, or remove it"))
        elif r["placemarks"] == 0:
            problems.append(("EMPTY", r["name"],
                             "parses, but contains no placemarks - it draws "
                             "nothing", "rebuild it, or remove it"))
        elif r["placemarks"] is not None and not r["provenance"]:
            problems.append(("NO SOURCE", r["name"],
                             "no source, licence or date anywhere in the "
                             "document - a dot you cannot check",
                             "rebuild it from a pipeline that records "
                             "provenance"))
    order = {"BROKEN": 0, "DUPLICATE": 1, "STALE": 2, "EMPTY": 3, "NO SOURCE": 4}
    return sorted(problems, key=lambda p: (order.get(p[0], 9), p[1]))


def report(directory, rows, problems, log=print, deep=True):
    total = sum(r["bytes"] for r in rows)
    log(f"\nATAK OVERLAY INVENTORY - {directory}")
    log(f"  {len(rows)} file(s), {human(total)} total\n")
    if not rows:
        log("  (empty)")
        return
    log(f"  {'file':<46} {'size':>7}  {'placemarks':>10}  {'when':<16} src")
    for r in sorted(rows, key=lambda x: x["name"].lower()):
        pm = "-" if r["placemarks"] is None else f"{r['placemarks']:,}"
        src = "yes" if r["provenance"] else ("-" if deep else "?")
        if r["error"]:
            pm, src = "ERR", "-"
        log(f"  {r['name']:<46} {human(r['bytes']):>7}  {pm:>10}  "
            f"{r['mtime']:<16} {src}")

    if problems:
        log(f"\n  {len(problems)} thing(s) worth looking at:\n")
        for sev, name, what, fix in problems:
            log(f"    [{sev}] {name}")
            log(f"        {what}")
            log(f"        -> {fix}")
    elif deep:
        log("\n  Nothing looks wrong.")
    else:
        log("\n  Nothing looks wrong in the filenames and timestamps checked "
            "under --quick. Run without --quick to look inside the files.")
    log("")
    log("  Nothing here was changed. Removal lines are printed, not run.")
    log("")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Inventory the ATAK overlays folder. Reads only.")
    ap.add_argument("--dir", default=os.environ.get("ATAK_DIR", DEFAULT_DIR))
    ap.add_argument("--quick", action="store_true",
                    help="names, sizes and dates only; do not open the files")
    ap.add_argument("--json", metavar="FILE", help="also write the raw findings")
    ap.add_argument("--names", metavar="SUBSTR", nargs="?", const="",
                    help="print the placemark names each file carries, folded "
                         "the way containment compares them; optionally only "
                         "files whose name contains SUBSTR")
    a = ap.parse_args(argv)
    deep = not a.quick
    if a.names is not None:
        # Two passes on purpose. The files being ASKED about are read first
        # and printed, then the rest, so a name query answers immediately
        # instead of after every file in the folder.
        rows = scan(a.dir, deep=True, only=a.names)
    else:
        rows = scan(a.dir, deep=deep)

    if a.names is not None:
        # Containment compares folded names. If two packs that clearly cover
        # the same ground are not being reported, this is the thing to look
        # at: the fold is what decides, not the filename.
        if a.names:
            print(f"  only files whose name contains {a.names!r}; "
                  f"{len(rows)} matched")
        for r in rows:
            names = sorted(r.get("pm_names") or [])
            print(f"\n{r['name']}  ({len(names)} distinct folded name(s))")
            if r.get("error"):
                print(f"    [!] {r['error']}")
            for n in names[:12]:
                print(f"    {n!r}")
            if len(names) > 12:
                print(f"    ... and {len(names) - 12} more")
        return 0
    # find_problems ALWAYS runs. DUPLICATE and STALE need only name, mtime
    # and error - fields --quick already collects without opening a file -
    # and the content checks (CONTAINED, EMPTY, NO SOURCE) are individually
    # guarded to no-op on the None/empty values --quick leaves in place.
    # Gating the whole function on `deep` used to mean a folder with the
    # double-county-boundary bug this tool exists to catch printed "Nothing
    # looks wrong" under --quick - a genuinely disabled check reporting a
    # clean bill of health is worse than no check at all.
    problems = find_problems(rows)
    report(a.dir, rows, problems, deep=deep)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"dir": a.dir, "scanned": time.strftime("%Y-%m-%d"),
                       "files": rows,
                       "problems": [{"severity": s, "file": n, "what": w,
                                     "fix": f} for s, n, w, f in problems]},
                      fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        print(f"  findings written to {a.json}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # `atak_inventory.py | head` closes the pipe partway through a long
        # report. That is a reasonable thing to do and must not end in a
        # traceback.
        try:
            sys.stdout.close()
        finally:
            os._exit(0)
