"""Tiny pure-Python PNG icon generator (no Pillow) so every KMZ ships its own
map symbols and ATAK renders them offline.

make_icon(shape, rgb, size) -> PNG bytes. Shapes: circle, square, diamond,
triangle, hexagon, ring, plus, star. Anti-aliased by 3x supersampling.
"""
import math
import struct
import zlib
from typing import Tuple

RGB = Tuple[int, int, int]


def _inside(shape: str, x: float, y: float) -> bool:
    # unit square [-1,1]^2; shape radius ~0.9
    r = 0.9
    ax, ay = abs(x), abs(y)
    if shape == "circle":
        return x * x + y * y <= r * r
    if shape == "ring":
        d = x * x + y * y
        return (r * 0.55) ** 2 <= d <= r * r
    if shape == "square":
        return ax <= r * 0.85 and ay <= r * 0.85
    if shape == "diamond":
        return ax + ay <= r
    if shape == "triangle":
        return -0.75 * r <= y <= r and ax <= (r - y) / 1.75
    if shape == "hexagon":
        return ay <= r * 0.866 and 0.866 * ax + 0.5 * ay <= r * 0.866
    if shape == "plus":
        return (ax <= r * 0.3 and ay <= r) or (ay <= r * 0.3 and ax <= r)
    if shape == "star":
        ang = math.atan2(y, x)
        d = math.hypot(x, y)
        k = 5
        m = (ang * k / (2 * math.pi)) % 1.0
        rr = r * (0.5 + 0.5 * (1 - abs(2 * m - 1)))
        return d <= rr
    return x * x + y * y <= r * r


def make_icon(shape: str, rgb: RGB, size: int = 32, outline: RGB = (20, 20, 20)) -> bytes:
    ss = 3
    px = []
    for j in range(size):
        row = bytearray()
        for i in range(size):
            hit = edge = 0
            for sj in range(ss):
                for si in range(ss):
                    x = ((i + (si + 0.5) / ss) / size) * 2 - 1
                    y = 1 - ((j + (sj + 0.5) / ss) / size) * 2
                    if _inside(shape, x, y):
                        # outline = inside but not inside the shrunken shape
                        if _inside(shape, x * 1.18, y * 1.18):
                            hit += 1
                        else:
                            edge += 1
            n = ss * ss
            a = (hit + edge) / n
            if a == 0:
                row += bytes((0, 0, 0, 0))
                continue
            fr = hit / max(hit + edge, 1)
            r = int(rgb[0] * fr + outline[0] * (1 - fr))
            g = int(rgb[1] * fr + outline[1] * (1 - fr))
            b = int(rgb[2] * fr + outline[2] * (1 - fr))
            row += bytes((r, g, b, int(255 * a)))
        px.append(bytes(row))
    raw = b"".join(b"\x00" + r for r in px)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def kml_color_to_rgb(aabbggrr: str) -> RGB:
    s = aabbggrr.lower().lstrip("#")
    if len(s) == 6:
        s = "ff" + s
    bb, gg, rr = s[2:4], s[4:6], s[6:8]
    return (int(rr, 16), int(gg, 16), int(bb, 16))
