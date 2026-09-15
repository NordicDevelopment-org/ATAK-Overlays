#!/usr/bin/env python3
"""Map symbols drawn in pure Python and embedded in the KMZ.

    python3 glyphs.py --sheet /tmp/glyphs.png     # look at them

WHY NOT A REMOTE URL. The first version of the power pack pointed IconStyle at
http://maps.google.com/mapfiles/kml/shapes/electronics.png. It rendered as a
television, which is how the real problem announced itself: a pack built for a
tablet that is offline in the field cannot fetch its own icons. docs/ATAK.md
already said so - "ATAK resolves relative icon hrefs inside the KMZ; remote http
icons would need network" - and the pack ignored it.

WHY NOT AN IMAGE LIBRARY. `pkg install python` and nothing else is the whole
install story for this on a phone. Pillow is not in it. These are PNGs written
by hand: a scanline polygon fill, supersampled, then zlib and four chunks.

WHY NOT ATAK'S OWN SYMBOLS - CORRECTION. This file used to say ATAK resolves
2525C only for CoT markers and not for a KML placemark. That is wrong. ATAK's
KML importer passes an IconStyle href containing a colon through unchanged, and
`asset` is a registered scheme, so `asset://mil-std-2525c/sfgpiue---h----.png`
reaches the 2525C PNGs inside ATAK's own APK. Read from ATAK-CIV source, not
run on a device - see docs/ATAK.md "MIL-STD-2525C from the APK".

Embedded glyphs stay the primary anyway, for reasons the correction does not
touch: a file in the zip renders on every ATAK version, on WinTAK and iTAK and
in Google Earth, and cannot be moved or renamed out from under a pack by an
APK update. `asset:` is the optional nicety, and only after someone confirms it
on a device the way TIGERweb and ACS were confirmed.

WHAT MAKES A GLYPH READABLE ON A TACTICAL MAP. It sits over satellite imagery
and a dark basemap at maybe 5 mm across. So: one silhouette, no interior
detail, a dark outline so it survives a light background, and a shape that is
still itself at 24 px. Colour carries the sector, shape carries the thing -
both, because colour alone fails for a colour-blind reader and in greyscale.
"""
import argparse
import math
import struct
import sys
import zlib

SIZE = 64          # ATAK treats the pixel size literally; 32 is ~1.8 mm on a
                   # 440 ppi phone, which is too small to read at a glance.
SS = 3             # supersampling factor
OUTLINE = (12, 14, 16)
OUTLINE_MARGIN = 0.12       # constant coordinate-space width of the outline
                            # band, the same all the way around every glyph


# --------------------------------------------------------------------------
# geometry helpers - everything is a list of closed rings in [-1, 1]
# --------------------------------------------------------------------------
# Every glyph is scaled to this maximum dimension and centred, so the set
# reads as one set. Two things forced it:
#
# CLIPPING. The outline is a constant-width band (OUTLINE_MARGIN) drawn
# outside the fill, so a glyph reaching past 1.0 - OUTLINE_MARGIN has its
# outline cut off at the edge of the image. Measured before this existed:
# broadcast 2.09 and repeater 2.08 were losing outline on every side, and
# anchor 1.94 on two.
#
# CONSISTENCY. Sizes ran 1.52 (battery) to 2.09 (broadcast), a 37% spread, so
# neighbouring pins in the same pack looked like different weights of the same
# idea. Normalising the bounding box is the ordinary way an icon set is made
# to sit evenly; it is applied at render time so each glyph is still authored
# in whatever coordinates its shape is natural in.
#
# This is GEOMETRIC, not optical: a solid square and a thin cross of the same
# bounding box do not carry the same visual weight. Where that shows, the fix
# is to redraw that one glyph, not to add a fudge factor here that moves every
# other one at the same time.
GLYPH_EXTENT = 1.66          # leaves 0.05 of margin after OUTLINE_MARGIN


