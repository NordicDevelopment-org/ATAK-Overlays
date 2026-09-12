"""Attribute normalization: keep every source field, and ADD a canonical,
unit-labelled view so a power plant from EIA, HIFLD, or OSM reads the same in
ATAK ("Capacity: 1,146 MW - Fuel: Nuclear - Operator: Xcel Energy").

Rules (PROJECT RULES): never invent a value. A canonical field is filled only
when a mapped source field is present and parseable; otherwise it is absent.
Unit conversions are explicit (OSM volts -> kV, W/kW/GW suffixes -> MW,
meters -> ft only when a source is documented as metric). Raw fields are never
overwritten: on a name collision the raw value is kept as `src_<name>`.

Catalog usage:
    fields:                       # canonical_key: source spec
      capacity_mw: {from: [Total_MW, Install_MW], unit: MW}
      voltage_kv:  {from: [voltage], unit: V}          # V -> kV conversion
      operator:    {from: [Utility_Name, operator]}
    name: "{plant_name} ({capacity_mw} MW)"            # placemark title template
    entity: power_plant                                # for cross-source reconcile

Sensible defaults exist for every canonical key (DEFAULT_FROM), so most
sources need no `fields:` block at all.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from .model import Feature, LayerResult

# canonical key -> (label, unit, format)
CANONICAL: Dict[str, Tuple[str, str, str]] = {
    "name":             ("Name", "", "{}"),
    "capacity_mw":      ("Capacity", "MW", "{:,.1f}"),
    "nameplate_mw":     ("Nameplate capacity", "MW", "{:,.1f}"),
    "capacity_mwh":     ("Energy capacity", "MWh", "{:,.1f}"),
    "generators":       ("Generators", "", "{}"),
    "fuel":             ("Primary fuel", "", "{}"),
    "technology":       ("Technology", "", "{}"),
    "voltage_kv":       ("Voltage", "kV", "{:g}"),
    "max_voltage_kv":   ("Max voltage", "kV", "{:g}"),
    "min_voltage_kv":   ("Min voltage", "kV", "{:g}"),
    "volt_class":       ("Voltage class", "", "{}"),
    "circuits":         ("Circuits", "", "{}"),
    "lines":            ("Lines", "", "{}"),
    "frequency_hz":     ("Frequency", "Hz", "{:g}"),
    "operator":         ("Operator", "", "{}"),
    "owner":            ("Owner", "", "{}"),
    "status":           ("Status", "", "{}"),
    "type":             ("Type", "", "{}"),
    "substance":        ("Substance", "", "{}"),
    "diameter_in":      ("Diameter", "in", "{:g}"),
    "pressure":         ("Pressure", "", "{}"),
    "capacity_bpd":     ("Capacity", "bbl/day", "{:,.0f}"),
    "capacity_mmcfd":   ("Capacity", "MMcf/day", "{:,.1f}"),
    "capacity_mgy":     ("Capacity", "Mgal/yr", "{:,.1f}"),
    "horsepower":       ("Horsepower", "hp", "{:,.0f}"),
    "storage_acre_ft":  ("Max storage", "acre-ft", "{:,.0f}"),
    "height_ft":        ("Height", "ft", "{:,.1f}"),
    "height_m":         ("Height", "m", "{:,.1f}"),
    "hazard_class":     ("Hazard class", "", "{}"),
    "condition":        ("Condition", "", "{}"),
    "year_built":       ("Year built", "", "{}"),
    "purpose":          ("Purpose", "", "{}"),
    "flow_mgd":         ("Design flow", "MGD", "{:,.2f}"),
    "population_served": ("Population served", "", "{:,.0f}"),
    "capacity_persons": ("Capacity", "persons", "{:,.0f}"),
    "population":       ("Population", "", "{:,.0f}"),
    "staff":            ("Staff", "", "{:,.0f}"),
    "security_level":   ("Security level", "", "{}"),
    "beds":             ("Beds", "", "{:,.0f}"),
    "trauma":           ("Trauma", "", "{}"),
    "helipad":          ("Helipad", "", "{}"),
    "emergency":        ("Emergency dept", "", "{}"),
    "tracks":           ("Tracks", "", "{}"),
    "runway_ft":        ("Longest runway", "ft", "{:,.0f}"),
    "elevation_ft":     ("Elevation", "ft", "{:,.0f}"),
    "structure_type":   ("Structure type", "", "{}"),
    "address":          ("Address", "", "{}"),
    "city":             ("City", "", "{}"),
    "county":           ("County", "", "{}"),
    "state":            ("State", "", "{}"),
    "phone":            ("Phone", "", "{}"),
    "source_id":        ("Source ID", "", "{}"),
    "website":          ("Website", "", "{}"),
    "start_date":       ("Commissioned", "", "{}"),
    "adt":              ("Avg daily traffic", "veh/day", "{:,.0f}"),
    "length_ft":        ("Length", "ft", "{:,.0f}"),
}

# Default candidate source fields per canonical key (case-insensitive match).
# Order = priority. Units: a candidate may be "FIELD@unit" to declare its unit.
DEFAULT_FROM: Dict[str, List[str]] = {
    "name": ["Plant_Name", "PLANT_NAME", "Plant Name", "NAME", "name", "FACILITY", "FACILITY_NAME",
             "Facility Name", "Dam Name", "Station Name", "Site Name", "Provider Name", "Site_Name",
             "CWP_NAME", "PWS_NAME", "SHELTER_NAME", "PORT_NAME", "STNNAME", "YARDNAME", "ARPT_NAME",
             "SITE_NAME", "DAM_NAME", "Dam_Name", "FACNAME", "STATION", "Utility_Name", "FULLNAME",
             "Ident", "ARPT_NAME", "official_name", "ref", "PIN", "PID"],
    "capacity_mw": ["Total_MW", "TOTAL_MW", "Capacity_MW", "CAPACITY_MW", "OPER_CAP", "SUMMER_CAP",
                    "Summer_Capacity_MW", "Net Summer Capacity (MW)", "Install_MW", "INSTALL_MW",
                    "Nameplate_MW", "NAMEPLATE", "Nameplate Capacity (MW)",
                    "plant:output:electrical@W", "generator:output:electrical@W", "MW"],
    "nameplate_mw": ["Install_MW", "INSTALL_MW", "Nameplate_MW", "NAMEPLATE_MW", "Nameplate Capacity (MW)",
                     "NAMEPLATE", "Nameplate"],
    "capacity_mwh": ["Energy_Capacity_MWh", "Nameplate Energy Capacity (MWh)", "MWh",
                     "plant:output:electrical:storage@Wh"],
    "generators": ["Generators", "NUM_GEN", "GENERATORS", "generator:count"],
    "fuel": ["PrimSource", "PRIMSOURCE", "PRIM_FUEL", "Primary_Fuel", "PRIMARY_FUEL", "Energy Source Code",
             "plant:source", "generator:source", "FUEL", "Fuel", "source_desc"],
    "technology": ["tech_desc", "TECH_DESC", "Technology", "TECHNOLOGY", "plant:method", "generator:method",
                   "generator:type", "Prime Mover Code"],
    "voltage_kv": ["VOLTAGE@kV", "Voltage@kV", "VOLT@kV", "KV", "voltage@V", "VOLT_KV"],
    "max_voltage_kv": ["MAX_VOLT@kV", "Max_Volt@kV", "MAXVOLT@kV"],
    "min_voltage_kv": ["MIN_VOLT@kV", "Min_Volt@kV", "MINVOLT@kV"],
    "volt_class": ["VOLT_CLASS", "Volt_Class", "VOLTCLASS"],
    "circuits": ["circuits", "CIRCUITS", "NUM_CIRCUITS"],
    "lines": ["LINES", "Lines"],
    "frequency_hz": ["frequency", "FREQUENCY"],
    "operator": ["Utility_Name", "UTILITY_NAME", "OPERATOR", "Operator", "operator", "Utility", "OPER_NAME"],
    "owner": ["OWNER", "Owner", "owner", "RROWNER1", "Primary Owner Type", "OWNER_TYPE", "Owner Type", "Owner_Name"],
    "status": ["STATUS", "Status", "status", "STATUSDESC", "Operating Status", "OPSTATUS", "disused", "abandoned"],
    "type": ["TYPE", "Type", "substation", "tower:type", "power", "man_made", "amenity", "TYPE_CODE",
             "FAC_TYPE", "Facility_Type", "NAICS_DESC", "healthcare", "emergency", "Dam Type", "DAM_TYPE",
             "Primary Dam Type", "pipeline", "usage", "railway", "aeroway", "telecom", "landuse"],
    "substance": ["substance", "SUBSTANCE", "COMMODITY", "Commodity", "content", "Product", "PRODUCT"],
    "diameter_in": ["DIAMETER", "Diameter", "!diameter@mm", "DIAM_IN", "Pipe_Diameter"],
    "pressure": ["pressure", "PRESSURE", "MAOP"],
    "capacity_bpd": ["Cap_Bpd", "CAP_BPD", "Capacity_BPD", "Barrels_per_Day", "AD_Mbpd@Mbpd", "Total_Cap_BPD",
                     "Crude_Cap", "BBLS_DAY", "Cap_BPCD", "Working_Storage_Cap_BBL"],
    "capacity_mmcfd": ["Cap_MMcfd", "CAP_MMCFD", "Capacity_MMcfd", "Plant_Flow", "Capacity (MMcf/d)", "MMCFD"],
    "capacity_mgy": ["Cap_Mmgal", "CAP_MGY", "Capacity_MGY", "Cap_MMGal_Yr", "Production Capacity (Mmgal/yr)"],
    "horsepower": ["HP", "Horsepower", "HORSEPOWER", "Total_HP"],
    "storage_acre_ft": ["Max Storage (Acre-Ft)", "MAX_STOR", "Max_Storage", "NID Storage (Acre-Ft)", "NID_STOR",
                        "Normal Storage (Acre-Ft)"],
    "height_ft": ["Dam Height (Ft)", "DAM_HEIGHT", "NID Height (Ft)", "NID_HEIGHT", "HEIGHT_FT", "OVERALL_HGT",
                  "Height", "STRUC_HGT", "!height@m", "ELEV_HGT"],
    "hazard_class": ["Hazard Potential Classification", "HAZARD", "Hazard", "HAZARD_CLASS", "NID Hazard"],
    "condition": ["Condition Assessment", "CONDITION", "Condition", "COND_ASSESS", "LOWEST_RATING"],
    "year_built": ["Year Completed", "YEAR_COMPL", "YEAR_BUILT", "Year_Built", "start_date", "Operating Year",
                   "YEAR"],
    "purpose": ["Primary Purpose", "PURPOSES", "Purposes", "PURPOSE"],
    "flow_mgd": ["CWP_TOTAL_DESIGN_FLOW_NMBR", "Design_Flow_MGD", "DESIGN_FLOW", "Design Flow (MGD)",
                 "FLOW_MGD", "Existing Total Flow (MGD)", "CWP_ACTUAL_AVERAGE_FLOW_NMBR", "AVG_FLOW_MGD"],
    "population_served": ["POP_SERVED", "Population Served", "PopServed", "Population Served Count"],
    "capacity_persons": ["EVACUATION_CAPACITY", "POST_IMPACT_CAPACITY", "CAPACITY", "Capacity", "capacity"],
    "population": ["POPULATION", "Population", "population", "tot_res", "TOT_RES", "ENROLLMENT"],
    "staff": ["TTL_STAFF", "tot_staff", "TOT_STAFF", "STAFF"],
    "security_level": ["SECURELVL", "SECURITY_LEVEL", "SecureLvl"],
    "beds": ["BEDS", "Beds", "beds", "TOTAL_BEDS", "Total_Beds", "NUM_BEDS"],
    "trauma": ["TRAUMA", "Trauma", "TRAUMA_LEVEL"],
    "helipad": ["HELIPAD", "Helipad"],
    "emergency": ["emergency", "EMERGENCY", "ER"],
    "tracks": ["TRACKS", "Tracks", "tracks"],
    "runway_ft": ["MAX_RWY_LENGTH", "Longest_Runway", "RUNWAY_LEN", "LONGEST_RUNWAY", "!length@m"],
    "elevation_ft": ["ELEVATION", "ELEV", "Elevation", "!ele@m", "GROUND_ELEV"],
    "structure_type": ["STRUCTURE_TYPE", "Structure_Type", "STRUC_TYPE", "tower:construction", "TOWER_TYPE"],
    "address": ["ADDRESS", "Address", "addr:full", "STREET", "Street_Address", "FULLADDR", "ADDR"],
    "city": ["CITY", "City", "addr:city", "MUNICIPALITY", "CTU_NAME"],
    "county": ["COUNTY", "County", "COUNTYNAME", "County_Name", "CNTY_NAME"],
    "state": ["STATE", "State", "StateName", "STATE_ABBR", "ST", "addr:state"],
    "phone": ["TELEPHONE", "PHONE", "Phone", "phone", "contact:phone"],
    "source_id": ["Plant_Code", "PLANT_CODE", "NID ID", "NID_ID", "ID", "OBJECTID", "FID", "GLOBALID",
                  "Registration Number", "REG_NUM", "STRUCTURE_NUMBER", "FACILITY_ID", "NPDES_ID",
                  "NPDES", "PWSID", "NCESSCH", "UNITID", "STNCODE", "GNIS_ID", "SYSTEM_ID", "FDID",
                  "osm_id", "CCN_ID", "PROVIDER_ID"],
    "website": ["WEBSITE", "Website", "website", "URL", "contact:website"],
    "start_date": ["start_date", "Operating Year", "YEAR_COMPL", "Year Completed", "ONLINE_DATE"],
    "adt": ["ADT", "ADT_029", "AADT", "Average Daily Traffic"],
    "length_ft": ["STRUCTURE_LEN_MT@m", "Structure_Length", "LENGTH_FT"],
}

# Unit -> multiplier to reach the canonical unit of the key.
_UNIT_TO_CANON = {
    ("capacity_mw", "W"): 1e-6, ("capacity_mw", "kW"): 1e-3, ("capacity_mw", "MW"): 1.0, ("capacity_mw", "GW"): 1e3,
    ("capacity_mwh", "Wh"): 1e-6, ("capacity_mwh", "kWh"): 1e-3, ("capacity_mwh", "MWh"): 1.0,
    ("voltage_kv", "V"): 1e-3, ("voltage_kv", "kV"): 1.0,
    ("max_voltage_kv", "V"): 1e-3, ("max_voltage_kv", "kV"): 1.0,
    ("min_voltage_kv", "V"): 1e-3, ("min_voltage_kv", "kV"): 1.0,
    ("height_ft", "m"): 3.28084, ("height_ft", "ft"): 1.0,
    ("runway_ft", "m"): 3.28084, ("runway_ft", "ft"): 1.0,
    ("elevation_ft", "m"): 3.28084, ("elevation_ft", "ft"): 1.0,
    ("length_ft", "m"): 3.28084, ("length_ft", "ft"): 1.0,
    ("length_ft", "mi"): 5280.0, ("length_ft", "km"): 3280.84, ("length_ft", "nmi"): 6076.12,
    ("height_ft", "km"): 3280.84, ("runway_ft", "mi"): 5280.0, ("elevation_ft", "km"): 3280.84,
    ("diameter_in", "mm"): 1 / 25.4, ("diameter_in", "in"): 1.0,
    ("capacity_bpd", "Mbpd"): 1000.0, ("capacity_bpd", "bpd"): 1.0,
}
NUMERIC_KEYS = {k for k, (_, u, f) in CANONICAL.items() if ":" in f and f != "{}"} | {"generators", "circuits", "lines", "tracks", "beds", "year_built", "capacity_persons", "population", "staff"}
_SUFFIX_MULT = {"": 1.0, "w": 1.0, "kw": 1e3, "mw": 1e6, "gw": 1e9, "v": 1.0, "kv": 1e3, "mv": 1e6,
                "wh": 1.0, "kwh": 1e3, "mwh": 1e6, "gwh": 1e9, "m": 1.0, "ft": 1.0, "hz": 1.0}


def parse_number(v: Any, unit_hint: Optional[str] = None) -> Optional[float]:
    """Parse numbers as they appear in the wild, including OSM unit suffixes
    ('1.2 MW', '345 kV', '115000;34500' -> max). Returns the value in the
    *hinted* unit's base (W, V, ...) when a suffix is present, else raw."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().replace(",", "")
    if not s or s in ("null", "none", "n/a", "na", "-", "unknown"):
        return None
    vals = []
    for part in re.split(r"[;/|]", s):
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*([a-z]*)", part.strip())
        if not m:
            continue
        num, suf = float(m.group(1)), m.group(2)
        if suf and suf in _SUFFIX_MULT and unit_hint:
            # bring to the hint's base unit (W, V, Wh, m)
            base_of_hint = {"W": 1.0, "kW": 1e3, "MW": 1e6, "GW": 1e9, "V": 1.0, "kV": 1e3,
                            "Wh": 1.0, "kWh": 1e3, "MWh": 1e6, "m": 1.0, "ft": 1.0}.get(unit_hint, 1.0)
            vals.append(num * _SUFFIX_MULT[suf] / base_of_hint)
        else:
            vals.append(num)
    return max(vals) if vals else None


