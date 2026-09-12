"""Features -> ATAK-ready KMZ with nested <Folder> eye-toggles, sector styling,
embedded PNG icons, unit-labelled popups, and ExtendedData.

One KMZ per layer (or one combined pack): Document > [Sector] > Layer >
group_by buckets > Placemarks. Dense layers import hidden. Every Document
carries provenance (source, license, retrieval date) in its description.

Style rules from the catalog (evaluated on canonical fields) let one layer
render by class, e.g. transmission lines by voltage:
    style_rules:
      - {when: "voltage_kv >= 345", color: ff0000ff, width: 4}
      - {when: "voltage_kv >= 200", color: ff00a5ff, width: 3}
"""
import re
import zipfile
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from ..icons import kml_color_to_rgb, make_icon
from ..model import Feature, LayerResult
from ..normalize import CANONICAL, feature_name, fmt_value, headline

# KML colors are aabbggrr.
# layer_key: (line/icon color, fill color, width, icon shape, hidden_by_default, sector)
LAYER_STYLE: Dict[str, Tuple[str, str, int, str, bool, str]] = {
    # --- energy: electric
    "power_plants":        ("ff00d7ff", "4000d7ff", 2, "star",     False, "Energy - Electric"),
    "generators":          ("ff00d7ff", "3000d7ff", 1, "circle",   True,  "Energy - Electric"),
    "battery_storage":     ("ff00ffbf", "4000ffbf", 2, "square",   False, "Energy - Electric"),
    "substations":         ("ff0080ff", "400080ff", 2, "square",   False, "Energy - Electric"),
    "transmission_lines":  ("ff0055ff", "00000000", 2, "circle",   False, "Energy - Electric"),
    "power_towers":        ("ff0055ff", "00000000", 1, "plus",     True,  "Energy - Electric"),
    "service_territories": ("ff88ffff", "1088ffff", 1, "circle",   True,  "Energy - Electric"),
    "rto_regions":         ("ff88ffff", "1088ffff", 2, "circle",   True,  "Energy - Electric"),
    "control_areas":       ("ff88ffff", "1088ffff", 2, "circle",   True,  "Energy - Electric"),
    "nuclear_reactors":    ("ff00ffff", "4000ffff", 2, "star",     False, "Energy - Electric"),
    # --- energy: oil/gas/fuel
    "pipelines":           ("ffff00aa", "00000000", 2, "circle",   False, "Energy - Oil & Gas"),
    "compressor_stations": ("ffff00aa", "40ff00aa", 2, "hexagon",  False, "Energy - Oil & Gas"),
    "gas_processing":      ("ffff00aa", "40ff00aa", 2, "hexagon",  False, "Energy - Oil & Gas"),
    "gas_storage":         ("ffff00aa", "40ff00aa", 2, "ring",     False, "Energy - Oil & Gas"),
    "lng_terminals":       ("ffff00aa", "40ff00aa", 2, "diamond",  False, "Energy - Oil & Gas"),
    "refineries":          ("ff8000ff", "408000ff", 2, "hexagon",  False, "Energy - Oil & Gas"),
    "fuel_terminals":      ("ff8000ff", "408000ff", 2, "diamond",  False, "Energy - Oil & Gas"),
    "ethanol_plants":      ("ff00ff80", "4000ff80", 2, "hexagon",  False, "Energy - Oil & Gas"),
    "fuel_stations":       ("ff00aaff", "2000aaff", 1, "circle",   True,  "Energy - Oil & Gas"),
    "fuel_storage":        ("ff8000ff", "308000ff", 1, "ring",     True,  "Energy - Oil & Gas"),
    "biodiesel_plants":    ("ff00ff80", "4000ff80", 2, "hexagon",  True,  "Energy - Oil & Gas"),
    "rail_terminals":      ("ff8000ff", "408000ff", 1, "diamond",  True,  "Energy - Oil & Gas"),
    # --- water
    "dams":                ("ffff8800", "40ff8800", 2, "triangle", False, "Water"),
    "levees":              ("ffff8800", "00000000", 2, "circle",   False, "Water"),
    "leveed_areas":        ("ffff8800", "20ff8800", 1, "circle",   True,  "Water"),
    "water_service_areas": ("ffffaa00", "15ffaa00", 1, "circle",   True,  "Water"),
    "water_treatment":     ("ffffaa00", "40ffaa00", 2, "circle",   False, "Water"),
    "wastewater_treatment": ("ff997a00", "40997a00", 2, "circle",  False, "Water"),
    "water_towers":        ("ffffaa00", "40ffaa00", 1, "ring",     False, "Water"),
    "water_wells":         ("ffffaa00", "20ffaa00", 1, "circle",   True,  "Water"),
    "reservoirs":          ("ffffaa00", "30ffaa00", 1, "circle",   True,  "Water"),
    "stream_gauges":       ("ffffaa00", "00000000", 1, "diamond",  True,  "Water"),
    # --- communications
    "comm_towers":         ("ffff00ff", "40ff00ff", 1, "triangle", False, "Communications"),
    "broadcast_towers":    ("ffff00ff", "40ff00ff", 1, "triangle", True,  "Communications"),
    "data_centers":        ("ffff55ff", "40ff55ff", 2, "square",   False, "Communications"),
    "telecom_exchanges":   ("ffff55ff", "40ff55ff", 1, "square",   True,  "Communications"),
    "psap":                ("ffff55ff", "40ff55ff", 2, "plus",     False, "Communications"),
    # --- emergency & health
    "hospitals":           ("ff0000ff", "400000ff", 2, "plus",     False, "Emergency & Health"),
    "urgent_care":         ("ff4040ff", "404040ff", 1, "plus",     True,  "Emergency & Health"),
    "fire_stations":       ("ff0040ff", "400040ff", 2, "square",   False, "Emergency & Health"),
    "police":              ("ffff8000", "40ff8000", 2, "square",   False, "Emergency & Health"),
    "ems":                 ("ff0000ff", "400000ff", 2, "diamond",  False, "Emergency & Health"),
    "eoc":                 ("ff00ffff", "4000ffff", 2, "star",     False, "Emergency & Health"),
    "shelters":            ("ff00ff00", "4000ff00", 1, "square",   True,  "Emergency & Health"),
    "nursing_homes":       ("ff4040ff", "204040ff", 1, "circle",   True,  "Emergency & Health"),
    "pharmacies":          ("ff4040ff", "204040ff", 1, "circle",   True,  "Emergency & Health"),
    "correctional":        ("ff808080", "40808080", 2, "square",   True,  "Government"),
    "government":          ("ff808080", "40808080", 1, "square",   True,  "Government"),
    "schools":             ("ff00ff00", "2000ff00", 1, "circle",   True,  "Government"),
    # --- transport
    "airports":            ("ffffffff", "20ffffff", 2, "diamond",  False, "Transportation"),
    "heliports":           ("ffffffff", "20ffffff", 1, "plus",     True,  "Transportation"),
    "railways":            ("ff404040", "00000000", 2, "circle",   False, "Transportation"),
    "rail_facilities":     ("ff404040", "40404040", 1, "square",   True,  "Transportation"),
    "bridges":             ("ffc0c0c0", "40c0c0c0", 1, "diamond",  True,  "Transportation"),
    "rail_crossings":      ("ff404040", "40404040", 1, "plus",     True,  "Transportation"),
    "ports":               ("ffff8040", "40ff8040", 2, "square",   False, "Transportation"),
    "industrial":          ("ff606060", "30606060", 1, "circle",   True,  "Industrial"),
    # --- chemical & hazmat
    "chemical_plants":     ("ff00a5ff", "4000a5ff", 2, "hexagon",  False, "Chemical & Hazmat"),
    "hazmat_storage":      ("ff0045ff", "400045ff", 2, "ring",     False, "Chemical & Hazmat"),
    "explosives_storage":  ("ff0000ff", "400000ff", 2, "diamond",  True,  "Chemical & Hazmat"),
    # --- agriculture & food
    "grain_storage":       ("ff40d0d0", "4040d0d0", 1, "square",   False, "Agriculture & Food"),
    "food_processing":     ("ff40c0a0", "4040c0a0", 2, "hexagon",  False, "Agriculture & Food"),
    "agri_facilities":     ("ff40c0a0", "2040c0a0", 1, "circle",   True,  "Agriculture & Food"),
    "livestock_operations": ("ff40c0a0", "2040c0a0", 1, "circle",  True,  "Agriculture & Food"),
    # --- mining
    "mines":               ("ff8b8b8b", "408b8b8b", 2, "triangle", False, "Mining"),
    # --- base map
    "parcels":             ("ff00ffff", "1a00ffff", 1, "circle",   True,  "Base"),
    "building_footprints": ("ff0000ff", "330000ff", 1, "circle",   True,  "Base"),
    "address_points":      ("ff00ff00", "00000000", 1, "circle",   True,  "Base"),
    "roads":               ("ffffaa00", "00000000", 2, "circle",   False, "Base"),
    "city_boundaries":     ("ffffffff", "00000000", 2, "circle",   False, "Base"),
    "county_boundary":     ("ff0055ff", "00000000", 3, "circle",   False, "Base"),
    "state_boundary":      ("ff0055ff", "00000000", 3, "circle",   False, "Base"),
}
DEFAULT_STYLE = ("ffffffff", "20ffffff", 1, "circle", False, "Other")
MAX_GROUPS = 80
MAX_EXTENDED = 80


