"""`overlaybuilder demo` - build a sample pack offline, with SYNTHETIC data.

Purpose: check how a pack looks and behaves in ATAK (folder tree, eye-toggles,
icons, popup layout, style rules) before spending a real build on the network.

Everything here is INVENTED. Names are prefixed "SAMPLE", coordinates are
placed on a neat grid near Chisago County, MN, and the provenance of every
layer says so, so a demo pack can never be mistaken for real infrastructure.
"""
from typing import List

from .drivers.base import Context, driver
from .model import Feature, LayerResult, Provenance

PROV_NOTE = ("SYNTHETIC DEMO DATA - invented values on a grid, NOT real infrastructure. "
             "For checking ATAK rendering only.")


def _prov(layer: str) -> Provenance:
    return Provenance(source_name=f"overlaybuilder demo ({layer})",
                      source_url="(generated locally - no network)",
                      license="Public domain (synthetic sample, no real data)",
                      retrieved="", driver="demo", notes=PROV_NOTE)


def _grid(n: int, lon0=-93.05, lat0=45.40, step=0.045):
    for i in range(n):
        yield [lon0 + (i % 5) * step, lat0 + (i // 5) * step * 0.7]


def _boundary() -> LayerResult:
    ring = [[-93.15, 45.33], [-92.65, 45.33], [-92.65, 45.78], [-93.15, 45.78], [-93.15, 45.33]]
    f = Feature({"type": "Polygon", "coordinates": [ring]},
                {"NAME": "SAMPLE County", "STATEFP": "27", "COUNTYFP": "999"})
    return LayerResult("county_boundary", [f], _prov("county_boundary"))


def _plants() -> LayerResult:
    rows = [("SAMPLE Riverside Station", 1146.4, 1210.0, "nuclear", "Pressurized water reactor", 2),
            ("SAMPLE Prairie Peaker", 212.5, 230.0, "natural gas", "Combustion turbine", 4),
            ("SAMPLE North Wind Farm", 98.0, 101.2, "wind", "Onshore wind turbine", 43),
            ("SAMPLE Solar Field A", 25.0, 31.5, "solar", "Photovoltaic", 1),
            ("SAMPLE Hydro Dam Plant", 8.4, 9.0, "hydroelectric", "Conventional hydroelectric", 3)]
    feats = []
    for (name, mw, nameplate, fuel, tech, gens), xy in zip(rows, _grid(len(rows))):
        feats.append(Feature({"type": "Point", "coordinates": xy},
                             {"Plant_Name": name, "Total_MW": mw, "Install_MW": nameplate,
                              "PrimSource": fuel, "tech_desc": tech, "Generators": gens,
                              "Utility_Name": "SAMPLE Power Cooperative", "Plant_Code": 90000 + len(feats),
                              "Period": "sample"}))
    return LayerResult("power_plants", feats, _prov("power_plants"), group_by=["fuel"])


def _substations() -> LayerResult:
    rows = [("SAMPLE East Sub", 345, 115, 6), ("SAMPLE West Sub", 161, 69, 3),
            ("SAMPLE Town Sub", 69, 34.5, 2), ("SAMPLE Tap", -999999, -999999, 1)]
    feats = []
    for (name, hi, lo, lines), xy in zip(rows, _grid(len(rows), lon0=-93.02, lat0=45.62)):
        feats.append(Feature({"type": "Point", "coordinates": xy},
                             {"NAME": name, "MAX_VOLT": hi, "MIN_VOLT": lo, "LINES": lines,
                              "TYPE": "TAP" if hi < 0 else "SUBSTATION", "STATUS": "IN SERVICE"}))
    return LayerResult("substations", feats, _prov("substations"), group_by=["type"])


def _lines() -> LayerResult:
    rows = [(500, "SAMPLE Interstate Intertie"), (345, "SAMPLE North Loop"),
            (161, "SAMPLE County Tie"), (69, "SAMPLE Local Feed")]
    feats = []
    for i, (kv, name) in enumerate(rows):
        y = 45.38 + i * 0.09
        feats.append(Feature({"type": "LineString",
                              "coordinates": [[-93.13, y], [-92.92, y + 0.05], [-92.70, y + 0.02]]},
                             {"OWNER": name, "VOLTAGE": kv, "VOLT_CLASS": str(kv),
                              "STATUS": "IN SERVICE", "SUB_1": "SAMPLE East Sub", "SUB_2": "SAMPLE West Sub"}))
    return LayerResult("transmission_lines", feats, _prov("transmission_lines"), group_by=["voltage_kv"])


def _pipelines() -> LayerResult:
    feats = [Feature({"type": "LineString", "coordinates": [[-93.14, 45.50], [-92.88, 45.56], [-92.66, 45.52]]},
                     {"TYPEPIPE": "Interstate", "Operator": "SAMPLE Gas Transmission", "Status": "Operating"}),
             Feature({"type": "LineString", "coordinates": [[-93.10, 45.41], [-92.75, 45.45]]},
                     {"TYPEPIPE": "Intrastate", "Operator": "SAMPLE Products Pipeline", "Status": "Operating"})]
    return LayerResult("pipelines", feats, _prov("pipelines"), group_by=["type"])


def _dams() -> LayerResult:
    rows = [("SAMPLE Upper Dam", 82.0, 14500, "High", "Satisfactory", 1961, "Flood control"),
            ("SAMPLE Mill Pond Dam", 24.5, 900, "Significant", "Fair", 1938, "Recreation"),
            ("SAMPLE Farm Pond", 11.0, 60, "Low", "Not rated", 1994, "Irrigation")]
    feats = []
    for (name, ht, stor, hz, cond, yr, purpose), xy in zip(rows, _grid(len(rows), lon0=-93.08, lat0=45.70)):
        feats.append(Feature({"type": "Point", "coordinates": xy},
                             {"Dam Name": name, "NID Height (Ft)": ht, "Max Storage (Acre-Ft)": stor,
                              "Hazard Potential Classification": hz, "Condition Assessment": cond,
                              "Year Completed": yr, "Primary Purpose": purpose,
                              "Primary Owner Type": "Local Government", "NID ID": f"MN9000{len(feats)}"}))
    return LayerResult("dams", feats, _prov("dams"), group_by=["hazard_class"])


def _hospitals() -> LayerResult:
    rows = [("SAMPLE Regional Medical Center", 186, "LEVEL III", "Y", "GENERAL ACUTE CARE"),
            ("SAMPLE Community Hospital", 42, "NOT AVAILABLE", "N", "CRITICAL ACCESS")]
    feats = []
    for (name, beds, trauma, heli, typ), xy in zip(rows, _grid(len(rows), lon0=-92.95, lat0=45.47)):
        feats.append(Feature({"type": "Point", "coordinates": xy},
                             {"NAME": name, "BEDS": beds, "TRAUMA": trauma, "HELIPAD": heli,
                              "TYPE": typ, "OWNER": "NON-PROFIT", "STATUS": "OPEN",
                              "TELEPHONE": "(000) 555-0100", "ADDRESS": "1 Sample Way"}))
    return LayerResult("hospitals", feats, _prov("hospitals"), group_by=["type"])


def _towers() -> LayerResult:
    feats = []
    for i, xy in enumerate(_grid(9, lon0=-93.12, lat0=45.36, step=0.05)):
        h = [1204.0, 498.0, 312.0][i % 3]
        feats.append(Feature({"type": "Point", "coordinates": xy},
                             {"name": f"SAMPLE Tower {i + 1:02d}",
                              "structure_type": ["Guyed tower", "Lattice tower", "Monopole"][i % 3],
                              "height_agl_m": round(h * 0.3048, 1), "status": "Constructed",
                              "owner": "SAMPLE Broadcasting LLC", "registration_number": f"10{i:05d}"}))
    return LayerResult("comm_towers", feats, _prov("comm_towers"), group_by=["structure_type"])


def _hazmat() -> LayerResult:
    rows = [("SAMPLE Co-op Ammonia Tank", "ammonia"), ("SAMPLE Water Plant Chlorine", "chlorine"),
            ("SAMPLE Propane Depot", "propane"), ("SAMPLE Fertiliser Store", "fertiliser")]
    feats = []
    for (name, content), xy in zip(rows, _grid(len(rows), lon0=-93.06, lat0=45.53)):
        feats.append(Feature({"type": "Point", "coordinates": xy},
                             {"name": name, "content": content, "man_made": "storage_tank",
                              "operator": "SAMPLE Farmers Cooperative"}))
    return LayerResult("hazmat_storage", feats, _prov("hazmat_storage"), group_by=["substance"])


def _grain() -> LayerResult:
    feats = []
    for i, xy in enumerate(_grid(6, lon0=-92.98, lat0=45.58, step=0.03)):
        feats.append(Feature({"type": "Point", "coordinates": xy},
                             {"name": f"SAMPLE Elevator {i + 1}", "man_made": "silo",
                              "content": "grain" if i % 2 else "corn",
                              "operator": "SAMPLE Grain Co"}))
    return LayerResult("grain_storage", feats, _prov("grain_storage"), group_by=["type", "substance"])


def _mines() -> LayerResult:
    rows = [("SAMPLE Sand & Gravel Pit", "sand", "quarry"), ("SAMPLE Aggregate Quarry", "limestone", "quarry")]
    feats = []
    for (name, res, kind), xy in zip(rows, _grid(len(rows), lon0=-92.80, lat0=45.72)):
        x, y = xy
        d = 0.008
        feats.append(Feature({"type": "Polygon",
                              "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]},
                             {"name": name, "resource": res, "landuse": kind,
                              "operator": "SAMPLE Aggregates Inc"}))
    return LayerResult("mines", feats, _prov("mines"), group_by=["substance"])


def _wastewater() -> LayerResult:
    rows = [("SAMPLE Municipal WWTP", 4.25, "POTW"), ("SAMPLE Village Lagoon", 0.18, "POTW")]
    feats = []
    for (name, mgd, typ), xy in zip(rows, _grid(len(rows), lon0=-92.86, lat0=45.66)):
        feats.append(Feature({"type": "Point", "coordinates": xy},
                             {"CWP_NAME": name, "CWP_TOTAL_DESIGN_FLOW_NMBR": mgd,
                              "CWP_FACILITY_TYPE_INDICATOR": typ, "NPDES_ID": f"MN00{len(feats)}9999",
                              "CWP_PERMIT_STATUS_DESC": "Effective"}))
    return LayerResult("wastewater_treatment", feats, _prov("wastewater_treatment"))


def _parcels() -> LayerResult:
    feats = []
    for i, xy in enumerate(_grid(24, lon0=-93.00, lat0=45.44, step=0.01)):
        x, y = xy
        d = 0.004
        feats.append(Feature({"type": "Polygon",
                              "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]},
                             {"PIN": f"SAMPLE 99.{i:04d}.000", "CITY": ["Sampletown", "Demo Lake", "Testburg"][i % 3],
                              "ACRES": round(1.2 + i * 0.1, 2)}))
    return LayerResult("parcels", feats, _prov("parcels"), group_by=["CITY"])


def _walk_coords(coords, fn):
    """Map fn over every position in a GeoJSON coordinate tree, at any nesting."""
    if coords and isinstance(coords[0], (int, float)):
        return fn(coords)
    return [_walk_coords(c, fn) for c in coords]


def _coord_bbox(results) -> tuple:
    xs, ys = [], []

    def note(pt):
        xs.append(pt[0])
        ys.append(pt[1])
        return pt

    for r in results:
        for f in r.features:
            if f.geometry:
                _walk_coords(f.geometry["coordinates"], note)
    if not xs:
        raise ValueError("no coordinates to rescale")
    return (min(xs), min(ys), max(xs), max(ys))


def rescale_to_bbox(results, bbox, margin: float = 0.06) -> None:
    """Move the synthetic grid into `bbox`, in place, uniformly.

    The sample layout is drawn around Chisago County. Pointing the demo at a
    whole state would otherwise leave every placemark in one corner. Scaling is
    UNIFORM (one factor for both axes) so the shapes stay shapes; `margin`
    keeps them off the envelope edge. This moves invented points around an
    invented map - it is not a projection and no real feature is relocated.
    """
    w, s, e, n = bbox
    sx0, sy0, sx1, sy1 = _coord_bbox(results)
    k = min(((e - w) * (1 - 2 * margin)) / ((sx1 - sx0) or 1e-9),
            ((n - s) * (1 - 2 * margin)) / ((sy1 - sy0) or 1e-9))
    cxs, cys = (sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0
    cxt, cyt = (w + e) / 2.0, (s + n) / 2.0

    def move(pt):
        return [round(cxt + (pt[0] - cxs) * k, 6),
                round(cyt + (pt[1] - cys) * k, 6)] + list(pt[2:])

    for r in results:
        for f in r.features:
            if f.geometry:
                f.geometry["coordinates"] = _walk_coords(f.geometry["coordinates"], move)


BUILDERS = [_boundary, _plants, _substations, _lines, _pipelines, _dams,
            _hospitals, _towers, _wastewater, _hazmat, _grain, _mines, _parcels]
_BY_LAYER = {b().logical: b for b in BUILDERS}


@driver("demo")
def fetch(logical: str, spec: dict, ctx: Context) -> LayerResult:
    """Synthetic source, for `overlaybuilder demo` and for offline testing."""
    b = _BY_LAYER.get(logical)
    if b is None:
        raise RuntimeError(f"no demo data for layer '{logical}'; have: {sorted(_BY_LAYER)}")
    return b()


def demo_specs_and_results():
    """(specs keyed by layer, LayerResults) for a synthetic pack."""
    specs = {
        "power_plants": {"layer": "power_plants", "driver": "demo", "entity": "power_plant",
                         "name": "{name} ({capacity_mw})"},
        "substations": {"layer": "substations", "driver": "demo",
                        "fields": {"max_voltage_kv": {"from": ["MAX_VOLT@kV"], "nulls": [-999999]},
                                   "min_voltage_kv": {"from": ["MIN_VOLT@kV"], "nulls": [-999999]}},
                        "style_rules": [{"when": "type = TAP", "icon": "ring", "color": "ff00c0ff", "width": 1},
                                        {"when": "max_voltage_kv >= 300", "color": "ff0000ff", "width": 3, "icon": "square"},
                                        {"when": "max_voltage_kv >= 100", "color": "ff0080ff", "width": 2, "icon": "square"}]},
        "transmission_lines": {"layer": "transmission_lines", "driver": "demo", "name": "{owner} {voltage_kv}",
                               "style_rules": [{"when": "voltage_kv >= 500", "color": "ff0000ff", "width": 5},
                                               {"when": "voltage_kv >= 300", "color": "ff0040ff", "width": 4},
                                               {"when": "voltage_kv >= 200", "color": "ff0080ff", "width": 3},
                                               {"when": "voltage_kv >= 100", "color": "ff00b0ff", "width": 2},
                                               {"when": "voltage_kv < 100", "color": "ff00e0ff", "width": 1}]},
        "dams": {"layer": "dams", "driver": "demo", "name": "{name} ({height_ft}, {hazard_class})",
                 "style_rules": [{"when": "hazard_class ~ ^high", "color": "ff0000ff", "icon": "triangle", "width": 3},
                                 {"when": "hazard_class ~ ^significant", "color": "ff0080ff", "icon": "triangle", "width": 2}]},
        "hospitals": {"layer": "hospitals", "driver": "demo", "name": "{name} ({beds} beds)",
                      "style_rules": [{"when": "trauma ~ LEVEL", "color": "ff0000ff", "icon": "plus", "width": 3}]},
        "comm_towers": {"layer": "comm_towers", "driver": "demo", "name": "{name} ({structure_type} {height_ft})",
                        "fields": {"height_ft": {"from": ["height_agl_m@m"]}},
                        "style_rules": [{"when": "height_ft >= 1000", "color": "ffff00ff", "icon": "triangle", "width": 3},
                                        {"when": "height_ft >= 400", "color": "ffff40ff", "icon": "triangle", "width": 2}]},
        "pipelines": {"layer": "pipelines", "driver": "demo", "name": "{operator} {substance}",
                      "fields": {"substance": {"const": "natural gas"}}},
        "wastewater_treatment": {"layer": "wastewater_treatment", "driver": "demo", "name": "{name} ({flow_mgd})",
                                 "fields": {"name": {"from": ["CWP_NAME"]},
                                            "flow_mgd": {"from": ["CWP_TOTAL_DESIGN_FLOW_NMBR"]},
                                            "source_id": {"from": ["NPDES_ID"]}}},
        "hazmat_storage": {"layer": "hazmat_storage", "driver": "demo", "name": "{name} ({substance})",
                           "fields": {"substance": {"from": ["content"]}},
                           "style_rules": [{"when": "substance ~ (?i)ammonia", "color": "ff00a5ff", "icon": "ring", "width": 3},
                                           {"when": "substance ~ (?i)(chlorine|acid)", "color": "ff0000ff", "icon": "ring", "width": 3}]},
        "grain_storage": {"layer": "grain_storage", "driver": "demo", "name": "{name} ({substance})",
                          "fields": {"substance": {"from": ["content"]}, "type": {"from": ["man_made"]}}},
        "mines": {"layer": "mines", "driver": "demo", "name": "{name} ({substance})",
                  "fields": {"substance": {"from": ["resource"]}, "type": {"from": ["landuse"]}}},
        "parcels": {"layer": "parcels", "driver": "demo"},
        "county_boundary": {"layer": "county_boundary", "driver": "demo"},
    }
    results = [b() for b in BUILDERS]
    return specs, results


def build_demo(out_dir: str, precision: int = 6, log=print, aoi=None,
               group_by: str = "sector") -> dict:
    """Write a synthetic pack with no network.

    `aoi` (an Aoi) moves the sample grid into that area's envelope, so the pack
    sits where an operator expects to find it on the map. `group_by` matches
    `overlaybuilder build`: "sector" writes one SAMPLE_<AOI>_<Sector>.kmz per
    sector, "layer" the per-layer files, "both" both.

    Every filename here starts with SAMPLE_ and every placemark name with
    "SAMPLE", because none of this is real infrastructure.
    """
    import datetime as _dt
    import json
    import os
    from collections import OrderedDict

    from . import convert, normalize
    from .build import GROUPINGS, aoi_prefix, sector_slug

    group_by = (group_by or "sector").lower()
    if group_by not in GROUPINGS:
        raise ValueError(f"group_by must be one of {GROUPINGS}, got {group_by!r}")

    os.makedirs(out_dir, exist_ok=True)
    specs, results = demo_specs_and_results()

    # Describe the box we ACTUALLY used, never the label we were handed. A county
    # Aoi carries its STATE envelope until TIGER supplies the polygon during a real
    # build, so "inside Chisago County" would be false by a few hundred km; an AOI
    # with no bbox at all (country:XX) cannot be placed, and then the pack must not
    # be named for it either.
    from .aoi import bbox_of_geometry
    box = None
    exact = False
    if aoi is not None:
        if aoi.geometry:
            box, exact = bbox_of_geometry(aoi.geometry), True
        elif aoi.bbox:
            box, exact = aoi.bbox, aoi.kind in ("state", "bbox")
    if box:
        rescale_to_bbox(results, box)
        w, s_, e, n = box
        env = f"{w:.2f},{s_:.2f},{e:.2f},{n:.2f}"
        where = (f"a grid inside the {aoi.describe()} envelope ({env})" if exact else
                 f"a grid inside {env} - the query envelope used for {aoi.describe()}, "
                 f"which is WIDER than its actual boundary")
    else:
        where = "a grid near Chisago County, MN"
        if aoi is not None:
            log(f"[!] {aoi.describe()} has no envelope to place the sample grid in; "
                f"leaving it near Chisago County and naming the pack SAMPLE_*")
    prefix = f"SAMPLE_{aoi_prefix(aoi)}" if box else "SAMPLE"

    stamp = _dt.date.today().isoformat()
    written, rows = [], []
    for res in results:
        spec = specs.get(res.logical, {"layer": res.logical, "driver": "demo"})
        res.provenance.retrieved = stamp
        res.provenance.notes = f"{PROV_NOTE} Placed on {where}."
        normalize.apply_to_layer(res, spec)
        if group_by in ("layer", "both"):
            kml, icons = convert.layer_kml(res, spec, precision, title=f"{res.logical} (SAMPLE)")
            path = os.path.join(out_dir, f"{prefix}_{res.logical}.kmz")
            n = convert.write_kmz(path, kml, icons)
            written.append(path)
            rows.append({"layer": res.logical, "status": "ok",
                         "features": len(res.features), "placemarks": n})
        else:
            rows.append({"layer": res.logical, "status": "ok", "features": len(res.features)})
        log(f"  {res.logical:24} {len(res.features):4} features")

    if group_by in ("sector", "both"):
        by_sector = OrderedDict()
        for res in results:
            sector = convert.style_for(res.logical, specs.get(res.logical))[5]
            by_sector.setdefault(sector, []).append(res)
        log("")
        for sector, group in by_sector.items():
            fname = f"{prefix}_{sector_slug(sector)}.kmz"
            path = os.path.join(out_dir, fname)
            kml, icons = convert.combined_kml(
                group, specs, precision, sector_folders=False,
                title=f"SAMPLE {sector} - synthetic demo data, NOT real infrastructure")
            n = convert.write_kmz(path, kml, icons)
            written.append(path)
            rows.append({"layer": sector_slug(sector), "sector": sector, "status": "ok",
                         "features": sum(len(r.features) for r in group), "placemarks": n,
                         "doc": fname})
            log(f"  {fname:42} {n:4} placemarks")

    kml, icons = convert.combined_kml(results, specs, precision,
                                      title="SAMPLE Critical Infrastructure (synthetic demo data)")
    allp = os.path.join(out_dir, "DEMO_SAMPLE_ALL.kmz")
    convert.write_kmz(allp, kml, icons)
    written.append(allp)
    # rows carry a row per layer AND a row per sector pack, both with "features"
    # over the same features - so publish the real total rather than let a caller
    # sum the column and print double.
    manifest = {"tool": "overlaybuilder demo", "built": stamp, "warning": PROV_NOTE,
                "aoi": aoi.describe() if aoi is not None else None,
                "placement": where, "group_by": group_by,
                "features_total": sum(len(r.features) for r in results),
                "packs": [os.path.basename(w) for w in written if os.path.basename(w) != "DEMO_SAMPLE_ALL.kmz"],
                "layers": rows, "files": [os.path.basename(w) for w in written]}
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    sector_files = [os.path.basename(w) for w in written if os.path.basename(w).startswith(prefix)]
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as fh:
        fh.write("SAMPLE PACK - SYNTHETIC DATA\n" + "=" * 28 + "\n\n" + PROV_NOTE + "\n\n"
                 f"Placed on {where}.\n"
                 "Nothing in this pack is real. Every placemark name starts with SAMPLE and\n"
                 "every file with SAMPLE_. Do not plan against it.\n\n"
                 "Load these into ATAK (Import Manager > Local SD) to check the folder tree,\n"
                 "eye-toggles, icons, line styling by voltage and the popup layout:\n\n"
                 + "".join(f"    {f}\n" for f in sector_files)
                 + "\nThen delete them and build a real pack on a machine with network access:\n"
                 "    overlaybuilder build --aoi state:MN --group-by sector\n")
    return manifest
