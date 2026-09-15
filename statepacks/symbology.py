#!/usr/bin/env python3
"""One table: which symbol and colour every layer gets, and why.

    python3 symbology.py                 # the table
    python3 symbology.py --check         # every catalog layer covered?
    python3 symbology.py --sheet out.png # look at the icons

WHY ONE TABLE. Three builders had already picked their own icons and colours
independently (power by fuel, NWR fixed, repeaters by mode), which is fine
until a fourth wants a colour that already means something else. A reader
learns "orange means oil and gas" once, or never.

HOW A ROW IS CHOSEN
- Shape says WHAT the thing is. It is the only channel that survives
  greyscale, colour-blindness, and a washed-out screen in daylight.
- Colour says WHICH SECTOR it belongs to, so a glance separates power from
  water without reading a single label.
- Where two layers honestly are the same kind of thing (food processing and
  livestock operations are both "a plant"), they share a shape. Inventing a
  distinct picture for a distinction that is not there is still inventing.

`geometry` is what the source actually provides, not what we wish it were:
`line` and `area` layers get a LineStyle/PolyStyle and no icon at all, which
is why a few rows have no glyph. A point icon on a 400-mile pipeline would be
a pin at its midpoint, and a midpoint is not a place.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glyphs                                                # noqa: E402

CATALOG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "catalog")

# One colour per sector. Picked to stay apart on a dark tactical basemap AND
# in greyscale: the luminance of these differs, not just the hue.
SECTOR = {
    "Energy - Electric":  (255, 209, 64),    # amber
    "Energy - Oil & Gas": (255, 150, 60),    # orange
    "Water":              (110, 200, 255),   # ice blue
    "Emergency & Health": (255, 110, 110),   # red
    "Communications":     (170, 230, 140),   # green
    "Transportation":     (200, 175, 255),   # violet
    "Chemical & Hazmat":  (240, 230, 120),   # acid yellow
    "Base":               (190, 195, 205),   # grey
}

# layer -> (glyph or None, sector, geometry)
# geometry: point | line | area. None as a glyph means the layer draws as a
# line or a polygon and never gets an icon.
LAYERS = {
    # --- Energy - Electric -------------------------------------------------
    "power_plants":        ("bolt",       "Energy - Electric",  "point"),
    "nuclear_reactors":    ("trefoil",    "Energy - Electric",  "point"),
    "generators":          ("bolt",       "Energy - Electric",  "point"),
    "substations":         ("substation", "Energy - Electric",  "point"),
    "power_towers":        ("pylon",      "Energy - Electric",  "point"),
    "battery_storage":     ("battery",    "Energy - Electric",  "point"),
    "transmission_lines":  (None,         "Energy - Electric",  "line"),
    "service_territories": (None,         "Energy - Electric",  "area"),
    "rto_regions":         (None,         "Energy - Electric",  "area"),

    # --- Energy - Oil & Gas ------------------------------------------------
    "refineries":          ("refinery",   "Energy - Oil & Gas", "point"),
    "gas_processing":      ("well",       "Energy - Oil & Gas", "point"),
    "compressor_stations": ("pipeline",   "Energy - Oil & Gas", "point"),
    "gas_storage":         ("tank",       "Energy - Oil & Gas", "point"),
    "lng_terminals":       ("tank",       "Energy - Oil & Gas", "point"),
    "fuel_terminals":      ("tank",       "Energy - Oil & Gas", "point"),
    "fuel_stations":       ("pump",       "Energy - Oil & Gas", "point"),
    "ethanol_plants":      ("factory",    "Energy - Oil & Gas", "point"),
    "biodiesel_plants":    ("factory",    "Energy - Oil & Gas", "point"),
    "pipelines":           (None,         "Energy - Oil & Gas", "line"),

    # --- Water -------------------------------------------------------------
    "dams":                ("dam",        "Water",              "point"),
    "water_treatment":     ("clarifier",  "Water",              "point"),
    "wastewater_treatment": ("clarifier", "Water",              "point"),
    "water_towers":        ("water_tower", "Water",             "point"),
    "water_wells":         ("well",       "Water",              "point"),
    "reservoirs":          (None,         "Water",              "area"),
    "levees":              (None,         "Water",              "line"),
    "leveed_areas":        (None,         "Water",              "area"),
    "water_service_areas": (None,         "Water",              "area"),

    # --- Emergency & Health ------------------------------------------------
    "hospitals":           ("cross",      "Emergency & Health", "point"),
    "urgent_care":         ("cross",      "Emergency & Health", "point"),
    "nursing_homes":       ("cross",      "Emergency & Health", "point"),
    "pharmacies":          ("cross",      "Emergency & Health", "point"),
    "ems":                 ("star_of_life", "Emergency & Health", "point"),
    "fire_stations":       ("hydrant",    "Emergency & Health", "point"),
    "police":              ("shield",     "Emergency & Health", "point"),
    "correctional":        ("bars",       "Emergency & Health", "point"),
    "eoc":                 ("capitol",    "Emergency & Health", "point"),
    "government":          ("capitol",    "Emergency & Health", "point"),
    "schools":             ("mortarboard", "Emergency & Health", "point"),
    "shelters":            ("shelter",    "Emergency & Health", "point"),

    # --- Communications ----------------------------------------------------
    "comm_towers":         ("tower",      "Communications",     "point"),
    "broadcast_towers":    ("broadcast",  "Communications",     "point"),
    "telecom_exchanges":   ("server",     "Communications",     "point"),
    "data_centers":        ("server",     "Communications",     "point"),
    "psap":                (None,         "Communications",     "area"),

    # --- Transportation ----------------------------------------------------
    "airports":            ("plane",      "Transportation",     "point"),
    "heliports":           ("helipad",    "Transportation",     "point"),
    "ports":               ("anchor",     "Transportation",     "point"),
    "bridges":             ("bridge",     "Transportation",     "point"),
    "rail_facilities":     ("rail",       "Transportation",     "point"),
    "rail_crossings":      ("rail",       "Transportation",     "point"),
    "industrial":          ("factory",    "Transportation",     "point"),
    "railways":            (None,         "Transportation",     "line"),

    # --- Chemical & Hazmat -------------------------------------------------
    "chemical_plants":     ("flask",      "Chemical & Hazmat",  "point"),
    "hazmat_storage":      ("flask",      "Chemical & Hazmat",  "point"),
    "explosives_storage":  ("burst",      "Chemical & Hazmat",  "point"),
    "mines":               ("pick",       "Chemical & Hazmat",  "point"),
    "grain_storage":       ("silo",       "Chemical & Hazmat",  "point"),
    "food_processing":     ("factory",    "Chemical & Hazmat",  "point"),
    "livestock_operations": ("factory",   "Chemical & Hazmat",  "point"),
    "agri_facilities":     ("factory",    "Chemical & Hazmat",  "point"),

    # --- Base --------------------------------------------------------------
    "address_points":      (None,         "Base",               "point"),
    "building_footprints": (None,         "Base",               "area"),
    "parcels":             (None,         "Base",               "area"),
    "roads":               (None,         "Base",               "line"),
    "city_boundaries":     (None,         "Base",               "area"),
    "county_boundary":     (None,         "Base",               "area"),
    "state_boundary":      (None,         "Base",               "area"),
}

# Layers built by statepacks/ rather than the catalog. Same table so the
# colours cannot drift apart from the ones above.
STATEPACK_LAYERS = {
    "repeaters":           ("repeater",   "Communications",     "point"),
    "nwr_transmitters":    ("broadcast",  "Communications",     "point"),
}


# power_plants subdivides by fuel, which is the one place a layer earns more
# than one symbol: "a power plant" is not a useful thing to know when the
# question is whether it needs fuel delivered, cooling water, or neither. The
# sector colour is overridden here on purpose - fuel IS the distinction being
# drawn, so it gets the colour channel. Kept in this file so the table below
# stays the only place symbols are chosen.
FUEL_STYLE = {
    "nuclear":        ("trefoil",  (255, 240, 60)),
    "coal":           ("flame",    (170, 170, 175)),
    "natural gas":    ("flame",    (120, 200, 255)),
    "petroleum":      ("flame",    (255, 150, 70)),
    "biomass":        ("flame",    (150, 220, 120)),
    "geothermal":     ("flame",    (230, 130, 190)),
    "hydroelectric":  ("droplet",  (90, 180, 255)),
    "pumped storage": ("droplet",  (140, 200, 255)),
    "wind":           ("turbine",  (200, 235, 255)),
    "solar":          ("sun",      (255, 210, 70)),
    "batteries":      ("battery",  (140, 230, 190)),
}


def colour_for(layer):
    """The sector colour for a layer, or None if the layer is unknown."""
    row = LAYERS.get(layer) or STATEPACK_LAYERS.get(layer)
    return SECTOR[row[1]] if row else None


def glyph_for(layer):
    """The glyph name, or None for a line/area layer that gets no icon."""
    row = LAYERS.get(layer) or STATEPACK_LAYERS.get(layer)
    return row[0] if row else None


def icon_for(layer, size=glyphs.SIZE):
    """PNG bytes for a layer's icon, or None if it does not take one."""
    name = glyph_for(layer)
    if name is None:
        return None
    return glyphs.render(name, colour_for(layer), size=size)


