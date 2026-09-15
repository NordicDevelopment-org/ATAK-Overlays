#!/usr/bin/env python3
"""Airports and helipads for a state, from OpenStreetMap.

    python3 build_aviation_pack.py --state MN --out ~/atak-packs
    python3 build_aviation_pack.py --check            # offline, instant
    python3 build_aviation_pack.py --query            # see what it asks

Everything except this table lives in osm_pack.py, which build_emergency_pack
also uses. Two class tables, one code path.

ADAPTED FROM AN OVERPASS TURBO QUERY, WITH THREE CHANGES. Turbo is a browser
tool and its idioms do not survive the trip into this pipeline:

1. ({{bbox}}) is Turbo's placeholder. fetch_osm formats its query with
   str.format(), which turns {{bbox}} into the literal text {bbox}, and every
   mirror answers HTTP 400. The box is written ({s:.4f},{w:.4f},{n:.4f},{e:.4f})
   here and substituted per tile. This has already cost one live run.

2. Separate node/way/relation lines collapse to one `nwr`. Same result, a
   third of the query.

3. `out body; >; out skel qt;` becomes `out center tags;`. The Turbo form
   returns every member node of every way so the browser can draw the polygon.
   For a pack of point icons that is hundreds of thousands of untagged nodes
   fetched and thrown away, and a pin can only be placed at the centre anyway.
   If outlines are ever wanted, that is a different pack and a different
   writer, not a flag on this one.

WHAT IS NOT HERE. Runway surface and length live on the runway ways, not on
the aerodrome, so they are not in this pack - a length copied off one runway
onto the airport it belongs to would be an invented value. Polygon area and
building counts are not OSM tags at all; they are computed from geometry this
pack deliberately does not fetch.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import osm_pack                                             # noqa: E402

CLASSES = [
    ("airports", [(("aeroway", "=", "aerodrome"),)],
     "Airports"),
    # helipad and heliport are both in use and mean the same thing to someone
    # looking for a place a helicopter can land, so they share a folder.
    ("heliports", [(("aeroway", "=", "helipad"),),
                   (("aeroway", "=", "heliport"),)],
     "Helipads and heliports"),
]

DEFAULT_OFF = set()

# Read from the raw OSM tags. Every one of these is a real aviation
# identifier; nothing here is derived or computed.
FIELDS = [
    ("ICAO", "icao", ""),
    ("IATA", "iata", ""),
    ("Local ref", "ref", ""),
    ("Type", "aerodrome:type", ""),
    ("Elevation", "ele", "not recorded (metres above sea level when present)"),
    ("Surface", "surface", ""),
    ("Runways", "runways", ""),
]

SPEC = {
    "kind": "Aviation",
    "title": "airports and helipads",
    "classes": CLASSES,
    "default_off": DEFAULT_OFF,
    "fields": FIELDS,
}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Airports and helipads from OpenStreetMap.")
    osm_pack.add_arguments(ap, SPEC)
    return osm_pack.run(SPEC, argv, ap)


if __name__ == "__main__":
    sys.exit(main())