def style_for(layer_key: str, spec: Optional[dict] = None):
    base = LAYER_STYLE.get(layer_key, DEFAULT_STYLE)
    color, fill, width, icon, hidden, sector = base
    spec = spec or {}
    st = spec.get("style") or {}
    return (st.get("color", color), st.get("fill", fill), int(st.get("width", width)),
            st.get("icon", icon), bool(spec.get("hidden", hidden)), spec.get("sector", sector))


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xy(c):
    """A usable (x, y) pair, or None. Sources ship nulls, strings and short tuples."""
    if not c or len(c) < 2:
        return None
    try:
        x, y = float(c[0]), float(c[1])
    except (TypeError, ValueError):
        return None
    if x != x or y != y or x in (float("inf"), float("-inf")) or y in (float("inf"), float("-inf")):
        return None
    return x, y


def _coords(seq, prec):
    pts = [p for p in (_xy(c) for c in seq or ()) if p]
    return " ".join(f"{x:.{prec}f},{y:.{prec}f},0" for x, y in pts)


def _poly(rings, prec):
    if not rings:
        return ""
    outer = _coords(rings[0], prec)
    if not outer:
        return ""
    out = ["<Polygon><tessellate>1</tessellate>",
           f"<outerBoundaryIs><LinearRing><coordinates>{outer}"
           "</coordinates></LinearRing></outerBoundaryIs>"]
    for inner in rings[1:]:
        ring = _coords(inner, prec)
        if ring:
            out.append(f"<innerBoundaryIs><LinearRing><coordinates>{ring}"
                       "</coordinates></LinearRing></innerBoundaryIs>")
    out.append("</Polygon>")
    return "".join(out)


