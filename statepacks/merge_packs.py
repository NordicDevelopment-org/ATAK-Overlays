#!/usr/bin/env python3
"""Merge pages of one dataset into a single pack with a real folder tree.

    python3 merge_packs.py --inspect ~/atak-packs/CI_substations_*.kmz
    python3 merge_packs.py --name MN_Substations --folder-by VOLTAGE \\
        --label '{NAME} - {VOLTAGE} kV' --out ~/atak-packs \\
        /storage/emulated/0/atak/overlays/CI_substations_*.kmz

WHY. A fetch that pages at 2,000 features writes CI_substations_01..04 and
CI_transmission_lines_01..06. Every one of those is a separate top-level entry
in Overlay Manager, so ten eye-toggles control what is really two layers, and
no single toggle turns "substations" on or off. The split is an artifact of
how the data was fetched and means nothing to anyone reading a map.

INSPECT FIRST. The label template and the folder field are chosen from the
fields the files ACTUALLY carry, not from what a schema ought to have. That is
the whole reason --inspect exists and prints the key names it found: guessing
a field name here produces a pack labelled "{NAME} - {VOLTAGE} kV" with the
braces still in it, or worse, silently blank.

WHAT IS NOT DONE HERE. Placemarks are carried across verbatim, geometry,
ExtendedData and all. Only the <name> is rewritten, and only when a template
is given and every field it needs is present on that placemark. Nothing is
recomputed, reprojected or reformatted - this is a regrouping, not a rebuild,
and a merge that quietly changed a coordinate would be much worse than ten
entries in a menu.
"""
import argparse
import datetime as dt
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_county_pack as bcp                             # noqa: E402

KML_NS = "http://www.opengis.net/kml/2.2"
ET.register_namespace("", KML_NS)


def _local(tag):
    """An element's tag with any {namespace} prefix stripped."""
    return tag.rsplit("}", 1)[-1]


def _same_ns_tag(sibling, name):
    """A qualified tag using the SAME namespace as `sibling` (or none) - for
    a new child that must not look mismatched next to elements this
    placemark already carries from its own source file."""
    if sibling.tag.startswith("{"):
        return f"{sibling.tag.split('}', 1)[0]}}}{name}"
    return name


def _child(parent, name):
    """First DIRECT child matching `name`, in ANY namespace or none.

    A merge input is not obligated to be kml/2.2 to be real KML - an older
    Google Earth export (2.0, 2.1) or a generator that skips xmlns entirely
    are both valid, and hardcoding 2.2 made every lookup below miss on such
    a file. Nothing raised: doc.find() just returned None for name,
    description, every Style, every Placemark - a whole file silently read
    as empty, which is worse than an error naming it.
    """
    for el in parent:
        if _local(el.tag) == name:
            return el
    return None


def _descendant(parent, name):
    """First matching element anywhere under `parent` (not `parent` itself),
    any namespace - coordinates sits inside Point/LineString/Polygon, itself
    a child of the placemark, so a shallow search would miss it."""
    for el in parent.iter():
        if el is not parent and _local(el.tag) == name:
            return el
    return None


def _all(parent, name):
    """Every matching element anywhere under `parent`, any namespace -
    Style/Placemark/etc can sit at any depth inside Document/Folder
    nesting."""
    return [el for el in parent.iter() if _local(el.tag) == name]


def read_pack(path):
    """One pack: its document name, description, styles, placemarks, icons.

    Never raises on a bad file - a merge of ten packs must say which one is
    broken rather than dying with a traceback naming none of them.
    """
    out = {"path": path, "name": os.path.basename(path), "doc_name": "",
           "description": "", "styles": [], "placemarks": [], "icons": {},
           "error": None}
    try:
        if path.lower().endswith(".kml"):
            with open(path, "rb") as fh:
                data = fh.read()
        else:
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".kml")]
                if not names:
                    out["error"] = "no .kml inside this .kmz"
                    return out
                pick = next((n for n in names if n.lower().endswith("doc.kml")),
                            names[0])
                data = z.read(pick)
                for n in z.namelist():
                    if n.lower().endswith((".png", ".jpg", ".jpeg")):
                        out["icons"][n] = z.read(n)
    except (zipfile.BadZipFile, OSError, KeyError) as ex:    # noqa: BLE001
        out["error"] = f"unreadable: {ex}"
        return out

    try:
        root = ET.fromstring(data)
    except ET.ParseError as ex:
        out["error"] = f"not parseable XML: {ex}"
        return out

    doc = _child(root, "Document")
    if doc is None:
        doc = root
    n = _child(doc, "name")
    out["doc_name"] = (n.text or "").strip() if n is not None else ""
    d = _child(doc, "description")
    out["description"] = (d.text or "").strip() if d is not None else ""
    out["styles"] = _all(doc, "Style") + _all(doc, "StyleMap")
    out["placemarks"] = _all(doc, "Placemark")
    return out