def _get_ci(props: dict, field: str):
    if field in props:
        return props[field]
    fl = field.lower()
    for k in props:
        if k.lower() == fl:
            return props[k]
    return None


def _squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _get_loose(props: dict, field: str):
    """Match ignoring case AND punctuation, so a candidate `DAM_NAME` still finds
    the column `Dam Name` and `Cap_MMcfd` finds `CAP MMCFD`. Government servers
    re-spell columns constantly; the meaning does not change."""
    want = _squash(field)
    if not want:
        return None
    for k in props:
        if _squash(k) == want:
            return props[k]
    return None


DEFAULT_NULLS = {"NOT AVAILABLE", "NOT APPLICABLE", "UNKNOWN", "N/A", "NA", "NULL", "NONE", "-"}
# Sentinels governments use for "no value" in numeric columns (HIFLD, NBI, FAA).
# Compared numerically so -999, -999.0 and "-999" are all caught.
NULL_NUMBERS = {-999.0, -9999.0, -99999.0, -999999.0, -9999999.0, -1e30}


def _is_null(raw: Any, extra) -> bool:
    s = str(raw).strip().upper()
    if s in DEFAULT_NULLS:
        return True
    try:
        f = float(s.replace(",", ""))
    except (TypeError, ValueError):
        f = None
    if f is not None and f in NULL_NUMBERS:
        return True
    for n in extra or ():
        if s == str(n).strip().upper():
            return True
        try:
            if f is not None and f == float(n):
                return True
        except (TypeError, ValueError):
            pass
    return False


