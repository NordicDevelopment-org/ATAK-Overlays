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


def build_demo(out_dir: str, precision: int = 6, log=print) -> dict:
    """Write a synthetic pack (per-layer KMZ + ALL.kmz + manifest) with no network."""
    import datetime as _dt
    import json
    import os

    from . import convert, normalize

    os.makedirs(out_dir, exist_ok=True)
    specs, results = demo_specs_and_results()
    stamp = _dt.date.today().isoformat()
    written, rows = [], []
    for res in results:
        spec = specs.get(res.logical, {"layer": res.logical, "driver": "demo"})
        res.provenance.retrieved = stamp
        normalize.apply_to_layer(res, spec)
        kml, icons = convert.layer_kml(res, spec, precision, title=f"{res.logical} (SAMPLE)")
        path = os.path.join(out_dir, f"{res.logical}.kmz")
        n = convert.write_kmz(path, kml, icons)
        written.append(path)
        rows.append({"layer": res.logical, "status": "ok", "features": len(res.features), "placemarks": n})
        log(f"  {res.logical:24} {len(res.features):4} features")
    kml, icons = convert.combined_kml(results, specs, precision,
                                      title="SAMPLE Critical Infrastructure (synthetic demo data)")
    allp = os.path.join(out_dir, "DEMO_SAMPLE_ALL.kmz")
    convert.write_kmz(allp, kml, icons)
    written.append(allp)
    manifest = {"tool": "overlaybuilder demo", "built": stamp, "warning": PROV_NOTE,
                "layers": rows, "files": [os.path.basename(w) for w in written]}
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as fh:
        fh.write("SAMPLE PACK - SYNTHETIC DATA\n" + "=" * 28 + "\n\n" + PROV_NOTE + "\n\n"
                 "Load DEMO_SAMPLE_ALL.kmz into ATAK (Import Manager > Local SD) to check the\n"
                 "folder tree, eye-toggles, icons, line styling by voltage and the popup layout.\n"
                 "Then delete it and build a real pack:  overlaybuilder build --aoi county:27025\n")
    return manifest