def _geom(g, prec=6):
    if not g:
        return ""
    t = g.get("type")
    if t == "GeometryCollection":
        return "<MultiGeometry>" + "".join(_geom(x, prec) for x in g.get("geometries", [])) + "</MultiGeometry>"
    c = g.get("coordinates")
    if c is None:
        return ""
    if t == "Point":
        p = _xy(c)
        return f"<Point><coordinates>{p[0]:.{prec}f},{p[1]:.{prec}f},0</coordinates></Point>" if p else ""
    if t == "MultiPoint":
        pts = [p for p in (_xy(q) for q in c) if p]
        return ("<MultiGeometry>" + "".join(
            f"<Point><coordinates>{x:.{prec}f},{y:.{prec}f},0</coordinates></Point>" for x, y in pts)
            + "</MultiGeometry>") if pts else ""
    if t == "LineString":
        line = _coords(c, prec)
        return (f"<LineString><tessellate>1</tessellate><coordinates>{line}"
                "</coordinates></LineString>") if line.count(",") >= 3 else ""
    if t == "MultiLineString":
        parts = [_coords(l, prec) for l in c]
        parts = [p for p in parts if p.count(",") >= 3]
        return ("<MultiGeometry>" + "".join(
            f"<LineString><tessellate>1</tessellate><coordinates>{p}</coordinates></LineString>"
            for p in parts) + "</MultiGeometry>") if parts else ""
    if t == "Polygon":
        return _poly(c, prec)
    if t == "MultiPolygon":
        parts = [_poly(p, prec) for p in c]
        parts = [p for p in parts if p]
        return ("<MultiGeometry>" + "".join(parts) + "</MultiGeometry>") if parts else ""
    return ""