def _split_candidate(f: str) -> Tuple[str, Optional[str], bool]:
    """`FIELD`, `FIELD@unit`, or `!FIELD@unit` (leading ! = exact spelling only,
    for lowercase OSM tags like `length@m` that would otherwise capture a
    source's unrelated `LENGTH` column)."""
    exact_only = f.startswith("!")
    f = f[1:] if exact_only else f
    if "@" in f:
        n, u = f.split("@", 1)
        return n, u, exact_only
    return f, None, exact_only


def _candidates(key: str, spec_fields: dict) -> List[Tuple[str, Optional[str]]]:
    cfg = (spec_fields or {}).get(key)
    out: List[Tuple[str, Optional[str]]] = []
    if cfg:
        unit = cfg.get("unit") if isinstance(cfg, dict) else None
        frm = cfg.get("from", []) if isinstance(cfg, dict) else cfg
        if isinstance(frm, str):
            frm = [frm]
        for f in frm:
            n, u, exact = _split_candidate(f)
            out.append((n, u if u is not None else unit, exact))
    if not cfg or (isinstance(cfg, dict) and cfg.get("defaults", True)):
        for f in DEFAULT_FROM.get(key, []):
            n, u, exact = _split_candidate(f)
            out.append((n, u, exact))
    return out


class UnknownUnit(ValueError):
    """A catalog mapping declared `FIELD@unit` for a unit with no conversion."""