def catalog_layers(path=CATALOG):
    """Every layer name the catalog defines."""
    names = set()
    for dirpath, _dirnames, filenames in os.walk(path):
        for fn in sorted(filenames):
            if not fn.endswith((".yaml", ".yml")):
                continue
            text = open(os.path.join(dirpath, fn),
                        encoding="utf-8", errors="replace").read()
            names.update(re.findall(r"^  - layer:\s*(\S+)\s*$", text, re.M))
    return names


def check(path=CATALOG, log=print):
    """Problems, as a list. Empty means the table covers the catalog.

    This ENFORCES rather than documents. A layer added to the catalog with no
    row here would otherwise build with no icon and no complaint, and nobody
    finds that until they are looking at the map in the field.
    """
    problems = []
    known = glyphs.glyph_names()
    for layer, (glyph, sector, geom) in sorted(
            list(LAYERS.items()) + list(STATEPACK_LAYERS.items())):
        if sector not in SECTOR:
            problems.append(f"{layer}: sector {sector!r} has no colour")
        if geom not in ("point", "line", "area"):
            problems.append(f"{layer}: geometry {geom!r} is not point/line/area")
        if glyph is not None and glyph not in known:
            problems.append(f"{layer}: glyph {glyph!r} does not exist")
        if glyph is None and geom == "point" and layer not in (
                "address_points",):
            problems.append(f"{layer}: a point layer with no glyph gets no icon")

    missing = catalog_layers(path) - set(LAYERS)
    for layer in sorted(missing):
        problems.append(f"{layer}: in the catalog, missing from this table")
    extra = set(LAYERS) - catalog_layers(path)
    for layer in sorted(extra):
        problems.append(f"{layer}: in this table, not in the catalog")

    for fuel, (g, rgb) in sorted(FUEL_STYLE.items()):
        if g not in known:
            problems.append(f"fuel {fuel!r}: glyph {g!r} does not exist")
        if len(rgb) != 3 or not all(0 <= c <= 255 for c in rgb):
            problems.append(f"fuel {fuel!r}: {rgb} is not an 8-bit rgb triple")

    used = {g for g, _s, _t in
            list(LAYERS.values()) + list(STATEPACK_LAYERS.values()) if g}
    used |= {g for g, _rgb in FUEL_STYLE.values()}
    unused = set(known) - used - {None}
    for g in sorted(unused):
        log(f"  note: glyph {g!r} is drawn but no layer uses it")
    return problems


