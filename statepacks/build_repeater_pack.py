#!/usr/bin/env python3
"""Amateur repeaters as an ATAK overlay, from a coordinated list.

    python3 build_repeater_pack.py --state MN \\
        --from-file mn_repeaters_established.geojson --out ~/atak-packs

WHAT GOES IN. Only repeaters that are FREQUENCY-COORDINATED by the state's
coordination body - in Minnesota, the Minnesota Repeater Council. Coordination
is the objective bar for an established machine, and it is what keeps out the
1 watt simplex hotspots that a network directory is full of: a Raspberry Pi on
a desk is never coordinated. Those hotspot coordinates are people's homes, so
this is a privacy line as much as a quality one.

THIS BUILDER DOES NOT FETCH. The coordination list is a PDF published by a
council, not an endpoint, so the input is a file someone assembled. That means
the input is exactly as trustworthy as whoever built it, and this builder
checks it rather than trusting it. Two things it found on the first real file:

  * A 4x JOIN FAN-OUT. 2,080 features held 648 distinct records; one callsign
    appeared 16 times. Stacked pins on a map look like one pin, so the error is
    invisible until someone counts. Identical records are collapsed and the
    reduction is printed.
  * TONES THAT ARE NOT TONES. "23.0" is DCS 023 read as a number, "443.4" is a
    70cm FREQUENCY sitting in the tone column. Both would be programmed into a
    radio, where the first opens nothing and the second is not a tone at all.
    A value that is not a valid CTCSS tone, DCS code or DMR colour code is
    still SHOWN - it is what the source said - but labelled so nobody keys it
    in believing it.

WHAT IS NOT DERIVED. An input frequency is carried only where the source has
one. It is never computed from a band's usual offset: "usual" is not "this
machine's", and a repeater you cannot key is worse than one you know you cannot
key.
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

# The 50 standard CTCSS tones. A value outside this set is not a CTCSS tone,
# whatever it looks like.
CTCSS = {
    "67.0", "69.3", "71.9", "74.4", "77.0", "79.7", "82.5", "85.4", "88.5",
    "91.5", "94.8", "97.4", "100.0", "103.5", "107.2", "110.9", "114.8",
    "118.8", "123.0", "127.3", "131.8", "136.5", "141.3", "146.2", "151.4",
    "156.7", "159.8", "162.2", "165.5", "167.9", "171.3", "173.8", "177.3",
    "179.9", "183.5", "186.2", "189.9", "192.8", "196.6", "199.5", "203.5",
    "206.5", "210.7", "218.1", "225.7", "229.1", "233.6", "241.8", "250.3",
    "254.1",
}
DCS_RX = re.compile(r"^[Dd]\s*\d{2,3}[NnIi]?$")     # D023, D172, D47
CC_RX = re.compile(r"^CC\s*\d{1,2}$", re.I)          # DMR colour code

# Colour per mode. Shape says "repeater", colour says which kind, and the
# folder says it again in words - three ways, because one is not enough on a
# map that is also showing terrain.
MODE_COLOUR = {
    "FM": (120, 220, 255), "DMR": (255, 170, 80), "Fusion": (180, 255, 150),
    "D-STAR": (255, 140, 200), "D-STAR-DD": (255, 140, 200),
    "P25": (255, 230, 110), "NXDN": (200, 170, 255), "ATV": (255, 255, 255),
}
MODE_FALLBACK = (255, 209, 64)


def classify_tone(raw):
    """(kind, note). kind is ctcss / dcs / colour-code / unrecognised / none.

    Nothing is corrected or dropped. A bad value is passed through with a note,
    because it is what the source says and hiding it would make the next person
    re-discover it - but it does not get to look like a tone somebody can use.
    """
    t = str(raw or "").strip()
    if not t:
        return "none", ""
    if t in CTCSS:
        return "ctcss", ""
    if DCS_RX.match(t):
        return "dcs", ""
    if CC_RX.match(t):
        return "colour-code", ""
    # The two shapes actually seen in a real file, named so the report can say
    # which one rather than just "bad".
    if re.match(r"^\d{1,3}\.0$", t):
        return "unrecognised", (f"not a CTCSS tone - looks like DCS "
                                f"{int(float(t)):03d} read as a number")
    if re.match(r"^\d{2,3}\.\d$", t):
        return "unrecognised", "not a CTCSS tone - looks like a frequency"
    return "unrecognised", "not a CTCSS tone, DCS code or DMR colour code"


def dedupe(feats, log=print):
    """Drop records identical in every property AND coordinate.

    Only exact duplicates. Two rows that differ anywhere are kept, because a
    difference might be real - one callsign genuinely running FM and DMR from
    one tower is two rows, not a mistake.
    """
    seen, out = set(), []
    for f in feats:
        key = (json.dumps(f.get("properties") or {}, sort_keys=True),
               json.dumps(f.get("geometry") or {}, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    if len(out) != len(feats):
        log(f"    [!] {len(feats) - len(out)} duplicate record(s) collapsed; "
            f"{len(out)} distinct. Stacked pins look like one pin, so this is "
            f"invisible on a map until someone counts.")
    return out


def rows_for(p):
    """(label, value, note) in reading order. Missing stays missing."""
    out_mhz = str(p.get("output_mhz") or "").strip()
    in_mhz = str(p.get("input_mhz") or "").strip()
    kind, note = classify_tone(p.get("tone"))
    tone = str(p.get("tone") or "").strip()
    src = str(p.get("coord_source") or "").strip()
    # A city-centroid is not where the antenna is, and a popup that does not
    # say so invites someone to drive to it.
    src_note = ("approximate - this is the centre of the town, not the tower"
                if "centroid" in src.lower() else "")
    return [
        ("Listen", f"{out_mhz} MHz" if out_mhz else "", ""),
        ("Transmit", f"{in_mhz} MHz" if in_mhz else "",
         "" if in_mhz else "not in the source; not computed from an offset"),
        ("Tone", tone, note),
        ("Mode", str(p.get("mode") or "").strip(), ""),
        ("Band", str(p.get("band") or "").strip(), ""),
        ("Sponsor", str(p.get("sponsor") or "").strip(), ""),
        ("Access", str(p.get("access") or "").strip(), ""),
        ("City", str(p.get("city") or "").strip(), ""),
        ("Callsign", str(p.get("callsign") or "").strip(), ""),
        ("Coordinated", str(p.get("coordinated") or "").strip(), ""),
        ("Coordination date", str(p.get("update") or "").strip(), ""),
        ("Position from", src, src_note),
    ]


def placemark(feat, meta):
    p = feat.get("properties") or {}
    coords = (feat.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        return None, None
    try:
        lon, lat = float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return None, None

    mode = str(p.get("mode") or "unknown").strip() or "unknown"
    rows = rows_for(p)
    body = ""
    for label, value, note in rows:
        if not value:
            continue
        body += f"{bcp.esc(label)}: <b>{bcp.esc(value)}</b>"
        if note:
            body += f' <font color="{bcp.GREY}">[{bcp.esc(note)}]</font>'
        body += "<br/>"
    missing = [label for label, value, note in rows if not value]
    if missing:
        body += (f'<br/><font color="{bcp.GREY}"><i>No data for: '
                 f'{bcp.esc(", ".join(missing))}</i></font><br/>')

    footer = (f'<hr/><font color="{bcp.GREY}"><i>'
              f"Source: {bcp.esc(meta['source'])}<br/>"
              f"Coordinated repeaters only - hotspots are not coordinated and "
              f"are not here<br/>"
              f"Input frequency carried, never computed from a band offset<br/>"
              f"Pack built: {bcp.esc(meta['built'])}</i></font>")

    call = str(p.get("callsign") or "").strip()
    out_mhz = str(p.get("output_mhz") or "").strip()
    name = " ".join(x for x in (call, out_mhz) if x) or "repeater"
    return mode, (f"<Placemark><name>{bcp.esc(name)}</name>"
                  f"<description><![CDATA[{body}{footer}]]></description>"
                  f"<styleUrl>#m_{bcp.safe(mode).lower()}</styleUrl>"
                  f"<Point><coordinates>{round(lon, 6)},{round(lat, 6)},0"
                  f"</coordinates></Point></Placemark>")


def pack_kml(state, feats, meta):
    by_mode, dropped = {}, 0
    for f in feats:
        mode, pm = placemark(f, meta)
        if pm is None:
            dropped += 1
            continue
        by_mode.setdefault(mode, []).append(pm)
    meta["dropped_no_coords"] = dropped

    styles = ""
    for mode in sorted(by_mode):
        rgb = MODE_COLOUR.get(mode, MODE_FALLBACK)
        styles += (f'<Style id="m_{bcp.safe(mode).lower()}">'
                   f"<IconStyle><scale>1.0</scale><Icon>"
                   f"<href>icons/repeater_{bcp.safe(mode).lower()}.png</href>"
                   f"</Icon></IconStyle>"
                   f"<LabelStyle><scale>0.8</scale></LabelStyle></Style>")
    folders = "".join(
        f"<Folder><name>{bcp.esc(m)} ({len(by_mode[m])})</name><open>0</open>"
        f"{''.join(by_mode[m])}</Folder>" for m in sorted(by_mode))

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{bcp.esc(meta['title'])}</name><open>0</open>"
        f"<description><![CDATA["
        f"Frequency-coordinated amateur repeaters in {bcp.esc(state)}.<br/><br/>"
        f"<b>Listen</b> is the repeater's output. <b>Transmit</b> is its input, "
        f"and it is shown only where the source carries one - it is never "
        f"computed from a band's usual offset.<br/><br/>"
        f"A tone that is not a valid CTCSS tone, DCS code or DMR colour code is "
        f"still shown, because it is what the source says, but it is labelled. "
        f"Do not key one in without checking.<br/><br/>"
        f"Some positions are a town centroid rather than a tower. Every "
        f"placemark says which.<br/><br/>"
        f"<b>Source:</b> {bcp.esc(meta['source'])}<br/>"
        f"<b>Pack built:</b> {bcp.esc(meta['built'])}"
        f"]]></description>{styles}{folders}</Document></kml>")


def tone_report(feats, log=print):
    kinds = {}
    bad = []
    for f in feats:
        p = f.get("properties") or {}
        kind, note = classify_tone(p.get("tone"))
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "unrecognised":
            bad.append((str(p.get("callsign") or ""), str(p.get("tone") or ""), note))
    log("    tones: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    if bad:
        log(f"    [!] {len(bad)} record(s) carry something in the tone column "
            f"that is not a tone. Shown in the pack, and labelled:")
        seen = set()
        for call, tone, note in bad:
            if tone in seen:
                continue
            seen.add(tone)
            log(f"          {tone:>8s}  {note}   (e.g. {call})")
    return kinds, bad


def build(state, out_dir, feats, source="", log=print):
    state = state.strip().upper()
    feats = dedupe(feats, log=log)
    tone_report(feats, log=log)

    built = dt.date.today().isoformat()
    meta = {"title": f"{state} Amateur Repeaters ({built})",
            "source": source or "coordinated repeater list", "built": built}
    kml = pack_kml(state, feats, meta)
    if meta["dropped_no_coords"]:
        log(f"    [!] {meta['dropped_no_coords']} record(s) had no usable "
            f"coordinates and were left out rather than placed at a guess")

    modes = sorted({str((f.get('properties') or {}).get('mode') or 'unknown')
                    for f in feats})
    icons = {}
    for mode in modes:
        rgb = MODE_COLOUR.get(mode, MODE_FALLBACK)
        icons[f"icons/repeater_{bcp.safe(mode).lower()}.png"] = \
            glyphs.render("repeater", rgb)

    stamp = bcp.edition(built, kml, icons)
    path = os.path.join(out_dir, f"{state}_Repeaters__{stamp}.kmz")
    size = bcp.write_kmz(path, kml, icons)
    drawn = len(feats) - meta["dropped_no_coords"]
    log(f"[*] {state}: {drawn} repeater(s) -> {os.path.basename(path)} "
        f"({size // 1024} KB)")
    return path, drawn


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build a coordinated-amateur-repeater overlay for ATAK.")
    ap.add_argument("--state", default="MN")
    ap.add_argument("--out", default=os.path.expanduser("~/atak-packs"))
    ap.add_argument("--from-file", required=True,
                    help="GeoJSON of coordinated repeaters. There is no live "
                         "endpoint: a coordination list is a PDF a council "
                         "publishes, so this input is assembled by hand.")
    ap.add_argument("--source", default="",
                    help="what to credit in every popup, e.g. "
                         "'Minnesota Repeater Council coordinated list, "
                         "2026-09-13, with coordinates from hearham and "
                         "Brandmeister'")
    a = ap.parse_args(argv)

    if not os.path.exists(a.from_file):
        # A traceback tells you the file is missing. It does not tell you that
        # this builder has no endpoint to fall back on, which is the thing
        # someone actually needs to know at this point.
        raise SystemExit(
            f"not found: {a.from_file}\n"
            f"\n"
            f"There is no live source to fall back on: a frequency-coordination\n"
            f"list is a PDF a council publishes, so this pack is built from a\n"
            f"file you assemble and put somewhere this script can read.\n"
            f"\n"
            f"If you downloaded it on the phone, it is probably still in\n"
            f"Downloads rather than in the folder above. To find it:\n"
            f"    ls ~/storage/downloads/*repeater* "
            f"/storage/emulated/0/Download/*repeater* 2>/dev/null\n"
            f"    unzip -o <that file> -d ~/atak-packs/\n"
            f"(if ~/storage does not exist yet, run termux-setup-storage once)")
    doc = json.load(open(a.from_file, encoding="utf-8"))
    feats = doc.get("features") if isinstance(doc, dict) else doc
    if not isinstance(feats, list):
        raise SystemExit(f"{a.from_file}: expected GeoJSON with a features list")
    print(f"[*] {len(feats)} record(s) from {a.from_file}")

    os.makedirs(a.out, exist_ok=True)
    build(a.state, a.out, feats, source=a.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