def known_unit(key: str, unit: Optional[str]) -> bool:
    """True when `FIELD@unit` for this canonical key has a defined conversion."""
    if not unit:
        return True
    canonical = CANONICAL.get(key, ("", "", ""))[1]
    return (key, unit) in _UNIT_TO_CANON or unit == canonical


def _convert(key: str, raw: Any, unit: Optional[str]) -> Any:
    if key in NUMERIC_KEYS:
        num = parse_number(raw, unit)
        if num is None:
            return None
        if unit and not known_unit(key, unit):
            # silently treating "3.2 miles" as 3.2 feet is the worst outcome
            raise UnknownUnit(f"no conversion from '{unit}' to {key} "
                              f"({CANONICAL.get(key, ('', '?'))[1]}); add it to _UNIT_TO_CANON")
        mult = _UNIT_TO_CANON.get((key, unit), 1.0) if unit else 1.0
        val = num * mult
        if key in ("generators", "circuits", "lines", "tracks", "beds", "year_built", "capacity_persons", "population", "staff"):
            return int(round(val))
        return round(val, 4)
    s = str(raw).strip()
    return s if s and s.lower() not in ("null", "none", "n/a") else None


def normalize_props(props: dict, spec: Optional[dict] = None) -> Dict[str, Any]:
    """Return the canonical dict for one feature (does not mutate props)."""
    spec_fields = (spec or {}).get("fields") or {}
    out: Dict[str, Any] = {}
    keys = list(CANONICAL)
    for key in keys:
        cfg = spec_fields.get(key)
        if cfg is False:
            continue
        if isinstance(cfg, dict) and "const" in cfg:
            out[key] = cfg["const"]
            continue
        cands = _candidates(key, spec_fields)
        nulls = cfg.get("nulls") if isinstance(cfg, dict) else None
        found = False
        # exact spelling first (OSM keys are lowercase and collide with nothing),
        # then case-insensitive, then ignoring punctuation ("Dam Name" ~ DAM_NAME)
        for strictness, getter in enumerate((lambda p, f: p.get(f), _get_ci, _get_loose)):
            for field, unit, exact_only in cands:
                if exact_only and strictness > 0:
                    continue                      # lowercase OSM tag: exact spelling only
                raw = getter(props, field)
                if raw in (None, "") or _is_null(raw, nulls):
                    continue
                try:
                    val = _convert(key, raw, unit)
                except UnknownUnit:
                    raise
                if val is not None:
                    out[key] = val
                    found = True
                    break
            if found:
                break
    # derived niceties
    if "voltage_kv" not in out and "max_voltage_kv" in out:
        out["voltage_kv"] = out["max_voltage_kv"]
    if "status" in out and str(out["status"]).lower() in ("yes",) and "disused" in props:
        out["status"] = "disused"
    return out


