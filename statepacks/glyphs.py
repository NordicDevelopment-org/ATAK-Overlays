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


# --------------------------------------------------------------------------
# geometry helpers - everything is a list of closed rings in [-1, 1]
# --------------------------------------------------------------------------
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


GLYPHS = {
    "bolt": g_bolt, "trefoil": g_trefoil, "flame": g_flame,
    "droplet": g_droplet, "turbine": g_turbine, "sun": g_sun,
    "battery": g_battery, "broadcast": g_broadcast, "tower": g_tower,
    "repeater": g_repeater,
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
    subs = GLYPHS[name]()
    n = size * SS
    fill = _coverage(subs, n)
    # The outline is the same shape grown slightly, drawn underneath.
    grown = _coverage([[(x * 1.14, y * 1.14) for x, y in sp] for sp in subs], n)

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
    tiles = [render(n, (255, 209, 64), size) for n in names]
    # Decoding our own PNGs back would be silly; re-render straight to a canvas.
    bg = (26, 28, 32)
    w = size * len(names)
    rows = [bytearray() for _ in range(size)]
    for name in names:
        subs = GLYPHS[name]()
        n = size * SS
        fill = _coverage(subs, n)
        grown = _coverage([[(x * 1.14, y * 1.14) for x, y in sp] for sp in subs], n)
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
    return names, len(tiles)


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