def normalize(subpaths, extent=GLYPH_EXTENT):
    """Scale and centre a glyph so its longest side is `extent`."""
    pts = [p for sp in subpaths for p in sp]
    if not pts:
        return subpaths
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    longest = max(w, h)
    if longest <= 0:
        return subpaths
    k = extent / longest
    cx = (max(xs) + min(xs)) / 2.0
    cy = (max(ys) + min(ys)) / 2.0
    return [[((x - cx) * k, (y - cy) * k) for x, y in sp] for sp in subpaths]


def circle(cx, cy, r, n=64):
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def wedge(cx, cy, r, a0, a1, n=24):
    """A pie slice from a0 to a1 radians, including the centre point."""
    pts = [(cx, cy)]
    for i in range(n + 1):
        a = a0 + (a1 - a0) * i / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def arc_band(cx, cy, r_out, r_in, a0, a1, n=28):
    """A thick arc: out along the outer radius, back along the inner one."""
    fwd = [(cx + r_out * math.cos(a0 + (a1 - a0) * i / n),
            cy + r_out * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]
    back = [(cx + r_in * math.cos(a1 - (a1 - a0) * i / n),
             cy + r_in * math.sin(a1 - (a1 - a0) * i / n)) for i in range(n + 1)]
    return fwd + back


# --------------------------------------------------------------------------
# the glyphs
# --------------------------------------------------------------------------
def hole(ring):
    """The same ring wound the other way, so nonzero winding cuts it out.

    The fill is nonzero, not even-odd (see _coverage), so an inner ring drawn
    in the same direction as its outer one UNIONS with it and the shape comes
    out solid. Three glyphs shipped as featureless blobs before anyone looked
    at a rendering of them.
    """
    return list(reversed(ring))


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def bar(cx, cy, w, h, ang=0.0):
    """A rectangle rotated about its own centre."""
    ca, sa = math.cos(ang), math.sin(ang)
    return [(cx + x * ca - y * sa, cy + x * sa + y * ca)
            for x, y in ((-w, -h), (w, -h), (w, h), (-w, h))]


def g_bolt():
    """Lightning bolt. Generation of any kind."""
    return [[(-0.08, 0.92), (0.50, 0.92), (0.14, 0.20), (0.60, 0.20),
             (-0.26, -0.92), (-0.02, -0.10), (-0.48, -0.10)]]


def g_trefoil():
    """The radiation trefoil. Three 60-degree blades at 120-degree spacing
    around a central disc, which is the construction the federal placarding
    rules describe (49 CFR 172 Appendix B, 10 CFR 20.1901). Drawn from that
    description rather than traced from anyone's artwork."""
    subs = []
    for k in range(3):
        mid = math.radians(90 + k * 120)
        subs.append(wedge(0, 0, 0.95, mid - math.radians(30),
                          mid + math.radians(30)))
    subs.append(circle(0, 0, 0.22))
    return subs


def g_flame():
    """A flame. Anything that burns to make power: coal, gas, oil, biomass.

    Asymmetric on purpose. The first version was a lumpy polygon that read as a
    tooth; the second was a smooth teardrop, which at 24 px is indistinguishable
    from the droplet used for hydro - two fuels, one silhouette, which is worse
    than an ugly icon. A flame leans, and its base is notched rather than round.
    Those two things are what separate it from a drop of water.
    """
    # Hand-placed outline, tip first, down the right side and back up the left.
    pts = [
        (0.10, 0.96),                       # tip, leaning right
        (0.34, 0.50), (0.30, 0.26),         # shoulder curling in
        (0.52, 0.12), (0.62, -0.22),        # bulge on the right
        (0.50, -0.60), (0.22, -0.84),       # down to the base
        (0.00, -0.72),                      # the notch: base kicks UP in the
                                            # middle, which a droplet never does
        (-0.22, -0.84), (-0.50, -0.60),
        (-0.60, -0.20), (-0.46, 0.10),      # up the left side
        (-0.24, 0.04),                      # the small secondary lick
        (-0.22, 0.34), (-0.06, 0.58),
    ]
    return [pts]


def g_droplet():
    """A water drop. Hydroelectric."""
    pts = [(0.0, 0.95)]
    for i in range(1, 49):
        a = math.radians(90 + i * 300 / 48)
        pts.append((0.58 * math.cos(a), -0.18 + 0.62 * math.sin(a)))
    return [pts]


def g_turbine():
    """A mast with a three-blade rotor. Wind.

    The rotor alone read as an asterisk, because three thin spokes is what an
    asterisk is. A tower under it is what makes it a wind turbine, and the
    blades are tapered rather than straight so they hold their shape small.
    """
    hub = (0.0, 0.40)
    subs = [[(-0.07, -0.95), (0.07, -0.95), (0.05, 0.40), (-0.05, 0.40)]]
    for k in range(3):
        a = math.radians(90 + k * 120)
        tip = (hub[0] + 0.56 * math.cos(a), hub[1] + 0.56 * math.sin(a))
        # a tapered blade: wide at the hub, narrow at the tip
        p1 = (hub[0] + 0.15 * math.cos(a + 1.9), hub[1] + 0.15 * math.sin(a + 1.9))
        p2 = (tip[0] + 0.05 * math.cos(a + 1.9), tip[1] + 0.05 * math.sin(a + 1.9))
        p3 = (tip[0] + 0.05 * math.cos(a - 1.9), tip[1] + 0.05 * math.sin(a - 1.9))
        p4 = (hub[0] + 0.15 * math.cos(a - 1.9), hub[1] + 0.15 * math.sin(a - 1.9))
        subs.append([p1, p2, p3, p4])
    subs.append(circle(hub[0], hub[1], 0.13))
    return subs


def g_sun():
    """A disc with rays. Solar."""
    subs = [circle(0, 0, 0.42)]
    for k in range(8):
        a = math.radians(k * 45)
        subs.append(arc_band(0, 0, 0.92, 0.56, a - 0.16, a + 0.16, n=6))
    return subs


def g_battery():
    """A cell with a terminal. Storage."""
    return [[(-0.62, 0.52), (0.62, 0.52), (0.62, -0.72), (-0.62, -0.72)],
            [(-0.22, 0.80), (0.22, 0.80), (0.22, 0.52), (-0.22, 0.52)]]


def g_broadcast():
    """A mast with radiating arcs. One-way broadcast: weather radio.

    Both the mast and the arcs got thicker. The first version put three thin
    arcs in the top corners and they disappeared against imagery, which is the
    one thing this symbol cannot afford - the arcs ARE the meaning, the mast
    on its own is just a tower.
    """
    top = 0.16
    subs = [[(-0.11, top), (0.11, top), (0.30, -0.95), (0.13, -0.95),
             (0.0, -0.30), (-0.13, -0.95), (-0.30, -0.95)]]
    for r in (0.46, 0.74, 1.00):
        for sign in (1, -1):
            a0, a1 = math.radians(30), math.radians(80)
            band = arc_band(0, top, r, r - 0.18, a0, a1, n=18)
            subs.append([(sign * x, y) for x, y in band])
    return subs


def g_tower():
    """A lattice tower. Comms structures generally."""
    return [[(-0.52, -0.92), (-0.30, -0.92), (-0.09, 0.42), (0.09, 0.42),
             (0.30, -0.92), (0.52, -0.92), (0.16, 0.56), (0.16, 0.92),
             (-0.16, 0.92), (-0.16, 0.56)]]


def g_repeater():
    """A lattice tower with arcs on both sides. An amateur repeater.

    Deliberately not the broadcast mast: NWR is one-way and this is not, and
    two layers that mean different things must not share a silhouette. The
    lattice legs are what tells them apart at icon size.
    """
    subs = [[(-0.46, -0.95), (-0.26, -0.95), (-0.08, 0.30), (0.08, 0.30),
             (0.26, -0.95), (0.46, -0.95), (0.14, 0.42), (0.14, 0.62),
             (-0.14, 0.62), (-0.14, 0.42)]]
    for r in (0.40, 0.66):
        for sign in (1, -1):
            band = arc_band(0, 0.50, r, r - 0.15,
                            math.radians(24), math.radians(72), n=16)
            subs.append([(sign * x, y) for x, y in band])
    return subs



# --------------------------------------------------------------------------
# Infrastructure. One silhouette each, no interior detail, readable at 24 px.
# Where two layers would need the same shape to be honest (food processing and
# livestock are both "a plant"), they share it and the colour separates them -
# a made-up distinction drawn as a picture is still a made-up distinction.
# --------------------------------------------------------------------------
def g_dam():
    """A dam in elevation: wall, spillway notch, water below.

    The arch-from-above version read as a plain dome, which is a hill. The
    elevation has the one feature nothing else here has - a notch cut in the
    top edge with water falling out of it.
    """
    # Cross-section, not elevation. The elevation versions read as a
    # lampshade: a wall alone is just a shape. What makes it a dam is water
    # standing against one face and nothing against the other, so that is
    # what is drawn - a battered wall with the reservoir stacked behind it.
    wall = [(-0.06, 0.88), (0.26, 0.88), (0.88, -0.84), (-0.06, -0.84)]
    water = [rect(-0.92, y - 0.11, -0.14, y + 0.11)
             for y in (0.54, 0.16, -0.22)]
    return [wall] + water + [rect(-0.96, -0.96, 0.96, -0.84)]


def g_substation():
    """A transformer: tank with two bushings. Not a bolt - a bolt already
    means generation, and a substation generates nothing."""
    return [rect(-0.72, -0.72, 0.72, 0.28),
            bar(-0.34, 0.54, 0.13, 0.30), bar(0.34, 0.54, 0.13, 0.30),
            rect(-0.52, 0.22, 0.52, 0.34)]


def g_pylon():
    """A lattice tower with crossarms - transmission, not broadcast."""
    return [[(-0.62, -0.92), (-0.30, -0.92), (-0.10, 0.52), (0.10, 0.52),
             (0.30, -0.92), (0.62, -0.92), (0.30, 0.62), (-0.30, 0.62)],
            rect(-0.86, 0.62, 0.86, 0.78)]


def g_pipeline():
    """A pipe run with flanges at both ends."""
    return [rect(-0.92, -0.26, 0.92, 0.26),
            rect(-0.92, -0.62, -0.62, 0.62), rect(0.62, -0.62, 0.92, 0.62)]


def g_refinery():
    """A distillation column: tall, capped, on a skirt."""
    tall = [(-0.72, -0.72), (-0.28, -0.72), (-0.28, 0.62),
            (-0.38, 0.86), (-0.62, 0.86), (-0.72, 0.62)]
    short = [(0.18, -0.72), (0.62, -0.72), (0.62, 0.20),
             (0.52, 0.40), (0.28, 0.40), (0.18, 0.20)]
    return [tall, short, rect(-0.28, 0.06, 0.18, 0.20),
            rect(-0.92, -0.92, 0.92, -0.70)]


def g_flask():
    """An Erlenmeyer flask - chemical and hazmat."""
    return [[(-0.20, 0.88), (0.20, 0.88), (0.20, 0.30),
             (0.74, -0.78), (-0.74, -0.78), (-0.20, 0.30)],
            rect(-0.30, 0.82, 0.30, 0.94)]


def g_cross():
    """A thick medical cross - hospital, urgent care, nursing."""
    a, b = 0.28, 0.88
    return [[(-a, -b), (a, -b), (a, -a), (b, -a), (b, a), (a, a),
             (a, b), (-a, b), (-a, a), (-b, a), (-b, -a), (-a, -a)]]


def g_star_of_life():
    """Six bars at 60 degrees - EMS. Distinct from the cross's four."""
    return [bar(0, 0, 0.17, 0.92, math.radians(a)) for a in (0, 60, 120)]


def g_hydrant():
    """A hydrant - fire stations. A flame would say 'this is on fire'."""
    return [rect(-0.34, -0.62, 0.34, 0.46),
            rect(-0.62, -0.92, 0.62, -0.62),
            rect(-0.86, -0.10, 0.86, 0.18),
            [(-0.26, 0.46), (0.26, 0.46), (0.16, 0.78), (-0.16, 0.78)]]


def g_shield():
    """A shield - law enforcement."""
    return [[(-0.68, 0.84), (0.68, 0.84), (0.68, 0.06),
             (0.34, -0.60), (0.00, -0.92), (-0.34, -0.60), (-0.68, 0.06)]]


def g_bars():
    """Barred window - correctional. Reads as confinement, not a building."""
    out = [rect(-0.80, 0.62, 0.80, 0.84), rect(-0.80, -0.84, 0.80, -0.62)]
    for x in (-0.54, -0.18, 0.18, 0.54):
        out.append(rect(x - 0.11, -0.72, x + 0.11, 0.72))
    return out


def g_capitol():
    """Pediment on a block - government and EOC. Columns vanish at 24 px, so
    the triangle does the work."""
    return [[(-0.92, 0.18), (0.00, 0.80), (0.92, 0.18)],
            rect(-0.74, -0.70, 0.74, 0.10),
            rect(-0.92, -0.90, 0.92, -0.70)]


def g_mortarboard():
    """A graduation cap - schools."""
    return [[(0.00, 0.78), (0.96, 0.30), (0.00, -0.18), (-0.96, 0.30)],
            [(-0.56, 0.04), (0.56, 0.04), (0.56, -0.56), (0.00, -0.84),
             (-0.56, -0.56)]]


def g_shelter():
    """A roof over an open space - shelters."""
    return [[(-0.96, 0.16), (0.00, 0.86), (0.96, 0.16), (0.96, -0.02),
             (0.00, 0.62), (-0.96, -0.02)],
            rect(-0.68, -0.90, -0.44, 0.10), rect(0.44, -0.90, 0.68, 0.10)]


def g_plane():
    """A plan-view aircraft - airports."""
    return [[(0.00, 0.94), (0.14, 0.44), (0.92, -0.10), (0.92, -0.32),
             (0.14, -0.06), (0.14, -0.56), (0.40, -0.80), (0.40, -0.94),
             (0.00, -0.82), (-0.40, -0.94), (-0.40, -0.80), (-0.14, -0.56),
             (-0.14, -0.06), (-0.92, -0.32), (-0.92, -0.10), (-0.14, 0.44)]]


def g_helipad():
    """An H in a ring - heliports."""
    return [circle(0, 0, 0.96), hole(circle(0, 0, 0.74)),
            rect(-0.42, -0.48, -0.20, 0.48), rect(0.20, -0.48, 0.42, 0.48),
            rect(-0.30, -0.12, 0.30, 0.12)]


def g_anchor():
    """An anchor - ports and terminals."""
    return [rect(-0.11, -0.62, 0.11, 0.62), rect(-0.46, 0.38, 0.46, 0.58),
            circle(0, 0.78, 0.22),
            [(-0.82, -0.12), (-0.62, -0.20), (-0.34, -0.72), (0.00, -0.94),
             (0.34, -0.72), (0.62, -0.20), (0.82, -0.12), (0.74, -0.44),
             (0.36, -0.94), (-0.36, -0.94), (-0.74, -0.44)]]


def g_rail():
    """Track: two rails and the ties between them."""
    out = [rect(-0.52, -0.94, -0.28, 0.94), rect(0.28, -0.94, 0.52, 0.94)]
    for y in (-0.66, -0.22, 0.22, 0.66):
        out.append(rect(-0.86, y - 0.10, 0.86, y + 0.10))
    return out


def g_bridge():
    """A deck on an arch, with piers."""
    return [rect(-0.96, 0.34, 0.96, 0.56),
            arc_band(0, -0.62, 0.98, 0.74, math.radians(18), math.radians(162)),
            rect(-0.92, -0.86, -0.68, 0.40), rect(0.68, -0.86, 0.92, 0.40),
            rect(-0.96, -0.92, 0.96, -0.74)]


def g_pick():
    """Crossed pick and hammer - mines and quarries."""
    return [bar(0, 0, 0.11, 0.90, math.radians(38)),
            bar(0, 0, 0.11, 0.90, math.radians(-38)),
            bar(0.46, 0.60, 0.30, 0.16, math.radians(-38)),
            [(-0.72, 0.50), (-0.30, 0.86), (-0.44, 0.42)]]


def g_silo():
    """A tall domed cylinder - grain."""
    return [rect(-0.40, -0.80, 0.40, 0.48),
            [(-0.40, 0.48), (-0.26, 0.78), (0.26, 0.78), (0.40, 0.48)],
            rect(-0.66, -0.94, 0.66, -0.78)]


def g_tank():
    """A squat storage tank - fuel, gas, LNG. Wider than the silo on
    purpose: same family, different proportion, which is what the real
    things look like too."""
    return [rect(-0.88, -0.72, 0.88, 0.40),
            [(-0.88, 0.40), (-0.62, 0.72), (0.62, 0.72), (0.88, 0.40)],
            rect(-0.96, -0.86, 0.96, -0.70)]


def g_pump():
    """A fuel dispenser - fuel stations."""
    return [rect(-0.76, -0.90, 0.06, 0.80),
            hole(rect(-0.60, 0.22, -0.10, 0.62)),
            rect(0.06, 0.40, 0.44, 0.58),
            rect(0.44, -0.44, 0.62, 0.58),
            rect(0.30, -0.62, 0.76, -0.44)]


def g_water_tower():
    """A tank on legs - the one thing every small town has."""
    # The bowl has to be clearly narrower than the stance of the legs, or
    # the whole thing reads as an anvil sitting on a block.
    bowl = [(-0.52, 0.34), (-0.40, 0.84), (0.40, 0.84), (0.52, 0.34),
            (0.00, 0.06)]
    left = [(-0.34, 0.30), (-0.16, 0.24), (-0.52, -0.92), (-0.72, -0.92)]
    right = [(0.16, 0.24), (0.34, 0.30), (0.72, -0.92), (0.52, -0.92)]
    return [bowl, left, right, bar(0, -0.36, 0.46, 0.055)]


def g_clarifier():
    """A circular clarifier with its sweep arm - water and wastewater
    treatment. It is what these plants look like from the air."""
    return [circle(0, 0, 0.94), hole(circle(0, 0, 0.72)),
            bar(0, 0, 0.88, 0.085, math.radians(28)), circle(0, 0, 0.26)]


def g_well():
    """A derrick over a wellhead - wells and gas processing."""
    return [[(-0.66, -0.92), (-0.40, -0.92), (-0.14, 0.62), (0.14, 0.62),
             (0.40, -0.92), (0.66, -0.92), (0.30, 0.86), (-0.30, 0.86)],
            rect(-0.52, -0.34, 0.52, -0.20), rect(-0.86, -0.94, 0.86, -0.80)]


def g_server():
    """A rack - data centres and telecom exchanges."""
    out = [rect(-0.74, -0.92, 0.74, 0.92)]
    for y in (0.50, 0.06, -0.38):
        out.append(hole(rect(-0.54, y - 0.13, 0.54, y + 0.13)))
    return out


def g_burst():
    """A starburst - explosives storage."""
    pts = []
    for i in range(16):
        a = math.pi * 2 * i / 16
        r = 0.96 if i % 2 == 0 else 0.40
        pts.append((r * math.cos(a), r * math.sin(a)))
    return [pts]


def g_factory():
    """Sawtooth roof and a stack - generic industry, food, livestock, agri."""
    return [rect(0.36, -0.80, 0.72, 0.88),
            [(-0.92, -0.80), (-0.92, 0.10), (-0.52, 0.42), (-0.52, 0.10),
             (-0.12, 0.42), (-0.12, 0.10), (0.28, 0.42), (0.28, -0.80)],
            rect(-0.96, -0.94, 0.96, -0.78)]


GLYPHS = {
    "bolt": g_bolt, "trefoil": g_trefoil, "flame": g_flame,
    "droplet": g_droplet, "turbine": g_turbine, "sun": g_sun,
    "battery": g_battery, "broadcast": g_broadcast, "tower": g_tower,
    "repeater": g_repeater,
    # Infrastructure
    "dam": g_dam, "substation": g_substation, "pylon": g_pylon,
    "pipeline": g_pipeline, "refinery": g_refinery, "flask": g_flask,
    "cross": g_cross, "star_of_life": g_star_of_life, "hydrant": g_hydrant,
    "shield": g_shield, "bars": g_bars, "capitol": g_capitol,
    "mortarboard": g_mortarboard, "shelter": g_shelter, "plane": g_plane,
    "helipad": g_helipad, "anchor": g_anchor, "rail": g_rail,
    "bridge": g_bridge, "pick": g_pick, "silo": g_silo, "tank": g_tank,
    "pump": g_pump, "water_tower": g_water_tower, "clarifier": g_clarifier,
    "well": g_well, "server": g_server, "burst": g_burst,
    "factory": g_factory,
}


def glyph_names():
    return sorted(GLYPHS)


# --------------------------------------------------------------------------
# rasteriser
# --------------------------------------------------------------------------
def _coverage(subpaths, n):
    """Nonzero-winding scanline fill at n x n. Returns a row-major float grid.

    Nonzero rather than even-odd on purpose: the trefoil's centre disc sits on
    top of three wedges that already cover it, and even-odd would punch a hole
    through the middle of the symbol.
    """
    edges = []
    for sp in subpaths:
        m = len(sp)
        for i in range(m):
            x0, y0 = sp[i]
            x1, y1 = sp[(i + 1) % m]
            if y0 != y1:
                edges.append((x0, y0, x1, y1))
    grid = [[0.0] * n for _ in range(n)]
    for j in range(n):
        y = 1 - (j + 0.5) / n * 2
        xs = []
        for x0, y0, x1, y1 in edges:
            if (y0 <= y < y1) or (y1 <= y < y0):
                t = (y - y0) / (y1 - y0)
                xs.append((x0 + t * (x1 - x0), 1 if y1 > y0 else -1))
        if not xs:
            continue
        xs.sort()
        wind = 0
        row = grid[j]
        for k in range(len(xs) - 1):
            wind += xs[k][1]
            if wind != 0:
                a = int((xs[k][0] + 1) / 2 * n)
                b = int((xs[k + 1][0] + 1) / 2 * n)
                for i in range(max(a, 0), min(b, n)):
                    row[i] = 1.0
    return grid


def _dilate(grid, n, radius):
    """Grow a coverage grid outward by `radius` cells, via a separable max
    filter - a real Minkowski-sum dilation, so the result is GUARANTEED to
    cover every cell the input covers (each pass takes a max over a window
    that includes the cell itself).

    This replaced scaling a glyph's coordinates by a fixed factor from the
    origin to make its "grown" outline shape. That looks like the same idea
    but is not: growing from the origin only reliably contains the original
    shape when the shape is star-shaped about the origin, and most of these
    glyphs are not (a lattice tower's legs, an off-centre hole). Measured
    before this existed: 31 of the 39 glyphs had the "grown" shape fail to
    cover part of the actual fill, up to 3,534 contiguous supersampled pixels
    on helipad - a real hole punched in the icon, not antialiasing.
    """
    if radius <= 0:
        return [row[:] for row in grid]
    h = [[0.0] * n for _ in range(n)]
    for j in range(n):
        row = grid[j]
        for i in range(n):
            h[j][i] = max(row[max(0, i - radius):min(n, i + radius + 1)])
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        col = [h[j][i] for j in range(n)]
        for j in range(n):
            out[j][i] = max(col[max(0, j - radius):min(n, j + radius + 1)])
    return out


def _layers(name, n):
    """(fill, grown) coverage grids for one glyph at supersampled size n.

    The one place this is computed, so render() and sheet() can never drift
    apart on how a glyph's outline is grown - which is exactly how the
    hole-punching bug above went unnoticed in the first place: two copies of
    the same logic, one of them never looked at closely.
    """
    subs = normalize(GLYPHS[name]())
    fill = _coverage(subs, n)
    radius = max(1, round(OUTLINE_MARGIN * n / 2))
    grown = _dilate(fill, n, radius)
    return fill, grown


def _png(size, rgba_rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rgba_rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def render(name, rgb, size=SIZE, outline=OUTLINE):
    """One glyph as PNG bytes, with a dark outline so it survives a light map."""
    if name not in GLYPHS:
        # Never fall back to a circle. A symbol the author did not ask for is
        # an invented value, and it would ship looking deliberate.
        raise KeyError(f"unknown glyph {name!r}. Known: {', '.join(glyph_names())}")
    n = size * SS
    fill, grown = _layers(name, n)

    rows = []
    for j in range(size):
        row = bytearray()
        for i in range(size):
            f = o = 0.0
            for dj in range(SS):
                for di in range(SS):
                    f += fill[j * SS + dj][i * SS + di]
                    o += grown[j * SS + dj][i * SS + di]
            f /= SS * SS
            o /= SS * SS
            if o <= 0.002:
                row += bytes((0, 0, 0, 0))
                continue
            # Inside the shape use the fill colour; in the grown margin use the
            # outline colour. Alpha comes from the outer coverage so the edge
            # stays antialiased.
            r = int(rgb[0] * f + outline[0] * (1 - f))
            g = int(rgb[1] * f + outline[1] * (1 - f))
            b = int(rgb[2] * f + outline[2] * (1 - f))
            row += bytes((r, g, b, int(255 * min(1.0, o))))
        rows.append(row)
    return _png(size, rows)


def sheet(path, size=96):
    """Every glyph in a row, on a dark ground, to look at them."""
    names = glyph_names()
    # Decoding our own PNGs back would be silly; render straight to a canvas
    # instead - but via the SAME _layers() render() uses, so this preview
    # shows the glyph set exactly as it ships, normalization and outline
    # both, not a second, divergent copy of the rasterizing.
    bg = (26, 28, 32)
    w = size * len(names)
    rows = [bytearray() for _ in range(size)]
    for name in names:
        n = size * SS
        fill, grown = _layers(name, n)
        for j in range(size):
            for i in range(size):
                f = o = 0.0
                for dj in range(SS):
                    for di in range(SS):
                        f += fill[j * SS + dj][i * SS + di]
                        o += grown[j * SS + dj][i * SS + di]
                f /= SS * SS
                o /= SS * SS
                r = int(255 * f + OUTLINE[0] * (1 - f))
                g = int(209 * f + OUTLINE[1] * (1 - f))
                b = int(64 * f + OUTLINE[2] * (1 - f))
                a = min(1.0, o)
                rows[j] += bytes((int(r * a + bg[0] * (1 - a)),
                                  int(g * a + bg[1] * (1 - a)),
                                  int(b * a + bg[2] * (1 - a))))
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    open(path, "wb").write(png)
    return names, len(names)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render the glyph set to look at it.")
    ap.add_argument("--sheet", default="/tmp/glyphs.png")
    ap.add_argument("--size", type=int, default=96)
    a = ap.parse_args(argv)
    names, n = sheet(a.sheet, a.size)
    print(f"{n} glyph(s) -> {a.sheet}")
    print("  " + "  ".join(names))
    for nm in names:
        print(f"    {nm:10s} {len(render(nm, (255, 209, 64))):5d} bytes at {SIZE}px")
    return 0


if __name__ == "__main__":
    sys.exit(main())