# ---- style rules ----------------------------------------------------------
_COND_RX = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_:]*)\s*(>=|<=|==|!=|=|>|<|~)\s*(.+?)\s*$")


def _eval_cond(props: dict, cond: str) -> bool:
    for part in re.split(r"\s+and\s+", cond, flags=re.I):
        m = _COND_RX.match(part)
        if not m:
            return False
        k, op, v = m.groups()
        x = props.get(k)
        if x is None:
            return False
        v = v.strip("'\"")
        if op == "~":
            if not re.search(v, str(x), re.I):
                return False
            continue
        try:
            xf, vf = float(x), float(v)
            ok = {">=": xf >= vf, "<=": xf <= vf, ">": xf > vf, "<": xf < vf,
                  "==": xf == vf, "=": xf == vf, "!=": xf != vf}[op]
        except (TypeError, ValueError):
            xs, vs = str(x).lower(), v.lower()
            ok = {"==": xs == vs, "=": xs == vs, "!=": xs != vs}.get(op, False)
        if not ok:
            return False
    return True


def _rules(spec: Optional[dict]):
    return list((spec or {}).get("style_rules") or [])


def _style_id_for(style_key: str, props: dict, rules) -> str:
    for i, r in enumerate(rules):
        if _eval_cond(props, r.get("when", "")):
            return f"{style_key}_r{i}"
    return style_key


def _style_block(style_id: str, color: str, fill: str, width: int, icon: str, label_scale: float = 0.8) -> str:
    return (f'<Style id="{style_id}">'
            f'<LineStyle><color>{color}</color><width>{width}</width></LineStyle>'
            f'<PolyStyle><color>{fill}</color></PolyStyle>'
            f'<IconStyle><scale>1.0</scale><Icon><href>icons/{style_id}.png</href></Icon></IconStyle>'
            f'<LabelStyle><scale>{label_scale}</scale></LabelStyle>'
            f'<BalloonStyle><text>$[description]</text></BalloonStyle>'
            '</Style>')


def styles_and_icons(layer_key: str, spec: Optional[dict],
                     style_key: Optional[str] = None) -> Tuple[str, Dict[str, bytes]]:
    """style_key defaults to the layer key; a combined pack passes the document
    key instead so two sources of the same layer keep their own style rules."""
    style_key = style_key or layer_key
    color, fill, width, icon, _, _ = style_for(layer_key, spec)
    blocks = [_style_block(style_key, color, fill, width, icon)]
    icons = {f"icons/{style_key}.png": make_icon(icon, kml_color_to_rgb(color))}
    for i, r in enumerate(_rules(spec)):
        sid = f"{style_key}_r{i}"
        c, f, w, ic = r.get("color", color), r.get("fill", fill), int(r.get("width", width)), r.get("icon", icon)
        blocks.append(_style_block(sid, c, f, w, ic))
        icons[f"icons/{sid}.png"] = make_icon(ic, kml_color_to_rgb(c))
    return "".join(blocks), icons


