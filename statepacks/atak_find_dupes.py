#!/usr/bin/env python3
"""Find every KML/KMZ anywhere in the ATAK tree, and say which ones collide.

    python3 atak_find_dupes.py                  # the whole /atak tree
    python3 atak_find_dupes.py --root /sdcard   # somewhere else too

WHY THIS EXISTS. `atak-list.sh`, `atak-remove.sh` and `atak_inventory.py` all
look at exactly one directory, /storage/emulated/0/atak/overlays, because that
is where this project puts packs. But ATAK reads more than that directory, and
Import Manager makes its own copy of anything imported through it. So a pack
removed from overlays/ can still be on the map, served from a copy nobody in
this repo has ever looked at.

This finds them. It is READ-ONLY - it prints removal lines, it does not run
them - because the only thing worse than a duplicate overlay is deleting the
wrong copy of the one you meant to keep.

Three kinds of collision are reported separately, because they need different
answers:

  SAME BYTES      one file, several places. Keep one, delete the rest; which
                  one you keep does not matter.
  SAME FAMILY     different editions of one pack (the `__` convention). Keep
                  the newest, delete the older - this is what atak-install.sh
                  does automatically for files it can recognise.
  SAME NAME       same basename, different bytes, no `__`. A judgement call:
                  nothing in the filename says which is newer, so look at the
                  size and date before choosing.
"""
import argparse
import hashlib
import os
import sys
import time

ROOTS = ["/storage/emulated/0/atak", "/sdcard/atak"]
EXTS = (".kmz", ".kml")


def human(n):
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0


def family_of(name):
    """The identity half of a pack filename.

    `__` is the identity/version boundary this project writes: everything
    before it names the pack, everything after names the edition. A file with
    no `__` has no edition, so its whole stem is the family - which is exactly
    why those files cannot be retired automatically.
    """
    stem = os.path.splitext(os.path.basename(name))[0]
    return stem.split("__")[0] if "__" in stem else stem


def scan(roots):
    """Every KML/KMZ under every root, each real file reported once."""
    seen_real, found = set(), []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if not fn.lower().endswith(EXTS):
                    continue
                path = os.path.join(dirpath, fn)
                # /sdcard is usually a symlink to /storage/emulated/0, so the
                # same file arrives twice. Count it once.
                real = os.path.realpath(path)
                if real in seen_real:
                    continue
                seen_real.add(real)
                try:
                    st = os.stat(path)
                except OSError as exc:
                    print(f"  [!] cannot stat {path}: {exc}")
                    continue
                found.append({"path": path, "size": st.st_size,
                              "mtime": st.st_mtime, "name": fn})
    return found


def digest(path, chunk=1 << 20):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(chunk), b""):
                h.update(block)
    except OSError as exc:
        return f"unreadable:{exc}"
    return h.hexdigest()


def group(files, keyfn):
    out = {}
    for f in files:
        out.setdefault(keyfn(f), []).append(f)
    return {k: v for k, v in out.items() if len(v) > 1}


def describe(f):
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(f["mtime"]))
    return f"{human(f['size']):>7}  {when}  {f['path']}"


def report(files, log=print):
    log(f"\nATAK KML/KMZ SWEEP - {len(files)} file(s)")
    log("=" * 72)

    by_dir = {}
    for f in files:
        by_dir.setdefault(os.path.dirname(f["path"]), []).append(f)
    log("\nWHERE THEY LIVE")
    for d in sorted(by_dir, key=lambda d: -len(by_dir[d])):
        total = sum(x["size"] for x in by_dir[d])
        log(f"  {len(by_dir[d]):4}  {human(total):>8}  {d}")

    for f in files:
        f["sha"] = digest(f["path"])

    problems = 0

    same_bytes = group(files, lambda f: f["sha"])
    if same_bytes:
        log("\nSAME BYTES - one file in several places")
        log("  Keep any one of each group; the rest are wasted space and a")
        log("  duplicate layer. Which copy you keep does not matter.")
        for sha, grp in sorted(same_bytes.items(),
                               key=lambda kv: -kv[1][0]["size"]):
            problems += 1
            log(f"\n  {len(grp)} copies, {human(grp[0]['size'])} each:")
            for f in sorted(grp, key=lambda f: f["path"]):
                log("    " + describe(f))

    # Only a name carrying `__` has an edition. Two files both called
    # roads.kmz share a family name but not a version, so calling them an
    # edition pair would tell someone to "keep the newest" when nothing in
    # either name says which that is. They belong in SAME NAME instead.
    #
    # One entry per distinct content, NOT per file: an old edition that also
    # has a byte-identical copy elsewhere is still an old edition sitting on
    # the map next to the new one. Filtering it out because SAME BYTES already
    # mentioned it is how the stale pack stays installed.
    first_of_sha, editions = {}, []
    for f in files:
        if "__" not in f["name"] or f["sha"] in first_of_sha:
            continue
        first_of_sha[f["sha"]] = f
        editions.append(f)
    fam = group(editions, lambda f: family_of(f["name"]))
    if fam:
        log("\nSAME FAMILY - different editions of one pack")
        log("  Newest first. Keeping more than one edition of a pack is what")
        log("  puts two of the same layer in Overlay Manager.")
        for name, grp in sorted(fam.items()):
            problems += 1
            log(f"\n  {name}:")
            for f in sorted(grp, key=lambda f: -f["mtime"]):
                log("    " + describe(f))

    fam_shas = {f["sha"] for grp in fam.values() for f in grp}
    same_name = group([f for f in files
                       if f["sha"] not in set(same_bytes)
                       and f["sha"] not in fam_shas],
                      lambda f: f["name"].lower())
    if same_name:
        log("\nSAME NAME, DIFFERENT BYTES - look before you choose")
        log("  No `__` edition tag, so nothing in the name says which is")
        log("  newer. Compare the size and date yourself.")
        for name, grp in sorted(same_name.items()):
            problems += 1
            log(f"\n  {name}:")
            for f in sorted(grp, key=lambda f: -f["mtime"]):
                log("    " + describe(f))

    log("\n" + "=" * 72)
    if not problems:
        log("No collisions. Every file here is unique in name and content.")
    else:
        log(f"{problems} collision group(s).")
        log("\nNothing was changed. To remove a copy, check the path first -")
        log("a file under a datapackage/ or an import directory belongs to")
        log("something else, and deleting it may break that instead:")
        log("    rm '<full path from above>'")
        log("Then force-stop ATAK: Settings > Apps > ATAK > Force stop.")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Find duplicate KML/KMZ anywhere in the ATAK tree.")
    ap.add_argument("--root", action="append", default=[],
                    help="extra directory to sweep (repeatable)")
    a = ap.parse_args(argv)

    roots = ROOTS + a.root
    files = scan(roots)
    if not files:
        print("No KML or KMZ found under:")
        for r in roots:
            mark = "" if os.path.isdir(r) else "   (no such directory)"
            print(f"  {r}{mark}")
        print("\nIf ATAK stores its files somewhere else on this device, pass")
        print("that directory: --root /storage/emulated/0/<wherever>")
        return 1
    report(files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
