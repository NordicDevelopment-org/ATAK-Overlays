"""Area of Interest (AOI): the thing every build is scoped to.

The tool scales by making the AOI a first-class object instead of assuming a
county. Every driver receives the AOI through the Context and decides how to
scope its query (bbox envelope, attribute filter, OSM area, ...).

    county:27025          one US county by FIPS (state 2 + county 3)
    state:MN              one US state (all sources, state-wide)
    region:upper-midwest  named preset = list of states (see regions.yaml)
    us                    whole United States
    country:CA            one country by ISO 3166-1 alpha-2 (OSM tier) -
                          CA is Canada here, the country code, not California
    bbox:W,S,E,N          arbitrary WGS84 envelope
    world                 no spatial limit (only global-tier sources apply;
                          Overpass drivers refuse this without tiling)

An AOI always carries a bbox (query envelope) and, when known, a polygon
(GeoJSON geometry in EPSG:4326) used to clip results to the true boundary.
Bboxes for states ship embedded so state builds can start before any network
call; the exact polygon is pulled from TIGER at build time when online.
"""
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

from . import fips as _fips

BBox = Tuple[float, float, float, float]  # west, south, east, north (WGS84)

# Approximate state envelopes (WGS84, generous). Used ONLY as a query envelope;
# clipping uses the real TIGER boundary once fetched. Values from the Census
# cartographic boundary files, rounded outward.
STATE_BBOX: Dict[str, BBox] = {
    "AL": (-88.48, 30.14, -84.89, 35.01), "AK": (-179.24, 51.18, -129.98, 71.44),  # western hemisphere only (Aleutians past the dateline dropped)
    "AZ": (-114.82, 31.33, -109.04, 37.01), "AR": (-94.62, 33.00, -89.64, 36.50),
    "CA": (-124.42, 32.53, -114.13, 42.01), "CO": (-109.06, 36.99, -102.04, 41.01),
    "CT": (-73.73, 40.98, -71.79, 42.06), "DE": (-75.79, 38.45, -75.05, 39.84),
    "DC": (-77.12, 38.79, -76.91, 38.996), "FL": (-87.64, 24.40, -80.03, 31.01),
    "GA": (-85.61, 30.36, -80.84, 35.01), "HI": (-160.25, 18.91, -154.81, 22.24),
    "ID": (-117.24, 41.99, -111.04, 49.00), "IL": (-91.51, 36.97, -87.02, 42.51),
    "IN": (-88.10, 37.77, -84.78, 41.76), "IA": (-96.64, 40.37, -90.14, 43.50),
    "KS": (-102.05, 36.99, -94.59, 40.00), "KY": (-89.57, 36.50, -81.96, 39.15),
    "LA": (-94.04, 28.93, -88.82, 33.02), "ME": (-71.08, 43.06, -66.95, 47.46),
    "MD": (-79.49, 37.91, -75.05, 39.72), "MA": (-73.51, 41.24, -69.93, 42.89),
    "MI": (-90.42, 41.70, -82.12, 48.31), "MN": (-97.24, 43.50, -89.49, 49.39),
    "MS": (-91.66, 30.17, -88.10, 35.00), "MO": (-95.77, 35.99, -89.10, 40.61),
    "MT": (-116.05, 44.36, -104.04, 49.00), "NE": (-104.05, 40.00, -95.31, 43.00),
    "NV": (-120.01, 35.00, -114.04, 42.00), "NH": (-72.56, 42.70, -70.60, 45.31),
    "NJ": (-75.56, 38.93, -73.89, 41.36), "NM": (-109.05, 31.33, -103.00, 37.00),
    "NY": (-79.76, 40.50, -71.86, 45.02), "NC": (-84.32, 33.84, -75.46, 36.59),
    "ND": (-104.05, 45.93, -96.55, 49.00), "OH": (-84.82, 38.40, -80.52, 41.98),
    "OK": (-103.00, 33.62, -94.43, 37.00), "OR": (-124.57, 41.99, -116.46, 46.29),
    "PA": (-80.52, 39.72, -74.69, 42.27), "RI": (-71.86, 41.15, -71.12, 42.02),
    "SC": (-83.35, 32.03, -78.54, 35.22), "SD": (-104.06, 42.48, -96.44, 45.95),
    "TN": (-90.31, 34.98, -81.65, 36.68), "TX": (-106.65, 25.84, -93.51, 36.50),
    "UT": (-114.05, 36.99, -109.04, 42.00), "VT": (-73.44, 42.73, -71.46, 45.02),
    "VA": (-83.68, 36.54, -75.24, 39.47), "WA": (-124.85, 45.54, -116.92, 49.00),
    "WV": (-82.65, 37.20, -77.72, 40.64), "WI": (-92.89, 42.49, -86.25, 47.31),
    "WY": (-111.06, 40.99, -104.05, 45.01), "PR": (-67.95, 17.88, -65.22, 18.52),
    "VI": (-65.09, 17.67, -64.56, 18.42), "GU": (144.62, 13.23, 144.96, 13.66),
    "AS": (-171.09, -14.55, -168.14, -11.05), "MP": (144.89, 14.11, 146.07, 20.56),
}
_US_ENV = [b for k, b in STATE_BBOX.items() if k not in ("GU", "MP")]   # Pacific territories east of the dateline get their own builds
US_BBOX: BBox = (min(b[0] for b in _US_ENV), min(b[1] for b in _US_ENV),
                 max(b[2] for b in _US_ENV), max(b[3] for b in _US_ENV))      # 50 states + DC + PR/VI/AS