def fields_of(placemark):
    """{key: value} from ExtendedData, both Data and SimpleData spellings."""
    out = {}
    for data in _all(placemark, "Data"):
        key = data.get("name")
        if not key:
            continue
        val = _child(data, "value")
        out[key] = (val.text or "").strip() if val is not None else ""
    for sd in _all(placemark, "SimpleData"):
        key = sd.get("name")
        if key:
            out[key] = (sd.text or "").strip()
    return out


def name_of(placemark):
    n = _child(placemark, "name")
    return (n.text or "").strip() if n is not None else ""


def coords_of(placemark):
    c = _descendant(placemark, "coordinates")
    return (c.text or "").strip() if c is not None else ""


TEMPLATE_FIELD = re.compile(r"\{([^{}]+)\}")


def render_label(template, fields, fallback):
    """The template with fields substituted, or None if any is missing.

    None, not a half-filled string. "Substation - {VOLTAGE} kV" on the map is
    worse than the original name, and " - kV" is worse still because it looks
    like a real label for a substation with no voltage. A placemark that
    cannot be labelled keeps the name it arrived with.
    """
    wanted = TEMPLATE_FIELD.findall(template)
    if not wanted:
        return None
    values = {}
    for key in wanted:
        v = fields.get(key)
        if v is None or v == "":
            return None
        values[key] = v
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out.strip() or fallback


def inspect(packs, log=print, samples=3, keys=12):
    """What these files actually contain, so a template is chosen not guessed."""
    log(f"\n{len(packs)} file(s)")
    log("=" * 68)
    all_keys, total = {}, 0
    for p in packs:
        if p["error"]:
            log(f"\n  {p['name']}\n    [!] {p['error']}")
            continue
        total += len(p["placemarks"])
        log(f"\n  {p['name']}")
        log(f"    document name : {p['doc_name'] or '(none)'}")
        log(f"    placemarks    : {len(p['placemarks'])}")
        log(f"    provenance    : "
            f"{'yes' if p['description'] else 'NONE IN THE DOCUMENT'}")
        seen = {}
        for pm in p["placemarks"]:
            for k, v in fields_of(pm).items():
                seen.setdefault(k, 0)
                if v:
                    seen[k] += 1
        for k, n in seen.items():
            all_keys[k] = all_keys.get(k, 0) + n
        if seen:
            log(f"    fields        : {len(seen)}")
            for k, n in sorted(seen.items(), key=lambda kv: -kv[1])[:keys]:
                log(f"        {k:28} filled on {n} of {len(p['placemarks'])}")
            if len(seen) > keys:
                log(f"        ... and {len(seen) - keys} more")
        else:
            log("    fields        : none - nothing to build a label from")
        for pm in p["placemarks"][:samples]:
            log(f"    example name  : {name_of(pm) or '(blank)'!r}")

    log("\n" + "=" * 68)
    log(f"{total} placemark(s) across {len(packs)} file(s)")
    if all_keys:
        log("\nFields present across all files, most-filled first - a --label")
        log("template or --folder-by may only use these:")
        for k, n in sorted(all_keys.items(), key=lambda kv: -kv[1]):
            log(f"    {{{k}}}  filled {n} time(s)")
    return all_keys


def dedupe_placemarks(placemarks, log=print):
    """Drop placemarks identical in name, coordinates and every field.

    Paged fetches overlap: a feature on a page boundary comes back on both
    pages. Identical in all three is one feature returned twice. Anything
    differing anywhere is kept - two substations can genuinely share a name.
    """
    seen, out = set(), []
    for pm in placemarks:
        key = (name_of(pm), coords_of(pm),
               tuple(sorted(fields_of(pm).items())))
        if key in seen:
            continue
        seen.add(key)
        out.append(pm)
    dropped = len(placemarks) - len(out)
    if dropped:
        log(f"    [!] {dropped} placemark(s) appeared on more than one page "
            f"and were merged; {len(out)} distinct.")
    return out


def folder_key(placemark, field, missing="(not recorded)"):
    """The folder this placemark belongs in.

    A missing value gets its OWN folder, named so, rather than being dropped
    or lumped into a real bucket. How many features lack the field is a fact
    about the data and belongs on screen.
    """
    value = fields_of(placemark).get(field, "")
    return value.strip() or missing


def _sort_key(label):
    """Numeric-aware, so 69 kV sorts before 115 kV and not after it."""
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", label)
    return (0, float(m.group(1)), "") if m else (1, 0.0, label.lower())