# ---- placemarks -----------------------------------------------------------
def _description(props: dict, layer_key: str, prov=None) -> str:
    head = headline(props, layer_key)
    parts = []
    if head:
        parts.append("<table>" + "".join(f"<tr><td><b>{_esc(k)}</b></td><td>{_esc(v)}</td></tr>" for k, v in head) + "</table>")
    raw = [(k, v) for k, v in props.items()
           if v not in (None, "") and not k.startswith("_") and k not in CANONICAL]
    if raw:
        parts.append(f"<hr/><b>Source attributes ({len(raw)})</b><table>" +
                     "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in raw) + "</table>")
    if prov is not None:
        parts.append(f"<hr/><small>{_esc(prov.source_name)} - retrieved {_esc(prov.retrieved)} - {_esc(prov.license)}</small>")
    return "<![CDATA[" + "".join(parts) + "]]>" if parts else ""


def _extended(props: dict) -> str:
    rows = []
    for k, v in props.items():
        if v in (None, "") or k.startswith("_"):
            continue
        if k in CANONICAL:
            rows.append(f'<Data name="{_esc(k)}"><displayName>{_esc(CANONICAL[k][0])}</displayName>'
                        f'<value>{_esc(fmt_value(k, v))}</value></Data>')
    for k, v in props.items():
        if len(rows) >= MAX_EXTENDED:
            break
        if v in (None, "") or k.startswith("_") or k in CANONICAL:
            continue
        rows.append(f'<Data name="{_esc(k)}"><value>{_esc(v)}</value></Data>')
    return f"<ExtendedData>{''.join(rows)}</ExtendedData>" if rows else ""


def _placemark(ft: Feature, layer_key: str, spec: Optional[dict], rules, prec: int, prov=None,
               style_key: Optional[str] = None) -> str:
    g = _geom(ft.geometry, prec)
    if not g:
        return ""
    props = ft.properties or {}
    sid = _style_id_for(style_key or layer_key, props, rules)
    name = feature_name(props, spec, layer_key)
    # KML 2.2 element sequence: name, visibility, description, styleUrl, ExtendedData, geometry
    return (f"<Placemark><name>{_esc(name)}</name>"
            f"<description>{_description(props, layer_key, prov)}</description>"
            f"<styleUrl>#{sid}</styleUrl>{_extended(props)}{g}</Placemark>")


def _prop(props, field):
    if field in props:
        return props[field]
    for k in props:
        if k.upper() == field.upper():
            return props[k]
    return None


def _bucketize(features: List[Feature], group_by: Optional[List[str]]):
    """Return (field_used_or_None, OrderedDict label -> [Feature])."""
    if not group_by:
        return None, OrderedDict([(None, features)])
    for field in group_by:
        vals = [_prop(f.properties or {}, field) for f in features]
        if all(v in (None, "") for v in vals):
            continue
        distinct = {("(blank)" if v in (None, "") else str(v)) for v in vals}
        if len(distinct) > MAX_GROUPS:
            continue
        buckets = OrderedDict()
        order = {}
        for f, v in zip(features, vals):
            if v in (None, ""):
                lbl, key = "(blank)", (2, "")
            elif field in CANONICAL:
                lbl = fmt_value(field, v)
                key = (0, -float(v)) if isinstance(v, (int, float)) else (1, lbl.lower())
            else:
                lbl = str(v)
                try:
                    key = (0, -float(v))     # numeric labels descending
                except (TypeError, ValueError):
                    key = (1, lbl.lower())
            buckets.setdefault(lbl, []).append(f)
            order[lbl] = key
        return field, OrderedDict(sorted(buckets.items(), key=lambda kv: order[kv[0]]))
    return None, OrderedDict([(None, features)])


def _folder(label, features, layer_key, spec, rules, visible, prec, prov=None, style_key=None):
    inner = "".join(_placemark(f, layer_key, spec, rules, prec, prov, style_key) for f in features)
    if not inner:
        return ""
    vis = "" if visible else "<visibility>0</visibility>"
    cnt = inner.count("<Placemark>")
    if label is None:
        return inner
    # KML 2.2 sequence: name, visibility, open, then features
    return f"<Folder><name>{_esc(label)} ({cnt})</name>{vis}<open>0</open>{inner}</Folder>"


def _prov_cdata(p) -> str:
    return (f"<![CDATA[source: {_esc(p.source_name)}<br/>url: {_esc(p.source_url)}<br/>"
            f"license: {_esc(p.license)}<br/>retrieved: {_esc(p.retrieved)}"
            f"{('<br/>note: ' + _esc(p.notes)) if p.notes else ''}]]>")


def layer_kml(result: LayerResult, spec: Optional[dict] = None, precision: int = 6,
              title: Optional[str] = None) -> Tuple[str, Dict[str, bytes]]:
    """Full KML Document for one logical layer. Returns (kml, icons)."""
    lk = result.logical
    _, _, _, _, hidden, sector = style_for(lk, spec)
    visible = not hidden
    rules = _rules(spec)
    styles, icons = styles_and_icons(lk, spec)
    _, buckets = _bucketize(result.features, result.group_by)
    body = "".join(_folder(lbl, fs, lk, spec, rules, visible, precision, result.provenance)
                   for lbl, fs in buckets.items())
    doc_vis = "" if visible else "<visibility>0</visibility>"
    name = title or (spec or {}).get("title") or lk
    # KML 2.2 sequence: name, visibility, open, description, styles, features
    kml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
           f"<name>{_esc(name)}</name>{doc_vis}<open>0</open>"
           f"<description>{_prov_cdata(result.provenance)}</description>"
           f"{styles}{body}</Document></kml>")
    return kml, icons