def apply_to_layer(result: LayerResult, spec: Optional[dict] = None) -> LayerResult:
    """Attach canonical values to every feature under `properties` (canonical
    keys win; a colliding raw key is preserved as src_<key>)."""
    for f in result.features:
        props = f.properties or {}
        canon = normalize_props(props, spec)
        for k, v in canon.items():
            if k in props and props[k] != v:
                props[f"src_{k}"] = props[k]
            props[k] = v
        f.properties = props
    return result


def fmt_value(key: str, v: Any) -> str:
    label, unit, fmt = CANONICAL.get(key, (key, "", "{}"))
    if v is None:
        return ""
    try:
        s = fmt.format(v) if isinstance(v, (int, float)) and fmt != "{}" else str(v)
    except (ValueError, TypeError):
        s = str(v)
    return f"{s} {unit}".strip()


# Which canonical keys headline each layer (order matters). Others fall to the
# "more" section. Unknown layers get GENERIC.
HEADLINES: Dict[str, List[str]] = {
    "power_plants": ["capacity_mw", "nameplate_mw", "fuel", "technology", "generators", "operator", "status", "start_date", "source_id"],
    "generators": ["capacity_mw", "fuel", "technology", "operator", "status", "start_date"],
    "substations": ["max_voltage_kv", "min_voltage_kv", "voltage_kv", "lines", "type", "operator", "owner", "status"],
    "transmission_lines": ["voltage_kv", "volt_class", "circuits", "owner", "operator", "status", "type"],
    "power_towers": ["type", "structure_type", "height_ft", "operator"],
    "battery_storage": ["capacity_mw", "capacity_mwh", "technology", "operator", "status"],
    "pipelines": ["substance", "type", "diameter_in", "pressure", "operator", "status"],
    "compressor_stations": ["horsepower", "operator", "status", "type"],
    "gas_processing": ["capacity_mmcfd", "operator", "status"],
    "refineries": ["capacity_bpd", "operator", "owner", "status"],
    "fuel_terminals": ["capacity_bpd", "substance", "operator", "type"],
    "ethanol_plants": ["capacity_mgy", "operator", "status"],
    "dams": ["height_ft", "storage_acre_ft", "hazard_class", "condition", "purpose", "year_built", "owner", "type"],
    "water_treatment": ["flow_mgd", "population_served", "operator", "type", "status"],
    "wastewater_treatment": ["flow_mgd", "population_served", "operator", "type", "status", "source_id"],
    "water_towers": ["type", "height_ft", "operator"],
    "comm_towers": ["height_ft", "structure_type", "type", "owner", "operator", "source_id"],
    "data_centers": ["operator", "type", "address"],
    "hospitals": ["beds", "trauma", "helipad", "emergency", "staff", "type", "owner", "status", "phone", "address"],
    "nursing_homes": ["beds", "population", "staff", "type", "owner", "phone", "address"],
    "shelters": ["capacity_persons", "type", "status", "address"],
    "correctional": ["capacity_persons", "population", "security_level", "type", "status", "owner"],
    "schools": ["type", "population", "address", "phone"],
    "pharmacies": ["type", "address", "phone"],
    "psap": ["name", "county", "state"],
    "rail_crossings": ["type", "owner", "source_id"],
    "fire_stations": ["type", "operator", "address", "phone"],
    "police": ["type", "population", "operator", "address", "phone"],
    "ems": ["type", "operator", "address", "phone"],
    "eoc": ["type", "operator", "address", "phone"],
    "airports": ["type", "runway_ft", "elevation_ft", "owner", "source_id"],
    "railways": ["owner", "operator", "tracks", "type", "status"],
    "rail_facilities": ["type", "owner", "operator"],
    "bridges": ["year_built", "condition", "adt", "length_ft", "type", "source_id"],
    "ports": ["type", "owner", "operator"],
}
GENERIC = ["type", "capacity_mw", "voltage_kv", "operator", "owner", "status", "address"]


