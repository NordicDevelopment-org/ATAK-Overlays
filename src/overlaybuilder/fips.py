"""FIPS resolution. Everything keys on state+county FIPS (the universal ID).

State FIPS are embedded (static, public domain). County FIPS are resolved from
the Census national county file, downloaded once and cached, so we don't ship a
3,000-row table. --fips bypasses county-name lookup entirely.
"""
import os
from typing import Optional, Tuple

from .http import http_get

# abbr -> (state_fp, full name)
STATES = {
    "AL": ("01", "Alabama"), "AK": ("02", "Alaska"), "AZ": ("04", "Arizona"),
    "AR": ("05", "Arkansas"), "CA": ("06", "California"), "CO": ("08", "Colorado"),
    "CT": ("09", "Connecticut"), "DE": ("10", "Delaware"), "DC": ("11", "District of Columbia"),
    "FL": ("12", "Florida"), "GA": ("13", "Georgia"), "HI": ("15", "Hawaii"),
    "ID": ("16", "Idaho"), "IL": ("17", "Illinois"), "IN": ("18", "Indiana"),
    "IA": ("19", "Iowa"), "KS": ("20", "Kansas"), "KY": ("21", "Kentucky"),
    "LA": ("22", "Louisiana"), "ME": ("23", "Maine"), "MD": ("24", "Maryland"),
    "MA": ("25", "Massachusetts"), "MI": ("26", "Michigan"), "MN": ("27", "Minnesota"),
    "MS": ("28", "Mississippi"), "MO": ("29", "Missouri"), "MT": ("30", "Montana"),
    "NE": ("31", "Nebraska"), "NV": ("32", "Nevada"), "NH": ("33", "New Hampshire"),
    "NJ": ("34", "New Jersey"), "NM": ("35", "New Mexico"), "NY": ("36", "New York"),
    "NC": ("37", "North Carolina"), "ND": ("38", "North Dakota"), "OH": ("39", "Ohio"),
    "OK": ("40", "Oklahoma"), "OR": ("41", "Oregon"), "PA": ("42", "Pennsylvania"),
    "RI": ("44", "Rhode Island"), "SC": ("45", "South Carolina"), "SD": ("46", "South Dakota"),
    "TN": ("47", "Tennessee"), "TX": ("48", "Texas"), "UT": ("49", "Utah"),
    "VT": ("50", "Vermont"), "VA": ("51", "Virginia"), "WA": ("53", "Washington"),
    "WV": ("54", "West Virginia"), "WI": ("55", "Wisconsin"), "WY": ("56", "Wyoming"),
    "AS": ("60", "American Samoa"), "GU": ("66", "Guam"),
    "MP": ("69", "Northern Mariana Islands"), "PR": ("72", "Puerto Rico"),
    "VI": ("78", "US Virgin Islands"),
}
_FP_TO_ABBR = {fp: abbr for abbr, (fp, _) in STATES.items()}
_GAZETTEER = "https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt"


def state_fp(state: str) -> str:
    s = state.strip().upper()
    if s in STATES:
        return STATES[s][0]
    for abbr, (fp, name) in STATES.items():
        if name.upper() == s:
            return fp
    if s.isdigit() and s.zfill(2) in _FP_TO_ABBR:
        return s.zfill(2)
    raise KeyError(f"unknown state '{state}'")


def abbr_for_fp(fp: str) -> str:
    return _FP_TO_ABBR.get(fp, "")


def _gazetteer(cache_dir: str, tries: int = 4) -> str:
    """The Census county file, downloaded once. Written atomically: a failed or
    truncated download must not leave an empty file that later reads as a
    successful lookup with no counties in it."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "national_county2020.txt")
    if not os.path.exists(path) or os.path.getsize(path) < 1024:
        body = http_get(_GAZETTEER, cache=False, tries=tries, timeout=60)
        if len(body) < 1024 or b"|" not in body[:4096]:
            raise RuntimeError(f"the Census county file at {_GAZETTEER} did not look like the gazetteer")
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(body)
        os.replace(tmp, path)
    with open(path, encoding="latin-1") as fh:
        return fh.read()


def _norm(name: str) -> str:
    n = name.strip().lower()
    for suf in (" county", " parish", " borough", " census area",
                " municipality", " city and borough", " city"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    return n.strip()


def resolve(state: str, county: Optional[str] = None,
            fips: Optional[str] = None, cache_dir: str = ".cache") -> Tuple[str, str, str, str]:
    """Return (state_fp, county_fp, county_name, state_abbr).

    Provide either fips=SSCCC, or state + county name.
    """
    if fips:
        f = fips.strip()
        if len(f) != 5 or not f.isdigit():
            raise ValueError("fips must be 5 digits (state 2 + county 3)")
        sfp, cfp = f[:2], f[2:]
        abbr = abbr_for_fp(sfp)
        if not abbr:
            raise ValueError(f"'{f}' does not start with a known state FIPS "
                             f"(got '{sfp}'); see overlaybuilder list-regions or a FIPS table")
        if county:
            name = county
        else:
            try:
                name = _lookup_name(sfp, cfp, cache_dir, tries=1)
            except Exception:  # noqa: BLE001
                # the county NAME is cosmetic (labels and the output folder);
                # never let a gazetteer download stop a build that has a FIPS
                name = ""
        return sfp, cfp, name, abbr

    sfp = state_fp(state)
    abbr = abbr_for_fp(sfp)
    if not county:
        raise ValueError("need a county name (or pass fips=SSCCC)")
    target = _norm(county)
    try:
        gaz = _gazetteer(cache_dir)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"cannot look up '{county}, {abbr}' without the Census county file ({e}). "
            f"Use the FIPS directly instead, e.g. --aoi county:27025") from None
    for line in gaz.splitlines()[1:]:
        parts = line.split("|")
        if len(parts) < 5:
            continue
        _, statefp, countyfp, _, countyname = parts[:5]
        if statefp == sfp and _norm(countyname) == target:
            return sfp, countyfp.zfill(3), countyname, abbr
    raise KeyError(f"county '{county}' not found in state {abbr}")


def _lookup_name(sfp: str, cfp: str, cache_dir: str, tries: int = 4) -> str:
    for line in _gazetteer(cache_dir, tries=tries).splitlines()[1:]:
        parts = line.split("|")
        if len(parts) < 5:
            continue
        if parts[1] == sfp and parts[2].zfill(3) == cfp:
            return parts[4]
    return ""