def combined_kml(results: List[LayerResult], specs: Optional[Dict[str, dict]] = None,
                 precision: int = 6, title: str = "Overlays") -> Tuple[str, Dict[str, bytes]]:
    """One Document: Folder per sector > Folder per source document > bucket folders.

    `specs` is keyed by each result's `doc_key` (falling back to its layer key), so
    two sources of the same layer - EIA and OSM power plants, rail yards and Amtrak
    stations - keep their own titles, visibility, name templates and style rules.
    """
    specs = specs or {}
    styles, icons = [], {}
    by_sector: "OrderedDict[str, List[str]]" = OrderedDict()
    seen_style = set()
    for r in results:
        lk = r.logical
        doc_key = getattr(r, "doc_key", lk)
        spec = specs.get(doc_key, specs.get(lk))
        _, _, _, _, hidden, sector = style_for(lk, spec)
        style_key = doc_key
        if style_key not in seen_style:
            blocks, ic = styles_and_icons(lk, spec, style_key)
            styles.append(blocks)
            icons.update(ic)
            seen_style.add(style_key)
        rules = _rules(spec)
        _, buckets = _bucketize(r.features, r.group_by)
        sub = "".join(_folder(lbl, fs, lk, spec, rules, not hidden, precision, r.provenance, style_key)
                      for lbl, fs in buckets.items())
        if not sub:
            continue
        vis = "" if not hidden else "<visibility>0</visibility>"
        name = (spec or {}).get("title") or doc_key
        by_sector.setdefault(sector, []).append(
            f"<Folder><name>{_esc(name)} ({sub.count('<Placemark>')})</name>{vis}<open>0</open>"
            f"<description>{_prov_cdata(r.provenance)}</description>{sub}</Folder>")
    folders = "".join(f"<Folder><name>{_esc(sec)}</name><open>0</open>{''.join(fs)}</Folder>"
                      for sec, fs in by_sector.items())
    kml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
           f"<name>{_esc(title)}</name><open>1</open>{''.join(styles)}{folders}</Document></kml>")
    return kml, icons


def write_kmz(path: str, kml: str, icons: Optional[Dict[str, bytes]] = None) -> int:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml)
        for name, data in (icons or {}).items():
            z.writestr(name, data)
    return kml.count("<Placemark>")