def headline(props: dict, layer_key: str) -> List[Tuple[str, str]]:
    keys = HEADLINES.get(layer_key, GENERIC)
    rows = []
    for k in keys:
        if k in props and props[k] not in (None, ""):
            rows.append((CANONICAL[k][0], fmt_value(k, props[k])))
    return rows


_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SEPARATOR = re.compile(r"^(\s*[,;|]\s*|\s+[-/]\s+)$")
_GROUPS = re.compile(r"(\([^()]*\)|\[[^\[\]]*\])")


def _render_run(text: str, values: dict) -> str:
    """Substitute a template run, dropping any comma/dash-delimited piece whose
    placeholder resolved to nothing - so "{name} ({beds} beds)" with no bed count
    yields "St Marys", never "St Marys ( beds)"."""
    pieces = re.split(r"(\s*[,;|]\s*|\s+[-/]\s+)", text)
    out: List[str] = []
    skip_next_sep = False
    for piece in pieces:
        if piece == "":
            continue
        if _SEPARATOR.match(piece):
            if skip_next_sep or not out:       # dangling separator from a dropped piece
                skip_next_sep = False
                continue
            out.append(piece)
            continue
        names = _PLACEHOLDER.findall(piece)
        if names and any(str(values.get(n, "")).strip() == "" for n in names):
            if out and _SEPARATOR.match(out[-1]):
                out.pop()                      # drop the separator that introduced it
            else:
                skip_next_sep = True           # ...or the one that follows it
            continue
        skip_next_sep = False
        out.append(_PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), piece))
    while out and _SEPARATOR.match(out[-1]):
        out.pop()
    return "".join(out)