CONUS_BBOX: BBox = (-124.85, 24.40, -66.95, 49.39)


@dataclass
class Aoi:
    kind: str                         # county | state | region | us | country | bbox | world
    id: str                           # "27025", "MN", "upper-midwest", "US", "CA", "w,s,e,n", "world"
    name: str                         # human label
    country: str = "US"               # ISO alpha-2 (US for county/state/region/us)
    bbox: Optional[BBox] = None       # query envelope
    geometry: Optional[dict] = None   # GeoJSON Polygon/MultiPolygon (clip boundary), if known
    state_abbrs: List[str] = field(default_factory=list)   # all states covered
    state_fp: Optional[str] = None    # for county/state kinds
    county_fp: Optional[str] = None   # for county kind
    county_name: Optional[str] = None

    # ---- identity helpers -------------------------------------------------
    @property
    def fips5(self) -> Optional[str]:
        return f"{self.state_fp}{self.county_fp}" if self.state_fp and self.county_fp else None

    @property
    def state_abbr(self) -> Optional[str]:
        return self.state_abbrs[0] if len(self.state_abbrs) == 1 else None

    @property
    def slug(self) -> str:
        """Filesystem-safe id: us/mn/27025_chisago, us/mn, us/region_upper-midwest, ca, bbox_..."""
        if self.kind == "county":
            st = (self.state_abbr or "xx").lower()
            nm = re.sub(r"[^a-z0-9]+", "_", (self.county_name or "").lower()).strip("_")
            return f"us/{st}/{self.fips5}_{nm}" if nm else f"us/{st}/{self.fips5}"
        if self.kind == "state":
            return f"us/{self.id.lower()}"
        if self.kind == "region":
            return f"us/region_{self.id.lower()}"
        if self.kind == "us":
            return "us" if self.id == "US" else self.id.lower()      # us vs conus must not collide
        if self.kind == "country":
            return self.id.lower()
        if self.kind == "bbox":
            w, s, e, n = self.bbox
            return f"bbox_{w:.3f}_{s:.3f}_{e:.3f}_{n:.3f}".replace("-", "m").replace(".", "p")
        return "world"

    def template_vars(self) -> Dict[str, str]:
        """Substitution vars for catalog `where`/`url` templates."""
        st = self.state_abbr or ""
        return {
            "fips5": self.fips5 or "",
            "state_fp": self.state_fp or "",
            "county_fp": self.county_fp or "",
            "county_name": self.county_name or "",
            "state_abbr": st,
            "state_name": _fips.STATES.get(st, ("", ""))[1] if st else "",
            "states_sql": ",".join(f"'{s}'" for s in self.state_abbrs),
            "country": self.country,
            "aoi_id": self.id,
        }

    def describe(self) -> str:
        if self.kind == "county":
            return f"{self.county_name} County, {self.state_abbr} (FIPS {self.fips5})"
        if self.kind == "state":
            return f"{self.name} (state {self.id})"
        if self.kind == "region":
            return f"region {self.id}: {', '.join(self.state_abbrs)}"
        if self.kind == "country":
            return f"country {self.id}"
        if self.kind == "bbox":
            return f"bbox {self.bbox}"
        return self.name


# ---- parsing ----------------------------------------------------------------
def _regions_file() -> str:
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(here, "catalog", "regions.yaml")


def load_regions(path: Optional[str] = None) -> Dict[str, List[str]]:
    p = path or _regions_file()
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    return {k: [s.upper() for s in v] for k, v in (doc.get("regions") or {}).items()}