def table(log=print):
    rows = sorted(list(LAYERS.items()) + list(STATEPACK_LAYERS.items()),
                  key=lambda kv: (kv[1][1], kv[0]))
    sector = None
    for layer, (glyph, sec, geom) in rows:
        if sec != sector:
            sector = sec
            r, g, b = SECTOR[sec]
            log(f"\n{sec}   rgb({r},{g},{b})")
            log(f"  {'layer':24} {'icon':14} geometry")
            log("  " + "-" * 48)
        log(f"  {layer:24} {str(glyph or '-'):14} {geom}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Layer symbology table.")
    ap.add_argument("--check", action="store_true",
                    help="verify the table covers the catalog")
    ap.add_argument("--sheet", help="write a PNG of every layer icon")
    a = ap.parse_args(argv)

    if a.sheet:
        glyphs.sheet(a.sheet)
        return 0
    if a.check:
        problems = check()
        for p in problems:
            print(f"  [!] {p}")
        print(f"\n{len(problems)} problem(s)")
        return 1 if problems else 0
    table()
    n = sum(1 for g, _s, _t in LAYERS.values() if g)
    print(f"\n{len(LAYERS)} catalog layer(s) + {len(STATEPACK_LAYERS)} "
          f"statepack layer(s); {n} take an icon, "
          f"{len(LAYERS) - n} draw as a line or polygon.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # `symbology.py | head` closes the pipe partway through. That is the
        # obvious thing to do with a 70-row table and it should not print a
        # traceback. Drop our end cleanly so the interpreter does not try to
        # flush into the closed pipe on the way out.
        try:
            sys.stdout.close()
        finally:
            os._exit(0)
