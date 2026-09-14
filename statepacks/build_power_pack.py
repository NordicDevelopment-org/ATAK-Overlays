#!/usr/bin/env python3
"""Power plants as an ATAK overlay, from the EIA U.S. Energy Atlas.

    python3 build_power_pack.py --state MN --out ~/atak-packs
    python3 build_power_pack.py --state MN --from-file power_plants_mn.geojson
    python3 build_power_pack.py --state MN --min-mw 25      # drop the small stuff

THE SOURCE. EIA's "Power Plants in the US" feature service, the same layer the
Chisago generation records came from:

    https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/
        Power_Plants_in_the_US/FeatureServer/0

Licence: US Government work, public domain. The reporting period rides in the
`Period` field of every record and is carried into the pack rather than
replaced by the build date - "when EIA last reported this" and "when I built
the file" are different facts and a pack that conflates them is lying about
one of them.

TWO CAPACITIES, NEVER ONE. `Install_MW` is NAMEPLATE capacity and `Total_MW` is
MAXIMUM SUMMER capacity. They are different measurements of different things and
this pack keeps them apart and labelled, because a number called "capacity" with
no qualifier is unusable. Summer capacity is NOT always lower: Clay Boswell in
Minnesota reports 923.3 MW nameplate and 937.8 MW summer. That is EIA's number
and it is passed through, not "corrected" into what someone expected.

DENSITY. Minnesota is 782 plants and 641 of them are solar or wind, almost all
of them small. Left all-on that is not a map, it is a layer of dots over one.
So folders carry a count, the dense fuels start switched off, and `--min-mw`
exists for when even that is too much. Nothing is dropped from the file - it is
switched off, which is a thing you can undo from the tablet.
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_county_pack as bcp                             # noqa: E402
import glyphs                                                # noqa: E402

EIA_URL = ("https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
           "/Power_Plants_in_the_US/FeatureServer/0")
EIA_PAGE = 2000          # the service's own maxRecordCount

# EIA filters on the FULL state name, not the abbreviation. Nothing else in
# this repo needed that, so the table lives here rather than being bolted onto
# the FIPS map, which is keyed for a different purpose.
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}

# Which fuels start switched ON. The rule is mission value per placemark, not
# capacity: two reactors matter more than five hundred rooftop solar arrays,
# and a coal plant is a landmark. Everything else is one tap away.
DEFAULT_ON = ("nuclear", "coal", "natural gas", "hydroelectric", "petroleum",
              "batteries", "pumped storage", "geothermal")

# Glyph and colour per fuel. Shape carries what it is, colour carries the
# family, and both are set because colour alone fails in greyscale and for a
# colour-blind reader. Anything not listed falls back to a bolt, which is true
# of every entry here: they all make electricity.
FUEL_STYLE = {
    "nuclear":       ("trefoil",  (255, 240, 60)),
    "coal":          ("flame",    (170, 170, 175)),
    "natural gas":   ("flame",    (120, 200, 255)),
    "petroleum":     ("flame",    (255, 150, 70)),
    "biomass":       ("flame",    (150, 220, 120)),
    "geothermal":    ("flame",    (230, 130, 190)),
    "hydroelectric": ("droplet",  (90, 180, 255)),
    "pumped storage": ("droplet", (140, 200, 255)),
    "wind":          ("turbine",  (200, 235, 255)),
    "solar":         ("sun",      (255, 210, 70)),
    "batteries":     ("battery",  (140, 230, 190)),
}
FUEL_FALLBACK = ("bolt", (255, 209, 64))


def style_for(fuel):
    return FUEL_STYLE.get(fuel, FUEL_FALLBACK)


def style_id(fuel):
    return "f_" + bcp.safe(fuel).lower()


# Per-fuel capacity columns, for the breakdown on a mixed-fuel plant.
FUEL_MW = [("Nuclear_MW", "nuclear"), ("Coal_MW", "coal"), ("NG_MW", "natural gas"),
           ("Hydro_MW", "hydro"), ("HydroPS_MW", "pumped storage"),
           ("Wind_MW", "wind"), ("Solar_MW", "solar"), ("Bio_MW", "biomass"),
           ("Crude_MW", "petroleum"), ("Bat_MW", "battery"),
           ("Geo_MW", "geothermal"), ("Other_MW", "other")]


def as_mw(v):
    """A capacity as a float, or None. Several of these columns are typed as
    strings in the service schema, so this never assumes a number arrived."""
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_state(state, url=EIA_URL, log=print):
    """Every plant in one state, paged. The service caps a response at 2000."""
    full = STATE_NAMES.get(state)
    if not full:
        raise SystemExit(f"no EIA state name known for {state!r}. EIA filters "
                         f"on the full name, so add it to STATE_NAMES.")
    log(f"[*] fetching power plants for {full}")
    out, offset = [], 0
    while True:
        q = urllib.parse.urlencode({
            "where": f"State='{full}'", "outFields": "*", "outSR": "4326",
            "f": "geojson", "resultOffset": offset,
            "resultRecordCount": EIA_PAGE})
        raw = bcp.http_get(f"{url}/query?{q}", log=log)
        page = json.loads(raw.decode("utf-8", "replace"))
        feats = page.get("features") or []
        out.extend(feats)
        log(f"    {len(out)} plant(s)")
        # Trust the server's own "there is more" flag where it sets one, and
        # fall back to a short page meaning the end. Paging until an empty
        # response would make one extra request every single time.
        if not feats or not page.get("properties", {}).get("exceededTransferLimit"):
            if len(feats) < EIA_PAGE:
                break
        offset += len(feats)
    return out


def capacity_rows(p):
    """The two capacities, kept apart, plus the per-fuel split when mixed."""
    nameplate, summer = as_mw(p.get("Install_MW")), as_mw(p.get("Total_MW"))
    rows = [
        ("Nameplate capacity", f"{nameplate:,.1f} MW" if nameplate is not None else ""),
        ("Max summer capacity", f"{summer:,.1f} MW" if summer is not None else ""),
        ("Primary fuel", str(p.get("PrimSource") or "").strip()),
        ("Technology", str(p.get("tech_desc") or "").strip()),
        ("Operator", str(p.get("Utility_Na") or "").strip()),
        ("Sector", str(p.get("sector_nam") or "").strip()),
        ("County", str(p.get("County") or "").strip()),
        ("EIA plant code", str(p.get("Plant_Code") or "").strip()),
    ]
    split = [(name, as_mw(p.get(col))) for col, name in FUEL_MW]
    split = [(n, v) for n, v in split if v]
    return rows, split


def placemark(feat, meta):
    p = feat.get("properties") or {}
    geom = feat.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None, None
    try:
        lon, lat = float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return None, None

    fuel = str(p.get("PrimSource") or "unknown").strip().lower() or "unknown"
    rows, split = capacity_rows(p)
    body = ""
    for label, value in rows:
        if value:
            body += f"{bcp.esc(label)}: <b>{bcp.esc(value)}</b><br/>"
    missing = [label for label, value in rows if not value]
    if missing:
        body += (f'<br/><font color="{bcp.GREY}"><i>No data for: '
                 f'{bcp.esc(", ".join(missing))}</i></font><br/>')
    if len(split) > 1:
        # Only worth showing when a plant actually burns more than one thing.
        # On a single-fuel plant it just repeats the nameplate row.
        listed = ", ".join(f"{n} {v:,.1f} MW" for n, v in split)
        body += (f'<br/><font color="{bcp.GREY}"><i>By fuel: '
                 f'{bcp.esc(listed)}</i></font><br/>')

    footer = (f'<hr/><font color="{bcp.GREY}"><i>'
              f"Source: {bcp.esc(str(p.get('Source') or 'EIA'))}<br/>"
              f"{bcp.esc(meta['url'])}<br/>"
              f"EIA reporting period: {bcp.esc(str(p.get('Period') or 'not stated'))}<br/>"
              f"Licence: public domain (US EIA)<br/>"
              f"Pack built: {bcp.esc(meta['built'])}</i></font>")

    name = str(p.get("Plant_Name") or "").strip() or f"EIA {p.get('Plant_Code')}"
    nameplate = as_mw(p.get("Install_MW"))
    if nameplate is not None:
        name = f"{name} ({nameplate:,.1f} MW)"

    hidden = fuel not in DEFAULT_ON
    # Visibility is set on the PLACEMARK as well as the folder. ATAK's KML
    # import path is not a full KML renderer and is documented as honouring
    # per-placemark visibility more reliably than a folder's; setting both
    # costs nothing and works whichever one it reads.
    vis = "<visibility>0</visibility>" if hidden else ""
    pm = (f"<Placemark><name>{bcp.esc(name)}</name>{vis}"
          f"<description><![CDATA[{body}{footer}]]></description>"
          f"<styleUrl>#{style_id(fuel)}</styleUrl>"
          f"<Point><coordinates>{round(lon, 6)},{round(lat, 6)},0"
          f"</coordinates></Point></Placemark>")
    return fuel, pm


def pack_kml(state, feats, meta):
    by_fuel, dropped = {}, 0
    for f in feats:
        fuel, pm = placemark(f, meta)
        if pm is None:
            dropped += 1
            continue
        by_fuel.setdefault(fuel, []).append(pm)
    meta["dropped_no_coords"] = dropped

    # Biggest first by placemark count would put 519 solar at the top. Order by
    # whether it is on by default, then by name: what is switched on is what
    # someone is looking for.
    def rank(fuel):
        return (0 if fuel in DEFAULT_ON else 1, fuel)

    folders = ""
    for fuel in sorted(by_fuel, key=rank):
        pms = by_fuel[fuel]
        off = fuel not in DEFAULT_ON
        folders += (f"<Folder><name>{bcp.esc(fuel)} ({len(pms)})</name>"
                    f"<open>0</open>"
                    f"{'<visibility>0</visibility>' if off else ''}"
                    f"{''.join(pms)}</Folder>")

    # One style per fuel actually present, so a pack carries only the icons it
    # uses rather than the whole set.
    styles = ""
    for fuel in sorted(by_fuel):
        name, _rgb = style_for(fuel)
        styles += (f'<Style id="{style_id(fuel)}"><IconStyle><scale>1.0</scale>'
                   f"<Icon><href>icons/{name}.png</href></Icon></IconStyle>"
                   f"<LabelStyle><scale>0.8</scale></LabelStyle></Style>")

    periods = sorted({str((f.get('properties') or {}).get('Period') or '')
                      for f in feats} - {''})
    vintage = ", ".join(periods) or "not stated"
    on = [f for f in by_fuel if f in DEFAULT_ON]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{bcp.esc(meta['title'])}</name><open>0</open>"
        f"<description><![CDATA["
        f"Electric generating plants in {bcp.esc(state)}.<br/><br/>"
        f"<b>Two capacities, kept apart:</b> nameplate is the rated output, "
        f"max summer is what EIA reports it can actually deliver in summer "
        f"conditions. Summer is not always the smaller number.<br/><br/>"
        f"<b>Switched on by default:</b> {bcp.esc(', '.join(sorted(on)))}. "
        f"The rest are in the tree and switched off, because most of the "
        f"placemarks are small solar and wind sites.<br/><br/>"
        f"<b>Source:</b> EIA U.S. Energy Atlas, Power Plants<br/>"
        f"{bcp.esc(meta['url'])}<br/>"
        f"<b>EIA reporting period:</b> {bcp.esc(vintage)}<br/>"
        f"<b>Licence:</b> public domain (US EIA)<br/>"
        f"<b>Pack built:</b> {bcp.esc(meta['built'])}"
        f"]]></description>"
        f"{styles}"
        f"{folders}</Document></kml>")


def build(state, out_dir, feats, min_mw=0.0, url=EIA_URL, log=print):
    state = state.strip().upper()
    if min_mw:
        before = len(feats)
        feats = [f for f in feats
                 if (as_mw((f.get("properties") or {}).get("Install_MW")) or 0) >= min_mw]
        log(f"    --min-mw {min_mw:g}: {len(feats)} of {before} plant(s) kept")

    built = dt.date.today().isoformat()
    meta = {"title": f"{state} Power Plants ({built})", "url": url, "built": built}
    kml = pack_kml(state, feats, meta)
    icons = {}
    for f in feats:
        fuel = str((f.get("properties") or {}).get("PrimSource")
                   or "unknown").strip().lower() or "unknown"
        name, rgb = style_for(fuel)
        icons.setdefault(f"icons/{name}.png", glyphs.render(name, rgb))
    if meta["dropped_no_coords"]:
        log(f"    [!] {meta['dropped_no_coords']} plant(s) had no usable "
            f"coordinates and were left out rather than placed at a guess")

    stamp = built.replace("-", "_")
    path = os.path.join(out_dir, f"{state}_PowerPlants__{bcp.safe(stamp)}.kmz")
    size = bcp.write_kmz(path, kml, icons)
    drawn = len(feats) - meta["dropped_no_coords"]
    log(f"[*] {state}: {drawn} plant(s) -> {os.path.basename(path)} "
        f"({size // 1024} KB)")
    return path, drawn


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build an EIA power plant overlay for ATAK.")
    ap.add_argument("--state", default="MN")
    ap.add_argument("--out", default=os.path.expanduser("~/atak-packs"))
    ap.add_argument("--from-file", metavar="FILE",
                    help="a GeoJSON already pulled from the EIA service")
    ap.add_argument("--min-mw", type=float, default=0.0,
                    help="skip plants below this nameplate capacity. Nothing "
                         "is dropped by default; the dense fuels are switched "
                         "off instead, which you can undo from the tablet.")
    ap.add_argument("--url", default=EIA_URL)
    a = ap.parse_args(argv)
    state = a.state.strip().upper()

    if a.from_file:
        doc = json.load(open(a.from_file, encoding="utf-8"))
        feats = doc.get("features") if isinstance(doc, dict) else doc
        if not isinstance(feats, list):
            raise SystemExit(f"{a.from_file}: expected GeoJSON with a features list")
        print(f"[*] {len(feats)} plant(s) from {a.from_file}")
    else:
        feats = fetch_state(state, a.url)

    os.makedirs(a.out, exist_ok=True)
    build(state, a.out, feats, min_mw=a.min_mw, url=a.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