def union_bbox(boxes: List[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def parse_aoi(spec: str, cache_dir: str = ".cache", county_name: Optional[str] = None,
              regions_path: Optional[str] = None) -> Aoi:
    """Parse an --aoi string. Network is touched only for county:FIPS name lookup
    (and only when a county name isn't supplied)."""
    s = spec.strip()
    low = s.lower()
    if low in ("us", "usa", "united-states"):
        return Aoi("us", "US", "United States", bbox=US_BBOX,
                   state_abbrs=sorted(k for k in STATE_BBOX))
    if low == "conus":
        return Aoi("us", "CONUS", "Contiguous United States", bbox=CONUS_BBOX,
                   state_abbrs=sorted(k for k in STATE_BBOX if k not in ("AK", "HI", "PR", "VI", "GU", "AS", "MP")))
    if low == "world":
        return Aoi("world", "world", "World", country="", bbox=(-180.0, -90.0, 180.0, 90.0))
    kind, _, val = s.partition(":")
    kind = kind.lower()
    if kind == "county":
        v = val.strip()
        if not re.fullmatch(r"\d{5}", v):
            raise ValueError("county AOI needs a 5-digit FIPS, e.g. county:27025")
        sfp, cfp, cname, abbr = _fips.resolve("", county_name, v, cache_dir=cache_dir)
        return Aoi("county", v, f"{cname} County, {abbr}", bbox=STATE_BBOX.get(abbr),
                   state_abbrs=[abbr], state_fp=sfp, county_fp=cfp, county_name=cname)
    if kind == "state":
        sfp = _fips.state_fp(val)
        abbr = _fips.abbr_for_fp(sfp)
        return Aoi("state", abbr, _fips.STATES[abbr][1], bbox=STATE_BBOX.get(abbr),
                   state_abbrs=[abbr], state_fp=sfp)
    if kind == "region":
        regions = load_regions(regions_path)
        key = val.strip().lower()
        if key not in regions:
            raise KeyError(f"unknown region '{key}'. known: {sorted(regions)}")
        sts = regions[key]
        return Aoi("region", key, f"Region {key}", bbox=union_bbox([STATE_BBOX[a] for a in sts]),
                   state_abbrs=sts)
    if kind == "country":
        cc = val.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", cc):
            raise ValueError(
                "country AOI needs ISO alpha-2, e.g. country:CA for Canada "
                "(the country code, not the California state abbreviation)")
        if cc == "US":
            return parse_aoi("us")
        return Aoi("country", cc, f"Country {cc}", country=cc, bbox=None)
    if kind == "bbox":
        parts = [float(x) for x in val.split(",")]
        if len(parts) != 4:
            raise ValueError("bbox AOI is bbox:W,S,E,N in WGS84")
        w, so, e, n = parts
        if not (w < e and so < n):
            raise ValueError("bbox must satisfy W<E and S<N")
        # infer US states the box touches so national + state tiers apply
        touched = [ab for ab, (bw, bs, be, bn) in STATE_BBOX.items()
                   if not (e < bw or w > be or n < bs or so > bn)]
        return Aoi("bbox", val, f"bbox {val}", country="US" if touched else "",
                   bbox=(w, so, e, n), state_abbrs=sorted(touched))
    if re.fullmatch(r"\d{5}", s):          # bare FIPS
        return parse_aoi(f"county:{s}", cache_dir, county_name, regions_path)
    if s.upper() in STATE_BBOX:           # bare state abbr
        return parse_aoi(f"state:{s}", cache_dir, county_name, regions_path)
    raise ValueError(f"cannot parse AOI '{spec}'. Use county:FIPS, state:XX, region:NAME, "
                     "us, country:XX, bbox:W,S,E,N, or world")


# ---- geometry helpers (pure python; no shapely) ----------------------------
def bbox_of_geometry(geom: dict) -> Optional[BBox]:
    xs, ys = [], []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for x in c:
                walk(x)
    if geom and geom.get("coordinates") is not None:
        walk(geom["coordinates"])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _pip(x: float, y: float, ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def point_in_geometry(x: float, y: float, geom: dict) -> bool:
    """Point-in-polygon for GeoJSON Polygon/MultiPolygon (holes respected)."""
    if not geom:
        return True
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else \
            geom["coordinates"] if geom["type"] == "MultiPolygon" else []
    for rings in polys:
        if not rings:
            continue
        if _pip(x, y, rings[0]) and not any(_pip(x, y, h) for h in rings[1:]):
            return True
    return False


def _sample_points(geom: dict):
    t = geom.get("type")
    c = geom.get("coordinates")
    if t == "Point":
        yield c
    elif t in ("MultiPoint", "LineString"):
        for p in c:
            yield p
    elif t == "MultiLineString":
        for line in c:
            for p in line:
                yield p
    elif t == "Polygon":
        for p in c[0]:
            yield p
    elif t == "MultiPolygon":
        for poly in c:
            for p in poly[0]:
                yield p


def _segments(geom: dict):
    t = geom.get("type")
    c = geom.get("coordinates")
    if t == "LineString":
        yield from zip(c, c[1:])
    elif t == "MultiLineString":
        for line in c:
            yield from zip(line, line[1:])
    elif t == "Polygon":
        for ring in c:
            yield from zip(ring, ring[1:])
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                yield from zip(ring, ring[1:])


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(p1, p2, q1, q2) -> bool:
    """Proper or touching intersection of segments p1p2 and q1q2."""
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0:
        return True

    def on_seg(a, b, c):
        return (min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1]))
    return ((d1 == 0 and on_seg(q1, q2, p1)) or (d2 == 0 and on_seg(q1, q2, p2)) or
            (d3 == 0 and on_seg(p1, p2, q1)) or (d4 == 0 and on_seg(p1, p2, q2)))


class BoundaryIndex:
    """Grid index over the edges of a boundary Polygon/MultiPolygon so line and
    polygon features that CROSS the AOI without a vertex inside are still kept."""

    def __init__(self, boundary: dict, cell: Optional[float] = None):
        self.boundary = boundary
        bb = bbox_of_geometry(boundary) or (0, 0, 1, 1)
        self.bbox = bb
        self.cell = cell or max(0.02, max(bb[2] - bb[0], bb[3] - bb[1]) / 200.0)
        self.grid: Dict[Tuple[int, int], List[Tuple[list, list]]] = {}
        self.first_vertex = None
        for a, b in _segments(boundary):
            if self.first_vertex is None:
                self.first_vertex = a
            for key in self._cells(a, b):
                self.grid.setdefault(key, []).append((a, b))

    def _cells(self, a, b):
        c = self.cell
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        for i in range(int(x0 // c), int(x1 // c) + 1):
            for j in range(int(y0 // c), int(y1 // c) + 1):
                yield (i, j)

    def segment_crosses(self, a, b) -> bool:
        seen = set()
        for key in self._cells(a, b):
            for edge in self.grid.get(key, ()):
                eid = id(edge)
                if eid in seen:
                    continue
                seen.add(eid)
                if segments_intersect(a, b, edge[0], edge[1]):
                    return True
        return False

    def crosses(self, geom: dict) -> bool:
        return any(self.segment_crosses(a, b) for a, b in _segments(geom))


def geometry_touches(geom: dict, boundary: dict, bbox: Optional[BBox] = None,
                     index: Optional["BoundaryIndex"] = None) -> bool:
    """Intersects test against the AOI: any vertex inside the boundary, OR any
    segment crossing a boundary edge (needs `index`), OR the feature polygon
    containing the boundary. Points use exact point-in-polygon. bbox short-circuits."""
    if not geom:
        return False
    if bbox:
        gb = bbox_of_geometry(geom)
        if gb and (gb[2] < bbox[0] or gb[0] > bbox[2] or gb[3] < bbox[1] or gb[1] > bbox[3]):
            return False
    if not boundary:
        return True
    for p in _sample_points(geom):
        if point_in_geometry(p[0], p[1], boundary):
            return True
    if geom.get("type") == "Point":
        return False
    if index is not None:
        if index.crosses(geom):
            return True
        if geom.get("type") in ("Polygon", "MultiPolygon") and index.first_vertex is not None:
            fv = index.first_vertex
            if point_in_geometry(fv[0], fv[1], geom):
                return True
    return False


def representative_point(geom: dict):
    """Centroid-ish point for a geometry (vertex average; good enough for icons)."""
    if not geom:
        return None
    if geom["type"] == "Point":
        return list(geom["coordinates"])
    pts = list(_sample_points(geom))
    if not pts:
        return None
    return [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)]


def tile_bbox(bbox: BBox, tile_deg: float) -> List[BBox]:
    """Split a bbox into <= tile_deg x tile_deg tiles (for Overpass etc.)."""
    w, s, e, n = bbox
    if (e - w) <= tile_deg and (n - s) <= tile_deg:
        return [bbox]
    tiles = []
    y = s
    while y < n:
        y2 = min(y + tile_deg, n)
        x = w
        while x < e:
            x2 = min(x + tile_deg, e)
            tiles.append((x, y, x2, y2))
            x = x2
        y = y2
    return tiles