def merge(packs, name, label=None, folder_by=None, log=print):
    """One KML document, one folder per group, plus the icons to draw it."""
    live = [p for p in packs if not p["error"]]
    for p in packs:
        if p["error"]:
            log(f"    [!] skipped {p['name']}: {p['error']}")
    if not live:
        raise SystemExit("nothing readable to merge")

    placemarks = [pm for p in live for pm in p["placemarks"]]
    log(f"    {len(placemarks)} placemark(s) from {len(live)} file(s)")
    placemarks = dedupe_placemarks(placemarks, log=log)

    relabelled = skipped = 0
    if label:
        for pm in placemarks:
            new = render_label(label, fields_of(pm), name_of(pm))
            if new is None:
                skipped += 1
                continue
            node = _child(pm, "name")
            if node is None:
                node = ET.SubElement(pm, _same_ns_tag(pm, "name"))
            node.text = new
            relabelled += 1
        log(f"    {relabelled} placemark(s) relabelled")
        if skipped:
            log(f"    [!] {skipped} kept their original name - the template "
                f"needs a field they do not carry. A half-filled label reads "
                f"as a real one, so none was written.")

    groups = {}
    if folder_by:
        for pm in placemarks:
            groups.setdefault(folder_key(pm, folder_by), []).append(pm)
    else:
        # A set of ids, not `pm in placemarks`. Element equality is identity,
        # so the list form is a linear scan per placemark - 10,000 of them
        # (which is what the transmission-line pages hold) is 100 million
        # comparisons to answer a question a hash answers once.
        kept = {id(pm) for pm in placemarks}
        for p in live:
            stem = os.path.splitext(p["name"])[0]
            for pm in p["placemarks"]:
                if id(pm) in kept:
                    groups.setdefault(stem, []).append(pm)

    # Provenance from every source, deduplicated. Sources that disagree keep
    # BOTH statements: a merged pack claiming one origin it does not have is
    # exactly the failure rule 4 exists to prevent.
    provenance = []
    for p in live:
        if p["description"] and p["description"] not in provenance:
            provenance.append(p["description"])
    missing = [p["name"] for p in live if not p["description"]]

    styles, seen_style = [], set()
    for p in live:
        for st in p["styles"]:
            sid = st.get("id")
            if sid and sid in seen_style:
                continue
            if sid:
                seen_style.add(sid)
            styles.append(ET.tostring(st, encoding="unicode"))

    icons, clashes = {}, []
    for p in live:
        for path, data in p["icons"].items():
            if path in icons and icons[path] != data:
                clashes.append(path)
                continue
            icons[path] = data
    if clashes:
        log(f"    [!] {len(sorted(set(clashes)))} icon path(s) differ between "
            f"files; the first was kept: {', '.join(sorted(set(clashes))[:4])}")

    body = []
    for key in sorted(groups, key=_sort_key):
        members = groups[key]
        inner = "".join(ET.tostring(pm, encoding="unicode") for pm in members)
        body.append(f"<Folder><name>{bcp.esc(key)} ({len(members)})</name>"
                    f"<open>0</open>{inner}</Folder>")

    desc = "".join(f"<p>{d}</p>" for d in provenance) or (
        "<p>No source, licence or retrieval date was recorded in any of the "
        "files merged here.</p>")
    if missing:
        desc += (f"<p style='color:#888'>No provenance in: "
                 f"{bcp.esc(', '.join(missing))}</p>")
    desc += (f"<p style='color:#888'>Merged from {len(live)} file(s): "
             f"{bcp.esc(', '.join(p['name'] for p in live))}</p>")

    kml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
           f"<name>{bcp.esc(name)}</name>"
           f"<description><![CDATA[{desc}]]></description>"
           + "".join(styles) + "".join(body) + "</Document></kml>")
    log(f"    {len(groups)} folder(s), {len(placemarks)} placemark(s)")
    return kml, icons, len(placemarks)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Merge pages of one dataset into a single pack.")
    ap.add_argument("files", nargs="+", help="the .kmz/.kml files to merge")
    ap.add_argument("--inspect", action="store_true",
                    help="report what the files contain and exit; use this "
                         "to pick --label and --folder-by from real fields")
    ap.add_argument("--name", help="document name, e.g. MN_Substations")
    ap.add_argument("--out", default=os.path.expanduser("~/atak-packs"))
    ap.add_argument("--label",
                    help="placemark name template, e.g. '{NAME} - {VOLTAGE} kV'. "
                         "A placemark missing any field keeps its own name.")
    ap.add_argument("--folder-by", metavar="FIELD",
                    help="group into folders by this field; default is one "
                         "folder per source file")
    a = ap.parse_args(argv)

    packs = [read_pack(p) for p in a.files]
    if a.inspect:
        inspect(packs)
        return 0
    if not a.name:
        raise SystemExit("--name is required when merging (or use --inspect)")

    kml, icons, n = merge(packs, a.name, label=a.label,
                          folder_by=a.folder_by)
    stamp = bcp.edition(dt.date.today().isoformat(), kml, icons)
    path = os.path.join(a.out, f"{bcp.safe(a.name)}__{stamp}.kmz")
    size = bcp.write_kmz(path, kml, icons)
    print(f"[*] {n} placemark(s) -> {os.path.basename(path)} "
          f"({size // 1024} KB)")
    print(f"    The files it was merged FROM are untouched. Remove them with "
          f"termux/atak-remove.sh once this one looks right.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
