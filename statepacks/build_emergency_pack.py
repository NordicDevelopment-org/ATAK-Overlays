#!/usr/bin/env python3
"""Emergency services, health and corrections for a state, from OpenStreetMap.

    python3 build_emergency_pack.py --state MN --out ~/atak-packs
    python3 build_emergency_pack.py --state MN --only police,correctional
    python3 build_emergency_pack.py --state MN --counts      # ask, do not build

WHY OSM AND NOT THE CATALOG'S FIRST CHOICE. Every HIFLD source for these
layers pointed at maps.nccs.nasa.gov, which stopped resolving in September
2026 - not blocked, gone from DNS. They are `enabled: false, confidence:
dead` in the catalog and there is nothing to fall back to for most of them.
OSM is the one source for this sector that has actually been fetched live
from this project, twice, for two states.

WHAT THAT COSTS, SAID PLAINLY. OSM is contributed, not surveyed. Coverage is
uneven and nothing here pretends otherwise: the build prints how many
features each class returned so a thin layer looks thin instead of looking
like an answer. Measured for Minnesota police in an earlier run: 517 features
in the state box, 32 carrying a phone number. That is the shape of this data.

The tiling, mirror rotation, rate-limit handling, caching and tile-splitting
are NOT reimplemented here - they are seed_le_contacts.fetch_osm, which is
the code that has survived two live state runs. This file chooses the tags,
the folders and the symbols.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import osm_pack                                            # noqa: E402

# Each class is (layer, [selectors], folder label).
#
# A SELECTOR is a tuple of clauses that must ALL hold, and a clause is
# (key, op, value) with op "=" or "~". Structured, not a string: the first
# version wrote them as "amenity=clinic][urgent_care=yes" and emitted
# nwr["amenity=clinic][urgent_care=yes"], which asks Overpass for a tag whose
# KEY is that entire string. It is valid QL, it returns nothing, and it looks
# like "there are no urgent care clinics in Minnesota". The repeater
# diagnostic already cost a live run to exactly this mistake.
#
# A "~" value is a POSIX ERE - no inline flags. Write "(?i)" at the front and
# the emitter turns it into Overpass's ,i modifier; it never reaches the
# server as text.
#
# The layer name is not decoration: symbology.py decides icon and colour from
# it, and a class naming a layer symbology does not know fails check_classes
# offline rather than building a folder of invisible placemarks.
CLASSES = [
    ("hospitals", [(("amenity", "=", "hospital"),)],
     "Hospitals"),
    ("urgent_care", [(("amenity", "=", "clinic"), ("urgent_care", "=", "yes")),
                     (("healthcare", "=", "clinic"), ("urgent_care", "=", "yes"))],
     "Urgent care"),
    ("nursing_homes", [(("amenity", "=", "nursing_home"),),
                       (("social_facility", "=", "nursing_home"),),
                       (("social_facility", "=", "assisted_living"),)],
     "Nursing and assisted living"),
    ("ems", [(("emergency", "=", "ambulance_station"),)],
     "EMS stations"),
    ("fire_stations", [(("amenity", "=", "fire_station"),)],
     "Fire stations"),
    ("police", [(("amenity", "=", "police"),)],
     "Law enforcement"),
    ("correctional", [(("amenity", "=", "prison"),)],
     "Correctional facilities"),
    ("eoc", [(("emergency", "=", "disaster_response"),),
             (("office", "=", "government"),
              ("government", "=", "emergency_management"))],
     "Emergency operations"),
    ("government", [(("amenity", "=", "townhall"),),
                    (("amenity", "=", "courthouse"),)],
     "Government and courts"),
    ("schools", [(("amenity", "=", "school"),)],
     "Schools"),
    ("shelters", [(("amenity", "=", "shelter"),
                   ("shelter_type", "~", "^(emergency|disaster|storm|civil)")),
                  (("social_facility", "=", "shelter"),)],
     "Shelters"),
]

# Folders that import SWITCHED OFF. docs/ATAK.md: a dense layer imports off so
# the tablet stays responsive, and one tap turns it on.
#
# Named explicitly rather than chosen by a count threshold. A threshold would
# silently flip a layer off in the one state where it happens to be dense, so
# the same pack would behave differently in Minnesota and Wyoming for no
# reason anybody could see in the file. Measured for Minnesota: schools 2,784
# and government 1,207 are 64% of the whole pack, and neither is why someone
# opens an emergency overlay.

DEFAULT_OFF = {"schools", "government"}

# Fields beyond the common ones, read from the raw OSM tags.
FIELDS = [
    ("Emergency dept", "emergency", ""),
    ("Beds", "beds", ""),
]

SPEC = {
    "kind": "Emergency",
    "title": "emergency services and health",
    "classes": CLASSES,
    "default_off": DEFAULT_OFF,
    "fields": FIELDS,
}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Emergency services, health and corrections from OSM.")
    osm_pack.add_arguments(ap, SPEC)
    return osm_pack.run(SPEC, argv, ap)


if __name__ == "__main__":
    sys.exit(main())