def render_name_template(tmpl: str, values: dict) -> str:
    """Render a `name:` template. Bracketed groups vanish entirely when nothing
    inside them has a value."""
    parts = []
    for chunk in _GROUPS.split(tmpl):
        if not chunk:
            continue
        if _GROUPS.fullmatch(chunk):
            inner = _render_run(chunk[1:-1], values)
            if re.search(r"[A-Za-z0-9]", inner):
                parts.append(chunk[0] + inner + chunk[-1])
        else:
            parts.append(_render_run(chunk, values))
    return re.sub(r"\s{2,}", " ", "".join(parts)).strip(" -|,;/")


def feature_name(props: dict, spec: Optional[dict] = None, layer_key: str = "") -> str:
    tmpl = (spec or {}).get("name")
    lead = re.match(r"\s*\{([A-Za-z_][A-Za-z0-9_]*)\}", tmpl or "")
    if lead and str((props or {}).get(lead.group(1), "")).strip() == "":
        tmpl = None          # the headline field is missing: use the fallback name instead
    if tmpl:
        values = {}
        for k, v in (props or {}).items():
            values[k] = fmt_value(k, v) if k in CANONICAL and k != "name" else ("" if v is None else v)
        try:
            rendered = render_name_template(tmpl, values)
        except Exception:  # noqa: BLE001  a bad template must never break a build
            rendered = ""
        if rendered:
            return rendered
    nm = props.get("name")
    if nm in (None, ""):
        for cand in DEFAULT_FROM["name"]:          # un-normalized features: scan raw names
            raw = props.get(cand)
            if raw not in (None, ""):
                nm = raw
                break
    if nm not in (None, ""):
        cap = props.get("capacity_mw")
        kv = props.get("voltage_kv")
        if cap not in (None, "") and layer_key in ("power_plants", "generators", "battery_storage"):
            return f"{nm} ({fmt_value('capacity_mw', cap)})"
        if kv not in (None, "") and layer_key in ("transmission_lines", "substations"):
            return f"{nm} ({fmt_value('voltage_kv', kv)})"
        return str(nm)
    for k in list(HEADLINES.get(layer_key, ())) + ["voltage_kv", "capacity_mw", "type", "source_id"]:
        if props.get(k) not in (None, ""):
            return fmt_value(k, props[k])
    return ""
