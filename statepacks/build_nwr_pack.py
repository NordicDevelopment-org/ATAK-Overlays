#!/usr/bin/env python3
"""NOAA Weather Radio transmitters as an ATAK overlay.

    python3 build_nwr_pack.py --state MN --out ~/atak-packs
    python3 build_nwr_pack.py --state MN --coverage      # includes border stations
    python3 build_nwr_pack.py --from-file nwr_all_us.json --state MN

WHAT THIS IS. NWR is one-way broadcast on seven channels between 162.400 and
162.550 MHz. A transmitter is a thing you LISTEN to; there is nothing to
transmit back on. So this pack carries no input frequency, no offset and no
tone, and those rows are absent from the popup rather than present and empty.

The 1050 Hz warning alert tone and the SAME digital header are NOT CTCSS or
DCS. They must never be written into a tone field - they are a broadcast
signalling scheme, not a repeater access tone, and a radio programmed from the
first as if it were the second does not work.

THE SOURCE. weather.gov's own county-coverage data file, which is what backs
the station pages on the site:

    https://www.weather.gov/source/nwr/JS/ccl-data.js

It is JavaScript, not JSON: `var cclData = [ ... ];`. Strip the assignment and
the trailing semicolon and the rest parses. This matters because the HTML
station tables at /nwr/stations carry no coordinates at all - callsign,
frequency, town, status and WFO only - so this file is the only published NWS
source that can place a transmitter on a map.

LICENCE. Public domain. https://www.weather.gov/disclaimer: "The information on
National Weather Service (NWS) Web pages are in the public domain, unless
specifically noted otherwise, and may be used without charge for any lawful
purpose so long as you do not: 1) claim it is your own ... 2) use it in a
manner that implies an endorsement or affiliation with NOAA/NWS, or 3) modify
its content and then present it as official government material." An attributed
pack satisfies all three, and the footer on every placemark is what satisfies
them.

STATUS IS LIVE DATA. A transmitter reads NORMAL, DEGRADED or OUT OF SERVICE at
the moment the file was fetched and not after, so the retrieval date rides in
the document name, the description and every placemark footer. An out-of-service
transmitter gets its own folder rather than a quiet flag, because "the weather
radio you were counting on is down" is the kind of thing a folder should say.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_county_pack as bcp                             # noqa: E402
import glyphs                                                # noqa: E402

CCL_URL = "https://www.weather.gov/source/nwr/JS/ccl-data.js"
NWS_DISCLAIMER = "https://www.weather.gov/disclaimer"
STATION_PAGE = "https://www.weather.gov/nwr/stations?State={state}"

# The seven NWR channels. A record off these is not rejected - it is the
# source's to be wrong about, and dropping it would hide the error - but it is
# counted and reported, because a frequency that is not an NWR channel means
# the file changed shape or the parse is wrong.
NWR_CHANNELS = ("162.400", "162.425", "162.450", "162.475",
                "162.500", "162.525", "162.550")


def parse_ccl(text):
    """The array out of `var cclData = [ ... ];`.

    Anchored on the assignment rather than on the first bracket: the file is
    generated, and a comment or a second variable before the data would make
    "find the first [" pick up the wrong thing silently.
    """
    m = re.search(r"var\s+cclData\s*=\s*(\[.*\])\s*;?\s*$", text,
                  re.S | re.M)
    if not m:
        # Say what was actually seen. "could not parse" sends someone looking
        # at their network when the file has simply been renamed.
        head = text[:120].replace("\n", " ")
        raise ValueError(
            f"no `var cclData = [...]` in the response. First 120 chars: {head!r}")
    return json.loads(m.group(1))


def fetch_ccl(url=CCL_URL, log=print):
    log(f"[*] fetching NWR transmitter data")
    log(f"    {url}")
    raw = bcp.http_get(url, log=log).decode("utf-8", "replace")
    rows = parse_ccl(raw)
    log(f"    {len(rows)} transmitters nationwide")
    return rows


def same_pairs(station):
    """[(same_code, county, state)] for one transmitter, order preserved.

    A county has ONE whole-county code (0 + 5-digit FIPS) and ZERO OR MORE
    partial-county codes (1-9 + the same FIPS). Partial County Alerting ADDS
    sub-area codes, it does not replace the whole-county one - Hennepin appears
    in this very file as 027053 and 127053 and 327053. Anything that treats a
    partial code as a substitute drops the whole-county code and with it most
    of the alerting.
    """
    out = []
    for c in station.get("counties") or []:
        same = str(c.get("same") or "").strip()
        if same:
            out.append((same, str(c.get("county") or "").strip(),
                        str(c.get("st") or "").strip()))
    return out


def in_state(station, state, coverage=False):
    """Physically sited in the state, or alerting into it.

    Two different questions with two different right answers, so the caller
    picks. `sitestate` is the point layer: where the antenna is. `coverage`
    includes a transmitter in North Dakota whose alerts reach Minnesota
    counties, which is what you want if the question is "what covers me".
    """
    if str(station.get("sitestate") or "").strip().upper() == state:
        return True
    if coverage:
        return any(st.upper() == state for _same, _c, st in same_pairs(station))
    return False


def station_rows(station, state):
    """(label, value) for the popup, in reading order. Missing stays missing."""
    freq = str(station.get("freq") or "").strip()
    power = str(station.get("power") or "").strip()
    # sitename and siteloc carry the same string on 440 of 1036 records, so
    # joining them blindly prints "Gunflint Lake Gunflint Lake". Where they
    # DIFFER both are worth keeping and they mean different things: the station
    # is named for the town it serves ("Alamosa") and sited on the hill the
    # antenna is actually on ("Agua Ramon Mountain").
    nm = str(station.get("sitename") or "").strip()
    loc = str(station.get("siteloc") or "").strip()
    if nm and loc and nm.lower() != loc.lower():
        site = f"{nm} ({loc})"
    else:
        site = nm or loc
    # "Grand Forks|ND" is how the source writes it; the pipe is theirs, not a
    # parsing artefact, so it is shown as a readable pair rather than kept raw.
    wfo = str(station.get("wfo") or "").strip().replace("|", ", ")
    same = same_pairs(station)
    here = [p for p in same if p[2].upper() == state]
    rows = [
        ("Listen", f"{freq} MHz" if freq else ""),
        ("Power", f"{power} W" if power else ""),
        ("Status", str(station.get("status") or "").strip()),
        ("Site", site),
        ("NWS office", wfo),
        (f"Counties alerted in {state}", str(len(here)) if same else ""),
    ]
    return rows, here


def placemark(station, state, meta):
    lat, lon = station.get("lat"), station.get("lon")
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None                       # no coordinate, no point. Not guessed.
    call = str(station.get("callsign") or "").strip()
    freq = str(station.get("freq") or "").strip()
    rows, here = station_rows(station, state)

    body = ""
    for label, value in rows:
        if value:
            body += f"{bcp.esc(label)}: <b>{bcp.esc(value)}</b><br/>"
    missing = [label for label, value in rows if not value]
    if missing:
        body += (f'<br/><font color="{bcp.GREY}"><i>No data for: '
                 f'{bcp.esc(", ".join(missing))}</i></font><br/>')
    if here:
        # The SAME code is the thing someone programs into a receiver, so it is
        # shown next to its county rather than summarised away.
        listed = "; ".join(f"{c} {s}" for s, c, _st in here[:40])
        more = f" ... and {len(here) - 40} more" if len(here) > 40 else ""
        body += (f'<br/><font color="{bcp.GREY}"><i>SAME codes ({state}): '
                 f'{bcp.esc(listed)}{more}</i></font><br/>')

    # .get() with a default, not meta[...]: a caller built before data_as_of
    # and data_verified_live existed is a caller that only ever meant a live
    # fetch, so the historically-correct fallback IS "verified live, as-of
    # the build date" - the same values build() used to hardcode everywhere.
    as_of_note = ("" if meta.get("data_verified_live", True)
                 else " (from a saved file, not a live check)")
    data_as_of = meta.get("data_as_of", meta.get("built", ""))
    footer = (f'<hr/><font color="{bcp.GREY}"><i>'
              f"Source: NOAA/NWS Weather Radio county coverage<br/>"
              f"{bcp.esc(meta['url'])}<br/>"
              f"Licence: public domain (NOAA/NWS)<br/>"
              f"Data as of: {bcp.esc(data_as_of)}{bcp.esc(as_of_note)}"
              f" - it changes<br/>"
              f"Pack built: {bcp.esc(meta['built'])}</i></font>")

    name = f"{call} {freq}".strip() if freq else call
    return (f"<Placemark><name>{bcp.esc(name)}</name>"
            f"<description><![CDATA[{body}{footer}]]></description>"
            f"<styleUrl>#nwr</styleUrl>"
            f"<Point><coordinates>{round(lon, 6)},{round(lat, 6)},0"
            f"</coordinates></Point></Placemark>")


def pack_kml(state, stations, meta):
    """One Document, one folder per status. Out-of-service is not a footnote."""
    by_status = {}
    dropped = 0
    for s in stations:
        pm = placemark(s, state, meta)
        if pm is None:
            dropped += 1
            continue
        status = str(s.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
        by_status.setdefault(status, []).append(pm)
    meta["dropped_no_coords"] = dropped

    # NORMAL first, then anything degraded, then out of service: the order
    # someone scanning the tree would want, not alphabetical.
    def rank(name):
        return {"NORMAL": 0, "DEGRADED": 1, "OUT OF SERVICE": 2}.get(name, 3)

    folders = ""
    for status in sorted(by_status, key=lambda n: (rank(n), n)):
        pms = by_status[status]
        folders += (f"<Folder><name>{bcp.esc(status)} ({len(pms)})</name>"
                    f"<open>0</open>{''.join(pms)}</Folder>")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{bcp.esc(meta['title'])}</name><open>0</open>"
        f"<description><![CDATA["
        f"NOAA Weather Radio All Hazards transmitters for {bcp.esc(state)}.<br/><br/>"
        f"One-way broadcast on the seven channels 162.400 to 162.550 MHz. "
        f"There is no input frequency, offset or access tone, so those are not "
        f"in this pack. The 1050 Hz alert tone and the SAME digital header are "
        f"not CTCSS or DCS.<br/><br/>"
        f"<b>Source:</b> NOAA/NWS Weather Radio county coverage data<br/>"
        f"{bcp.esc(meta['url'])}<br/>"
        f"<b>Licence:</b> public domain (NOAA/NWS)<br/>"
        f"<b>Station status read:</b> {bcp.esc(meta['built'])}. Status is live "
        f"data and is only true as of that date.<br/>"
        f"<b>Cross-check:</b> {bcp.esc(STATION_PAGE.format(state=state))}"
        f"]]></description>"
        # A mast with radiating arcs: an NWR transmitter is a broadcast tower
        # and nothing else. Embedded, because the tablet that needs it is the
        # one with no signal.
        f'<Style id="nwr"><IconStyle><scale>1.0</scale>'
        f"<Icon><href>icons/broadcast.png</href></Icon></IconStyle>"
        f"<LabelStyle><scale>0.8</scale></LabelStyle></Style>"
        f"{folders}</Document></kml>")


def build(state, out_dir, rows, coverage=False, url=CCL_URL, log=print,
         data_as_of=None, data_verified_live=True):
    state = state.strip().upper()
    picked = [s for s in rows if in_state(s, state, coverage=coverage)]
    sited = sum(1 for s in picked
                if str(s.get("sitestate") or "").upper() == state)
    # Spelled out rather than nested in the f-string: a nested f-string
    # spanning lines is 3.12 syntax and this has to run on 3.10.
    if coverage:
        border = len(picked) - sited
        where = (f" ({sited} sited in {state}, {border} border station(s) "
                 f"whose alerts reach it)")
    else:
        where = f" sited in {state}"
    log(f"[*] {state}: {len(picked)} transmitter(s){where}")

    off = [s for s in picked
           if str(s.get("freq") or "").strip() not in NWR_CHANNELS]
    if off:
        # Not dropped: the source's error to own, and hiding it would make the
        # next person re-discover it. Named so it can be checked.
        log(f"    [!] {len(off)} on a frequency that is not an NWR channel: "
            f"{', '.join(str(s.get('callsign')) for s in off[:6])}")

    built = dt.date.today().isoformat()
    # "built" is when THIS KMZ was assembled - true regardless of source, and
    # used for the edition stamp. "data_as_of" is when the underlying NWR
    # status is actually FROM, which is a different fact: a live fetch means
    # today really is both, but --from-file means the file can be an old
    # snapshot, and stamping it with today's date claimed NWS was checked
    # today when it was not. Rule 1 - never invent a value - covers a date
    # exactly as much as a coordinate or a capacity figure.
    if data_as_of is None:
        data_as_of = built
    meta = {"title": f"{state} NOAA Weather Radio ({built})",
            "url": url, "built": built, "data_as_of": data_as_of,
            "data_verified_live": data_verified_live}
    kml = pack_kml(state, picked, meta)
    # Amber for a working transmitter; the folder already separates
    # the dead ones, so one icon is enough.
    icons = {"icons/broadcast.png": glyphs.render("broadcast",
                                                 (255, 209, 64))}
    if meta["dropped_no_coords"]:
        log(f"    [!] {meta['dropped_no_coords']} transmitter(s) had no usable "
            f"coordinates and were left out rather than placed at a guess")

    stamp = bcp.edition(built, kml, icons)
    path = os.path.join(out_dir, f"{state}_WeatherRadio__{stamp}.kmz")
    size = bcp.write_kmz(path, kml, icons)
    drawn = len(picked) - meta["dropped_no_coords"]
    log(f"[*] {state}: {drawn} transmitter(s) -> {os.path.basename(path)} "
        f"({size // 1024} KB)")
    return path, drawn


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build a NOAA Weather Radio transmitter overlay for ATAK.")
    ap.add_argument("--state", default="MN", help="two-letter abbreviation")
    ap.add_argument("--out", default=os.path.expanduser("~/atak-packs"))
    ap.add_argument("--coverage", action="store_true",
                    help="include transmitters sited in a NEIGHBOURING state "
                         "whose alerts reach this one. Default is the physical "
                         "point layer: only transmitters sited here.")
    ap.add_argument("--from-file", metavar="FILE",
                    help="read an already-downloaded ccl-data.js, or the JSON "
                         "array parsed out of it, instead of fetching")
    ap.add_argument("--as-of", metavar="YYYY-MM-DD",
                    help="the date the --from-file snapshot is actually FROM, "
                         "if known. Without this, the file's own last-modified "
                         "date is used - never today's date, which would claim "
                         "NWS was checked today when it was not")
    ap.add_argument("--url", default=CCL_URL)
    a = ap.parse_args(argv)

    data_as_of, data_verified_live = None, True
    if a.from_file:
        text = open(a.from_file, encoding="utf-8").read()
        try:
            rows = json.loads(text)          # already-parsed array
        except json.JSONDecodeError:
            rows = parse_ccl(text)           # raw ccl-data.js
        if isinstance(rows, dict):           # a GeoJSON someone saved
            raise SystemExit(
                f"{a.from_file} looks like GeoJSON, not the ccl-data array. "
                f"This builder wants the NWS file so it can read the SAME "
                f"codes and status; pass the .js or the array it contains.")
        print(f"[*] {len(rows)} transmitters from {a.from_file}")
        data_verified_live = False
        if a.as_of:
            data_as_of = a.as_of
        else:
            mtime = os.path.getmtime(a.from_file)
            data_as_of = dt.date.fromtimestamp(mtime).isoformat()
            print(f"    [!] no --as-of given; using {a.from_file}'s own "
                 f"last-modified date ({data_as_of}) as the best available "
                 f"answer to 'as of when'. That is a proxy, not a guarantee -  "
                 f"a copy, download or extraction can change a file's "
                 f"modified time without changing what it says.")
    else:
        rows = fetch_ccl(a.url)

    os.makedirs(a.out, exist_ok=True)
    build(a.state, a.out, rows, coverage=a.coverage, url=a.url,
         data_as_of=data_as_of, data_verified_live=data_verified_live)
    return 0


if __name__ == "__main__":
    sys.exit(main())
