"""FCC Antenna Structure Registration (ASR) driver - registered towers, US-wide.

The FCC publishes the complete ASR database weekly as a zip of pipe-delimited,
header-less tables (https://data.fcc.gov/download/pub/uls/complete/r_tower.zip).
Three tables matter for a map:

  RA.dat  registration: structure type, heights, address, status
  CO.dat  coordinates in DMS (one row per structure; arrays have several)
  EN.dat  entities: owner / contact names

All three key on `unique_system_identifier` (column 4) and
`registration_number` (column 3). Column positions follow the FCC "ASR Public
Access Database Definitions"; they are configurable in the spec (`layout:`)
in case the FCC re-orders a table.

Spec:
    - layer: comm_towers
      driver: fcc_asr
      url: https://data.fcc.gov/download/pub/uls/complete/r_tower.zip   # default
      status_codes: [C, G]     # C=constructed, G=granted (default: constructed only)
      layout: {ra_state: 29}   # optional column overrides

Public domain (US Government). ~120 MB download, cached on disk.
"""
import io
import zipfile
from typing import Dict, List

from ..model import Feature, LayerResult, Provenance
from ._csv import dms_to_dd
from .base import Context, driver, http_get, today

DEFAULT_URL = "https://data.fcc.gov/download/pub/uls/complete/r_tower.zip"

# Column indices (0-based) in the pipe-delimited tables.
LAYOUT = {
    "usi": 4, "reg": 3,
    # RA.dat
    "ra_status": 8, "ra_date_constructed": 12, "ra_date_dismantled": 13,
    "ra_structure_type": 22, "ra_height_structure": 23, "ra_ground_elev": 24,
    "ra_height_agl": 25, "ra_height_amsl": 26, "ra_address": 27, "ra_city": 28,
    "ra_state": 29, "ra_zip": 30, "ra_faa_study": 36, "ra_paint_light": 39,
    # CO.dat
    "co_type": 5, "co_lat_d": 6, "co_lat_m": 7, "co_lat_s": 8, "co_lat_h": 9,
    "co_lon_d": 11, "co_lon_m": 12, "co_lon_s": 13, "co_lon_h": 14,
    "co_array_pos": 16, "co_array_total": 17,
    # EN.dat
    "en_contact_type": 5, "en_entity_type": 6, "en_name": 7,
}
STRUCTURE_TYPES = {
    "B": "Building", "BANT": "Building with antenna", "BMAST": "Building with mast",
    "BPIPE": "Building with pipe", "BPOLE": "Building with pole", "BRIDG": "Bridge",
    "BTWR": "Building with tower", "GTOWER": "Guyed tower", "LTOWER": "Lattice tower",
    "MAST": "Mast", "MTOWER": "Monopole", "NNGTANN": "Guyed tower array",
    "NNLTANN": "Lattice tower array", "NNMTANN": "Monopole array", "PIPE": "Pipe",
    "POLE": "Pole", "RIG": "Oil rig", "SIGN": "Sign", "SILO": "Silo", "STACK": "Smoke stack",
    "TANK": "Tank", "TOWER": "Tower", "TREE": "Tree", "UPOLE": "Utility pole",
}
STATUS = {"C": "Constructed", "G": "Granted", "T": "Terminated", "D": "Dismantled",
          "A": "Application", "P": "Pending", "I": "Incomplete"}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_tables(zip_bytes: bytes, layout: Dict[str, int], want_states: set,
                 status_codes: set) -> List[Feature]:
    L = dict(LAYOUT)
    L.update(layout or {})
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = {n.upper(): n for n in zf.namelist()}

    def rows(member):
        with zf.open(names[member]) as fh:
            for line in io.TextIOWrapper(fh, encoding="latin-1", errors="replace"):
                yield line.rstrip("\r\n").split("|")

    ra: Dict[str, dict] = {}
    for r in rows("RA.DAT"):
        if len(r) <= L["ra_state"]:
            continue
        st = r[L["ra_state"]].strip().upper()
        if want_states and st not in want_states:
            continue
        status = r[L["ra_status"]].strip().upper()
        if status_codes and status not in status_codes:
            continue
        code = r[L["ra_structure_type"]].strip().upper()
        ra[r[L["usi"]]] = {
            "registration_number": r[L["reg"]].strip(),
            "structure_type_code": code,
            "structure_type": STRUCTURE_TYPES.get(code, code),
            "status": STATUS.get(status, status),
            "height_agl_m": _f(r[L["ra_height_agl"]]),
            "height_amsl_m": _f(r[L["ra_height_amsl"]]),
            "ground_elev_m": _f(r[L["ra_ground_elev"]]),
            "date_constructed": r[L["ra_date_constructed"]].strip(),
            "address": r[L["ra_address"]].strip(), "city": r[L["ra_city"]].strip(),
            "state": st, "zip": r[L["ra_zip"]].strip(),
            "faa_study": r[L["ra_faa_study"]].strip() if len(r) > L["ra_faa_study"] else "",
            "paint_light": r[L["ra_paint_light"]].strip() if len(r) > L["ra_paint_light"] else "",
        }
    if "EN.DAT" in names:
        for r in rows("EN.DAT"):
            if len(r) <= L["en_name"]:
                continue
            usi = r[L["usi"]]
            if usi in ra and r[L["en_contact_type"]].strip().upper() in ("O", "") and not ra[usi].get("owner"):
                ra[usi]["owner"] = r[L["en_name"]].strip()
    feats: List[Feature] = []
    for r in rows("CO.DAT"):
        if len(r) <= L["co_lon_h"]:
            continue
        usi = r[L["usi"]]
        if usi not in ra:
            continue
        lat = dms_to_dd(r[L["co_lat_d"]], r[L["co_lat_m"]], r[L["co_lat_s"]], r[L["co_lat_h"]])
        lon = dms_to_dd(r[L["co_lon_d"]], r[L["co_lon_m"]], r[L["co_lon_s"]], r[L["co_lon_h"]])
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        props = dict(ra[usi])
        props["unique_system_identifier"] = usi
        if len(r) > L["co_array_total"] and r[L["co_array_total"]].strip() not in ("", "0", "1"):
            props["array_position"] = f"{r[L['co_array_pos']].strip()} of {r[L['co_array_total']].strip()}"
        props["fcc_url"] = f"https://wireless2.fcc.gov/UlsApp/AsrSearch/asrRegistration.jsp?regKey={usi}"
        feats.append(Feature({"type": "Point", "coordinates": [lon, lat]}, props))
    return feats


@driver("fcc_asr")
def fetch(logical: str, spec: dict, ctx: Context) -> LayerResult:
    url = spec.get("url", DEFAULT_URL)
    if ctx.aoi.country != "US":
        raise RuntimeError("fcc_asr covers US structures only")
    states = set(a.upper() for a in ctx.aoi.state_abbrs) if ctx.aoi.kind in ("county", "state", "region") else set()
    codes = set(c.upper() for c in spec.get("status_codes", ["C"]))
    raw = http_get(url, timeout=1800)
    feats = parse_tables(raw, spec.get("layout") or {}, states, codes)
    prov = Provenance(
        source_name=spec.get("source_name", "FCC Antenna Structure Registration (ASR)"),
        source_url=url, license=spec.get("license", "Public domain (US FCC)"),
        retrieved=today(), driver="fcc_asr",
        notes=(spec.get("notes", "") + " | heights in meters converted to ft via fields mapping; "
               "registration covers structures >200 ft AGL or near airports").strip(" |"))
    return LayerResult(logical, feats, prov, spec.get("group_by"), len(feats))
