#!/usr/bin/env python3
"""
build_county_pack.py - county-boundary KMZ packages for ATAK, one per US state.

WHAT IT DOES
------------
1. Downloads county polygons for a state from the US Census TIGERweb REST API
   (public domain, national coverage - every state and territory, same call).
2. Downloads population and housing-unit counts for those counties from the
   Census ACS 5-year API, which returns an exact figure with a known vintage.
3. Reads land area straight out of the TIGER attribute ALAND (square metres of
   LAND, water excluded) - not from a projected shape area, which is distorted.
4. Optionally reads county seat and sheriff / primary law-enforcement contact
   from CSV files in ./data, which ship mostly empty on purpose (see DATA
   INTEGRITY below).
5. Writes ONE KMZ per state containing every county as an outline placemark
   with all of the above in its popup, each value stamped with the year it
   came from.

WHY IT IS WRITTEN THIS WAY
--------------------------
* STANDARD LIBRARY ONLY. It runs on Termux on an Android phone, where
  `pip install pyproj` needs a C toolchain and PROJ and generally ruins the
  evening. urllib + json + zipfile are already there. Nothing to install but
  Python itself.
* ONE ENDPOINT FOR ALL 50 STATES. A per-state GIS server (mn geo, tx tnris,
  ...) means 50 different schemas. TIGERweb is one schema, nationwide.
* EVERY VALUE CARRIES ITS YEAR. See DATA INTEGRITY.

DATA INTEGRITY - THE RULE THIS FILE IS BUILT AROUND
---------------------------------------------------
A value is written only when a source actually returned it. Nothing is
estimated, rounded from memory, or filled in to look complete. A field with no
source renders as "not in dataset" - which is information. A plausible-looking
invented number is not information, it is a liability on a map someone may
act on.

Every rendered value is followed by its source and vintage, e.g.

    Population: 58,241  [ACS 5-year 2023]
    Land area: 414.2 sq mi  [TIGER 2024 ALAND]

so a pack found on a device two years from now can be judged on its age
without opening anything else.

USAGE
-----
    python3 build_county_pack.py --state MN
    python3 build_county_pack.py --state MN --out /sdcard/Download/atak_packs
    python3 build_county_pack.py --all-states          # every state, one KMZ each
    python3 build_county_pack.py --state MN --per-county   # also 1 KMZ per county
    python3 build_county_pack.py --probe               # check endpoints are alive

Exit codes: 0 ok, 1 nothing built, 2 network/endpoint failure.
"""

import argparse
import csv
import json
import os
import re
import ssl
import sys
import time
import zipfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

# --------------------------------------------------------------------------
# Endpoints. Override any of these from the command line if Census moves them;
# --probe tells you which ones answer before you spend a download on them.
# --------------------------------------------------------------------------

# TIGERweb county polygons, layer 1. VERIFIED on-device 2026-09-14: returns
# exactly 87 Minnesota counties with GEOID, NAME, BASENAME, AREALAND, AREAWATER.
#
# The service carries 71 layers because it stacks four VINTAGES - the top level
# (Current), then groups "BAS 2026", "ACS 2025" and "Census 2020" - and within
# each vintage the same counties appear at several GENERALIZATION levels for
# different map scales. Layer 1 is the first Counties layer of the current
# vintage. Run tiger_diagnose.py to see the whole tree, and --detail to compare
# how much boundary detail each generalization level actually carries.
TIGERWEB = ("https://tigerweb.geo.census.gov/arcgis/rest/services"
            "/TIGERweb/State_County/MapServer/1")
# Fallbacks tried in order if the primary will not answer. Different vintages
# of the same public-domain data; whichever answers is recorded as the source.
TIGERWEB_ALTERNATES = [
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0",
    "https://tigerweb.geo.census.gov/arcgis/rest/services/Basemaps/CBSA/MapServer/6",
]

# A wall-clock budget for ONE url, retries included. Without it the retry count
# and the socket timeout multiply: four tries at 120s is eight minutes on a
# single url, and falling through three endpoints made 24 minutes of a command
# that printed nothing while it waited. Verified by measurement, not guessed.
HTTP_BUDGET_S = 150

# ACS 5-year detailed tables.
#   B01003_001E = total population
#   B25001_001E = total housing units
#
# A KEY IS REQUIRED. Verified on-device 2026-09-14: a keyless request returns
# HTTP 200 with an HTML page titled "Missing Key", not an error status and not
# JSON - which is why it first surfaced as a json decode failure. The key is
# free and instant: https://api.census.gov/data/key_signup.html
ACS_YEAR = 2023
ACS_BASE = "https://api.census.gov/data/{year}/acs/acs5"
ACS_VARS = {"population": "B01003_001E", "housing_units": "B25001_001E"}
ACS_KEY_SIGNUP = "https://api.census.gov/data/key_signup.html"
# Kept outside the repo so a key can never be committed by accident.
KEY_FILE = os.path.join(os.path.expanduser("~"), ".config",
                        "atak-statepacks", "census_key")

# The boundary vintage is READ FROM THE SERVICE at build time (see
# service_vintage). This is only the fallback label used when the service does
# not report a year anywhere - in which case the popup says so instead of
# asserting a year nobody returned.
TIGER_VINTAGE_UNKNOWN = "vintage not reported"
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
SQ_M_PER_SQ_MI = 2589988.110336  # exact, by definition of the survey mile
# Provenance grey. Mid-grey reads on both the light and dark popup backgrounds
# ATAK uses, where a lighter grey washes out on white.
GREY = "#808080"

SSL_CTX = ssl.create_default_context()
UA = {"User-Agent": "atak-statepacks/1.0 (+https://github.com/NordicDevelopment-org/ATAK-Overlays)"}

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")

# FIPS state codes. Needed because the Census API addresses states by number.
STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15",
    "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56", "PR": "72",
}
FIPS_STATE = {v: k for k, v in STATE_FIPS.items()}


# --------------------------------------------------------------------------
# A value plus where it came from and when. Everything user-visible is one of
# these, so a popup can never show a number with no provenance attached.
# --------------------------------------------------------------------------
class Sourced:
    __slots__ = ("value", "source", "vintage")

    def __init__(self, value=None, source="", vintage=""):
        self.value = value
        self.source = source
        self.vintage = vintage

    def __bool__(self):
        return self.value is not None and self.value != ""

    def render(self, fmt=None):
        """'58,241  [ACS 5-year 2023]' as plain text, for logs and tests.

        An absent value renders as nothing at all - not as a bare provenance
        tag, which would show a source next to no number.
        """
        if not self:
            return ""
        v, tag = self.parts(fmt)
        return f"{v}  [{tag}]" if tag else v

    def parts(self, fmt=None):
        """(value, 'source vintage') kept SEPARATE so each can be styled.

        The popup renders the value bold and the provenance grey, which means
        they cannot be escaped as one string - the markup would be escaped with
        them.
        """
        if not self:
            # No value means no tag either. A source and year next to nothing
            # would render as provenance for a number that was never there.
            return "", ""
        v = fmt(self.value) if fmt else str(self.value)
        tag = " ".join(x for x in (self.source, self.vintage) if x)
        return v, tag


# --------------------------------------------------------------------------
# HTTP. Retries with backoff; never silently returns a partial body.
# --------------------------------------------------------------------------
_SECRET_PARAM = re.compile(r"([?&](?:key|api_key|token)=)[^&\s]+", re.I)


def redact(text):
    """Strip API keys out of anything that might be printed, logged or pasted.

    A failed request used to raise with the full URL in the message, and the
    diagnostics print every URL they try - so a key in a query string ends up in
    console output that people copy into chats and bug reports. The key is the
    one part of a URL that must never travel with it.
    """
    return _SECRET_PARAM.sub(r"\1<redacted>", str(text))


def http_get(url, params=None, tries=3, timeout=60, budget=None, log=print):
    """One request, retried, under a WALL-CLOCK BUDGET and never silently.

    Retries multiply: four tries at a 120s socket is eight minutes on ONE url,
    and with three endpoints to fall through that was 24 minutes of a command
    printing nothing. `budget` caps the total time spent on this url no matter
    how the tries and the socket timeout multiply out, each socket is clamped
    to what the budget has left, and every retry says which host stalled and
    how long is left - so a slow fetch reads as slow instead of hung.
    """
    # Resolved at call time, not bound as a default, so --http-budget can raise
    # it on a slow link without every caller having to pass it through.
    budget = HTTP_BUDGET_S if budget is None else budget
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    host = urlsplit(url).netloc or redact(url)
    start = time.monotonic()
    last, used = None, 0
    for i in range(tries):
        left = budget - (time.monotonic() - start)
        if left <= 0:
            break
        used = i + 1
        try:
            sock = max(5, min(timeout, int(left)))
            with urlopen(Request(url, headers=UA), timeout=sock, context=SSL_CTX) as r:
                return r.read()
        except (URLError, HTTPError, TimeoutError, OSError) as e:
            last = e
            wait = 1.5 * (i + 1)
            left = budget - (time.monotonic() - start)
            if i >= tries - 1 or left < wait:
                break
            log(f"    [!] {host} did not answer ({redact(e)}); retry "
                f"{i + 2}/{tries} in {wait:.0f}s ({left:.0f}s of budget left)")
            time.sleep(wait)
    spent = time.monotonic() - start
    raise RuntimeError(
        f"GET failed after {used} tr{'y' if used == 1 else 'ies'} and "
        f"{spent:.0f}s (budget {budget}s): {redact(url)}\n  {redact(last)}")


def get_json(url, params=None, **kw):
    return json.loads(http_get(url, params, **kw).decode("utf-8", "replace"))


def census_key(explicit=None):
    """The Census API key: --census-key, then $CENSUS_API_KEY, then the key file.

    Returns "" when there is none, so the caller can say so plainly rather than
    firing off a request that comes back as an HTML page.
    """
    if explicit:
        return explicit.strip()
    env = os.environ.get("CENSUS_API_KEY", "").strip()
    if env:
        return env
    try:
        with open(KEY_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


class MissingCensusKey(RuntimeError):
    """Raised with instructions, not a stack trace, when no key is configured."""

    def __init__(self):
        super().__init__(
            "the Census API requires a key (a keyless request returns an HTML "
            f'page titled "Missing Key").\n'
            f"    Get one free, instantly: {ACS_KEY_SIGNUP}\n"
            f"    Then either:\n"
            f"      export CENSUS_API_KEY=your_key_here\n"
            f"      (add that line to ~/.bashrc to make it stick)\n"
            f"    or save it once:\n"
            f"      mkdir -p {os.path.dirname(KEY_FILE)} && "
            f"echo your_key_here > {KEY_FILE}\n"
            f"    or pass it per run:  --census-key your_key_here")


# --------------------------------------------------------------------------
# Fetch: county polygons
# --------------------------------------------------------------------------
def service_vintage(url, log=print):
    """The vintage of the layer at `url`, read from the service's own layer tree.

    TIGERweb stacks several vintages in one service and names them as GROUP
    layers - "BAS 2026", "ACS 2025", "Census 2020" - with the current vintage
    sitting at the top level. So a layer's vintage is its parent group's name,
    which is a fact the service states, not a year scraped out of prose.

    Returns TIGER_VINTAGE_UNKNOWN rather than guessing, so the popup can say
    "vintage not reported" instead of asserting a year nothing returned.
    """
    m = re.search(r"/(\d+)/?$", url)
    root = re.sub(r"/\d+/?$", "", url)
    try:
        info = get_json(root, {"f": "json"}, tries=2, timeout=30)
    except Exception as e:                          # noqa: BLE001
        log(f"    [!] could not read service metadata ({e}); vintage unknown")
        return TIGER_VINTAGE_UNKNOWN

    layers = {l.get("id"): l for l in (info.get("layers") or [])}
    if m and int(m.group(1)) in layers:
        lyr = layers[int(m.group(1))]
        parent = lyr.get("parentLayerId", -1)
        if parent in layers:
            return str(layers[parent].get("name") or "").strip() or TIGER_VINTAGE_UNKNOWN
        # top level of a service that groups its older vintages = the current one
        if any(l.get("subLayerIds") for l in layers.values()):
            return "Current"
    for key in ("name", "description", "serviceDescription", "copyrightText"):
        found = _YEAR_RE.search(str(info.get(key) or ""))
        if found:
            return found.group(0)
    return TIGER_VINTAGE_UNKNOWN


def shared_descriptor(names):
    """The word every name ends with, or "" when they do not share one.

    Louisiana has parishes, Puerto Rico municipios, Alaska boroughs and census
    areas. Titling all of them "Counties" states something the source does not:
    TIGER spells the descriptor out in NAME, and this reads it back rather than
    pluralising a word from a table of what states are supposed to call things.

    The descriptor must be a SUFFIX of every name and must never be the whole
    of any name. TWO DISTINCT names are required: one name shares a suffix with
    itself trivially, which is how "District of Columbia" alone yielded the
    descriptor "of Columbia" and a pack titled "DC of Columbia boundaries".
    """
    uniq = sorted({str(n).strip() for n in names if str(n or "").strip()})
    if len(uniq) < 2:
        return ""                        # one sample establishes no pattern
    parts = [n.split() for n in uniq]
    tail = []
    for i in range(1, min(len(p) for p in parts) + 1):
        word = parts[0][-i]
        if any(p[-i] != word for p in parts):
            break
        if any(len(p) <= i for p in parts):   # would swallow a whole name
            break
        tail.insert(0, word)
    return " ".join(tail)


def county_geoid(props):
    """The 5-digit state+county FIPS of a returned feature, or None.

    None means "this is not an identifiable county" - a state polygon, a metro
    area, a row with no identity columns. Two things depend on being strict
    here:

    * fetch_counties uses it to drop anything the server should not have sent.
      A 2-digit state GEOID shares its first two digits with every county in
      that state, so a looser check would let a whole state polygon through as
      if it were a county.
    * build_state uses it as the FIPS shown in the popup. It returns None
      rather than padding a blank out to "27000", which would be a FIPS code no
      source returned, stamped with TIGER's name.
    """
    p = {str(k).upper(): v for k, v in (props or {}).items()}
    geoid = str(p.get("GEOID") or p.get("GEOID20") or "").strip()
    if len(geoid) == 5 and geoid.isdigit():
        return geoid
    sfp = ""
    for key in ("STATE", "STATEFP", "STATE_FIPS"):
        v = p.get(key)
        if v not in (None, ""):
            sfp = str(v).strip().zfill(2)[:2]
            break
    cfp = ""
    for key in ("COUNTY", "COUNTYFP", "COUNTY_FIPS"):
        v = p.get(key)
        if v not in (None, ""):
            cfp = str(v).strip().zfill(3)[:3]
            break
    if len(sfp) == 2 and sfp.isdigit() and len(cfp) == 3 and cfp.isdigit():
        return sfp + cfp
    return None


def county_label(props):
    """The county's name as its own source spells it.

    TIGER's NAME already carries the legal descriptor - "Chisago County",
    "Acadia Parish", "Nome Census Area", "Alexandria city", "Adjuntas
    Municipio". Appending a literal " County" to it produces "Chisago County
    County" and, worse, relabels 15 states' jurisdictions as something they are
    not. BASENAME is the bare name, so only that one gets a suffix.
    """
    p = {str(k).upper(): v for k, v in (props or {}).items()}
    for key in ("NAMELSAD", "NAME"):
        v = p.get(key)
        if v:
            return str(v).strip()
    base = p.get("BASENAME")
    return f"{str(base).strip()} County" if base else "Unknown"


def fetch_counties(state_fips, endpoint=TIGERWEB, alternates=None, log=print):
    """Every county in one state, as GeoJSON features, paged.

    Returns (features, endpoint_actually_used, vintage). The endpoint is
    returned because if a fallback answered, the pack must say so rather than
    claim it came from the primary; the vintage is read from that service
    rather than hardcoded, for the same reason.
    """
    tried = [endpoint] + list(alternates or [])
    last_err = None
    for url in tried:
        try:
            feats, offset, guard = [], 0, 0
            while True:
                page = get_json(f"{url}/query", {
                    "where": f"STATE='{state_fips}'",
                    "outFields": "*",
                    "returnGeometry": "true",
                    "outSR": "4326",
                    "f": "geojson",
                    "resultOffset": offset,
                    "resultRecordCount": 1000,
                })
                if "error" in page:
                    raise RuntimeError(page["error"])
                batch = page.get("features") or []
                feats.extend(batch)
                # Page on the server's own signal, not on our requested size. A
                # service whose maxRecordCount is below 1000 returns a short
                # first page that is NOT the last one, and treating it as the
                # last silently truncates the state.
                more = bool(page.get("exceededTransferLimit")
                            or (page.get("properties") or {}).get("exceededTransferLimit"))
                guard += 1
                if not batch or not more:
                    break
                if guard > 20:      # a server ignoring resultOffset would loop forever
                    log(f"    [!] {redact(url)} kept reporting more results after "
                        f"{len(feats)} features; stopping")
                    break
                offset += len(batch)

            # Trust the server's data, not its filtering. A `where` clause that a
            # server ignores, mis-parses, or applies to a column that does not
            # exist can come back as EVERY county in the country - which would
            # quietly ship California inside MN_Counties.kmz. Keep only the
            # features that actually belong to the state we asked for.
            kept = [f for f in feats
                    if (county_geoid(f.get("properties")) or "").startswith(state_fips)]
            dropped = len(feats) - len(kept)
            if dropped:
                log(f"    [!] {redact(url)} returned {dropped} feature(s) that are not "
                    f"counties of state {state_fips} - the filter was not "
                    f"honoured; they were dropped")
            if kept:
                if url != endpoint:
                    log(f"    [!] primary endpoint failed; used fallback {url}")
                return kept, url, service_vintage(url, log=log)
            last_err = f"0 counties for state {state_fips} (of {len(feats)} returned)"
        except Exception as e:                      # noqa: BLE001 - try the next one
            last_err = e
            log(f"    [!] {redact(url)} -> {redact(e)}")
    raise RuntimeError(f"no county endpoint answered for state {state_fips}: {last_err}")


# --------------------------------------------------------------------------
# Fetch: population and housing units, with an explicit vintage
# --------------------------------------------------------------------------
def fetch_acs(state_fips, year=ACS_YEAR, log=print, key=None):
    """{county_fips3: {"population": Sourced, "housing_units": Sourced}}.

    Returns {} and logs if ACS will not answer - the pack is still worth
    building with the boundaries and land areas, it just says so in the popup.
    """
    k = census_key(key)
    if not k:
        log(f"    [!] {MissingCensusKey()}")
        log("    population and housing will read 'not in dataset'")
        return {}

    url = ACS_BASE.format(year=year)
    params = {
        "get": "NAME," + ",".join(ACS_VARS.values()),
        "for": "county:*",
        "in": f"state:{state_fips}",
        "key": k,
    }
    try:
        raw = http_get(url, params).decode("utf-8", "replace")
    except Exception as e:                          # noqa: BLE001
        log(f"    [!] ACS {year} unavailable ({redact(e)}); population/housing omitted")
        return {}

    # The API answers a bad or missing key with HTTP 200 and an HTML page, so a
    # plain json.loads would report a decode error and hide the real cause.
    head = raw.lstrip()[:400].lower()
    if head.startswith("<"):
        why = ("the key was rejected" if "invalid key" in head
               else "no key reached the API" if "missing key" in head
               else "the API returned an HTML page instead of data")
        log(f"    [!] ACS {year}: {why}. Check CENSUS_API_KEY, or get one at "
            f"{ACS_KEY_SIGNUP}")
        return {}
    try:
        rows = json.loads(raw)
    except ValueError as e:
        log(f"    [!] ACS {year} returned unparseable data ({e}); omitted")
        return {}

    # The docstring promises this degrades rather than kills the pack, so the
    # parse has to be as guarded as the fetch: a short body, a renamed column or
    # a variable the year does not carry would otherwise raise out of here.
    if (not isinstance(rows, list) or not rows or not isinstance(rows[0], list)
            or "county" not in rows[0]
            or not all(v in rows[0] for v in ACS_VARS.values())):
        log(f"    [!] ACS {year} returned an unexpected shape; "
            f"population/housing omitted")
        return {}
    header, out = rows[0], {}
    idx = {name: header.index(var) for name, var in ACS_VARS.items()}
    cty_i = header.index("county")
    src, vint = "ACS 5-year", str(year)
    for row in rows[1:]:
        rec = {}
        for name in ACS_VARS:
            raw = row[idx[name]]
            try:                        # ACS uses negative sentinels for suppressed
                n = int(raw)
                rec[name] = Sourced(n if n >= 0 else None, src, vint)
            except (TypeError, ValueError):
                rec[name] = Sourced(None, src, vint)
        out[row[cty_i]] = rec
    return out


# --------------------------------------------------------------------------
# Local CSV enrichment: county seat, sheriff / primary LE.
#
# These ship EMPTY or partial and that is deliberate. There is no authoritative
# federal machine-readable file of county seats or sheriff non-emergency
# numbers. Rather than invent them, the pack renders "not in dataset" and you
# fill the CSV from a source you trust, recording that source and its year in
# the row so the popup can show it.
# --------------------------------------------------------------------------
def load_csv_table(filename):
    """{geoid: {column: value}} from data/<filename>, then data/<stem>.local.csv.

    The shipped file is tracked in git and ships empty. Anything you fetch or
    verify lands in the .local.csv beside it, which is gitignored - so a `git
    pull` can never conflict with your data, and your data can never be wiped
    by a pull. Local rows win over shipped ones for the same county.
    """
    out = {}
    stem = filename[:-4] if filename.endswith(".csv") else filename
    for name in (filename, f"{stem}.local.csv"):
        out.update(_read_csv_table(os.path.join(DATA_DIR, name)))
    return out


def _read_csv_table(path):
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        # The shipped CSVs lead with a "#" comment block explaining what they
        # are for. csv.DictReader has no notion of comments, so it would adopt
        # the first comment line as the one and only field name and then drop
        # every real row on the floor - silently, because a dropped row just
        # renders as "not in dataset" like any other absence.
        body = [line for line in fh if not line.lstrip().startswith("#")]
    for row in csv.DictReader(body):
        geoid = (row.get("geoid") or "").strip()
        if geoid:
            out[geoid] = {k: (v or "").strip() for k, v in row.items() if k}
    return out


def sourced_from_row(row, field):
    if not row or not row.get(field):
        return Sourced()
    return Sourced(row[field], row.get("source", ""), row.get("vintage", ""))


# --------------------------------------------------------------------------
# KML
# --------------------------------------------------------------------------
# XML 1.0 forbids these outright - they cannot be escaped, only removed. One of
# them anywhere in a name or a CSV value makes the whole doc.kml unparseable, so
# ATAK rejects the entire pack rather than the one bad field.
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def esc(s):
    s = _XML_ILLEGAL.sub("", str(s))            # tab/newline/CR are legal, kept
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def rings_of(geom):
    """EVERY ring of a Polygon or MultiPolygon, outer and inner.

    The naive version of this takes coordinates[0] and throws the rest away.
    That silently drops holes, and drops every polygon after the first in a
    MultiPolygon - so a county with an exclave or islands (Lake of the Woods
    and its Northwest Angle, island counties, anything split by water) renders
    as a lie about its own shape.
    """
    if not geom:
        return []
    t, c = geom.get("type"), geom.get("coordinates") or []
    if t == "Polygon":
        return [r for r in c if r]
    if t == "MultiPolygon":
        return [r for poly in c for r in poly if r]
    return []


def county_placemark(props, geom, meta, precision=6):
    """One county: outline geometry + a popup table of sourced values."""
    name = meta["name"]
    rows = [
        ("County", Sourced(f"{name}, {meta['state_abbr']}", "TIGER", meta["tiger_vintage"])),
        ("FIPS (GEOID)", meta["geoid"]),
        ("County seat", meta["seat"]),
        ("Population", meta["population"]),
        ("Housing units", meta["housing_units"]),
        ("Land area", meta["land_area"]),
        ("Water area", meta["water_area"]),
        ("Sheriff / primary LE", meta["le_agency"]),
        ("LE non-emergency", meta["le_phone"]),
    ]

    def comma(v):
        return f"{v:,}" if isinstance(v, (int, float)) else str(v)

    # A field nothing returned is left OUT of the popup entirely. Printing
    # "not in dataset" on nine rows buries the three that carry real values, and
    # an omitted row asserts nothing - which is the only thing the rule against
    # inventing values actually requires. The Document description still lists
    # every source consulted, so absence stays explainable.
    #
    # Styling: the VALUE is bold, because that is what someone is reading the
    # popup for; the source and year are grey and bracketed, present for
    # judgement but never competing with the number. <font color> rather than a
    # CSS span - ATAK's description renderer is not a full browser, and the old
    # tag is the one constrained renderers reliably honour.
    present = [(label, sv) for label, sv in rows if sv]
    body = ""
    for label, sv in present:
        value, tag = sv.parts(comma)
        line = f"{esc(label)}: <b>{esc(value)}</b>"
        if tag:
            line += f' <font color="{GREY}">[{esc(tag)}]</font>'
        body += line + "<br/>"
    missing = [label for label, sv in rows if not sv]
    if missing:
        body += (f'<br/><font color="{GREY}"><i>No data for: '
                 f'{esc(", ".join(missing))}</i></font><br/>')
    footer = (f'<hr/><font color="{GREY}"><i>'
              f"Boundary: {esc(meta['boundary_source'])}<br/>"
              f"{esc(meta['boundary_url'])}<br/>"
              f"Licence: public domain (US Census Bureau)<br/>"
              f"Pack built: {esc(meta['built'])}</i></font>")

    geoms = []
    for ring in rings_of(geom):
        coords = " ".join(
            f"{round(float(p[0]), precision)},{round(float(p[1]), precision)},0"
            for p in ring if p and len(p) >= 2)
        if coords:
            geoms.append(f"<LineString><tessellate>1</tessellate>"
                         f"<coordinates>{coords}</coordinates></LineString>")
    if not geoms:
        return None
    # MultiGeometry keeps exclaves and holes as separate drawn rings
    shape = geoms[0] if len(geoms) == 1 else f"<MultiGeometry>{''.join(geoms)}</MultiGeometry>"

    # KML 2.2 child order: name, visibility, description, styleUrl, geometry
    # The name is used exactly as the source spells it - see county_label.
    return (f"<Placemark><name>{esc(name)}</name>"
            f"<description><![CDATA[{body}{footer}]]></description>"
            f"<styleUrl>#county</styleUrl>{shape}</Placemark>")


def state_kml(state_abbr, placemarks, meta):
    """One Document holding every county in the state."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{esc(meta['title'])}</name><open>0</open>"
        f"<description><![CDATA["
        f"County boundaries and reference data for {esc(state_abbr)}.<br/><br/>"
        f"<b>Boundaries:</b> {esc(meta['boundary_source'])}<br/>"
        f"{esc(meta['boundary_url'])}<br/>"
        f"<b>Population / housing:</b> {esc(meta['acs_label'])}<br/>"
        f"<b>Land / water area:</b> TIGER {esc(meta['tiger_vintage'])} ALAND / AWATER<br/>"
        f"<b>Licence:</b> public domain (US Census Bureau)<br/>"
        f"<b>Counties:</b> {len(placemarks)}<br/>"
        f"<b>Built:</b> {esc(meta['built'])}<br/><br/>"
        f"Every value in a county popup is followed by its source and year. "
        f"A field reading &quot;not in dataset&quot; had no source - it is not zero."
        f"]]></description>"
        '<Style id="county">'
        '<LineStyle><color>ff0055ff</color><width>2.5</width></LineStyle>'
        '<PolyStyle><fill>0</fill><outline>1</outline></PolyStyle>'
        '<IconStyle><scale>0</scale></IconStyle>'
        '<LabelStyle><scale>0.8</scale></LabelStyle>'
        '</Style>'
        + "".join(placemarks) +
        "</Document></kml>")


def write_kmz(path, kml, icons=None):
    """doc.kml, plus any icons the document references by relative path.

    An icon INSIDE the kmz is the only kind that works on a tablet with no
    signal, which is most of why this pack exists. A remote href renders as
    nothing in the field and as a working icon on the bench, which is the worst
    way for a bug to behave.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml)
        for name, data in sorted((icons or {}).items()):
            z.writestr(name, data)
    return os.path.getsize(path)


def safe(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(name)).strip("_")


# --------------------------------------------------------------------------
# Build one state
# --------------------------------------------------------------------------
def build_state(state_abbr, out_dir, acs_year=ACS_YEAR, per_county=False,
                endpoint=TIGERWEB, precision=6, log=print, today=None,
                census_api_key=None):
    import datetime as _dt
    built = today or _dt.date.today().isoformat()
    sfp = STATE_FIPS[state_abbr]

    log(f"[*] {state_abbr}: fetching county boundaries ...")
    feats, used_url, vintage = fetch_counties(sfp, endpoint, TIGERWEB_ALTERNATES, log=log)
    log(f"    {len(feats)} counties (boundary vintage: {vintage})")

    log(f"[*] {state_abbr}: fetching ACS {acs_year} population + housing ...")
    acs = fetch_acs(sfp, acs_year, log=log, key=census_api_key)
    log(f"    {len(acs)} county records")

    seats = load_csv_table("county_seats.csv")
    le = load_csv_table("le_contacts.csv")

    boundary_source = f"US Census TIGERweb ({vintage})"
    # Same rule as the state pack: a pack is datable from its own filename.
    if vintage == TIGER_VINTAGE_UNKNOWN:
        stamp_for_files = f"built{built}"
    elif _YEAR_RE.search(vintage):
        stamp_for_files = vintage
    else:
        stamp_for_files = f"{vintage}_{built}"
    acs_label = (f"US Census ACS 5-year {acs_year}" if acs
                 else "not retrieved - population and housing show as not in dataset")

    placemarks, built_rows = [], []
    for f in feats:
        props = f.get("properties") or {}
        geom = f.get("geometry")
        # TIGERweb spells these upper-case; be forgiving about case anyway.
        p = {str(k).upper(): v for k, v in props.items()}
        name = county_label(props)
        # None when the feature carries no usable identity. It is NOT padded out
        # to "<state>000" - that would be a FIPS code no source returned, shown
        # under TIGER's name.
        geoid = county_geoid(props)
        cty3 = geoid[2:5] if geoid else ""

        def area(label, *keys):
            for key in keys:
                if key in p:
                    try:
                        return Sourced(round(float(p[key]) / SQ_M_PER_SQ_MI, 1),
                                       f"TIGER {label}", vintage)
                    except (TypeError, ValueError):
                        return Sourced()
            return Sourced()

        rec = acs.get(cty3, {}) if cty3 else {}
        meta = {
            "name": name, "state_abbr": state_abbr,
            "geoid_str": geoid or "",
            "geoid": Sourced(geoid, "TIGER", vintage) if geoid else Sourced(),
            "tiger_vintage": vintage, "built": built,
            "boundary_source": boundary_source, "boundary_url": used_url,
            "population": rec.get("population", Sourced()),
            "housing_units": rec.get("housing_units", Sourced()),
            "land_area": area("ALAND", "AREALAND", "ALAND"),
            "water_area": area("AWATER", "AREAWATER", "AWATER"),
            "seat": sourced_from_row(seats.get(geoid), "seat"),
            "le_agency": sourced_from_row(le.get(geoid), "agency"),
            "le_phone": sourced_from_row(le.get(geoid), "phone"),
        }
        # land_area carries sq mi; label it so the popup reads correctly
        for k in ("land_area", "water_area"):
            if meta[k]:
                meta[k].value = f"{meta[k].value:,.1f} sq mi"

        pm = county_placemark(props, geom, meta, precision)
        if pm is None:
            log(f"    [!] {name}: no usable geometry, skipped")
            continue
        placemarks.append(pm)
        built_rows.append(meta)

        if per_county:
            one = state_kml(state_abbr, [pm], {
                "title": f"{name}, {state_abbr}", "boundary_source": boundary_source,
                "boundary_url": used_url, "acs_label": acs_label,
                "tiger_vintage": vintage, "built": built})
            write_kmz(os.path.join(out_dir,
                                   f"{state_abbr}_{safe(name)}__{safe(stamp_for_files)}.kmz"),
                      one)

    if not placemarks:
        raise RuntimeError(f"{state_abbr}: no counties with usable geometry")

    # The filename carries the boundary vintage when the service reported one,
    # and the build date when it did not - so a pack is always datable from its
    # name, and never claims a year nothing returned.
    # A pack must be datable from its filename alone. A vintage that already
    # names a year ("Census 2020") is enough; one that does not ("Current") gets
    # the build date appended, because "Current" ages the moment it is written.
    stamp = stamp_for_files
    # What the source calls them, when it calls all of them the same thing.
    # With nothing in common it falls back to TIGER's own name for the layer;
    # with a single feature there is nothing to pluralise at all (DC).
    kind = shared_descriptor(m["name"] for m in built_rows)
    if not kind:
        kind = "" if len(built_rows) == 1 else "County"
    title = (f"{state_abbr} {kind} boundaries and reference data ({vintage})"
             .replace("  ", " "))
    kml = state_kml(state_abbr, placemarks, {
        "title": title, "boundary_source": boundary_source, "boundary_url": used_url,
        "acs_label": acs_label, "tiger_vintage": vintage, "built": built})
    # "__" separates the pack's IDENTITY from its VERSION. Everything left of it
    # names what the pack IS (MN_Counties); everything right of it is which
    # edition. atak-install.sh reads that boundary to retire older editions of
    # the same pack, and a filename with no "__" has no version, so it is simply
    # overwritten in place. Guessing the split from token counts instead would
    # have read SAMPLE_MN_Water as a version of SAMPLE_MN_Energy.
    path = os.path.join(out_dir, f"{state_abbr}_Counties__{safe(stamp)}.kmz")
    size = write_kmz(path, kml)

    filled = sum(1 for m in built_rows if m["population"])
    log(f"[*] {state_abbr}: {len(placemarks)} counties, {filled} with population "
        f"-> {os.path.basename(path)} ({size / 1024:.0f} KB)")
    return {"state": state_abbr, "path": path, "counties": len(placemarks),
            "with_population": filled, "boundary_url": used_url,
            "boundary_vintage": vintage,
            "acs_year": acs_year if acs else None, "built": built}


def probe(log=print):
    """Check every endpoint this project uses, and SHOW what is there.

    A pass/fail probe tells you something is wrong but not what to do. This
    prints each MapServer's layer list with ids and names, so if the county
    layer has been renumbered you can read the right id straight off and pass
    it with --endpoint .../MapServer/<id>.
    """
    ok = True

    def show_service(url):
        """Print the layer list of the MapServer this layer URL belongs to."""
        root = re.sub(r"/\d+/?$", "", url)
        try:
            info = get_json(root, {"f": "json"}, tries=1, timeout=30)
        except Exception as e:                      # noqa: BLE001
            log(f"        (could not list layers: {e})")
            return
        layers = info.get("layers") or []
        if not layers:
            return
        log(f"        layers at {root}:")
        for l in layers:
            mark = "  <-- counties?" if "county" in str(l.get("name", "")).lower() else ""
            log(f"          {str(l.get('id')):>3}  {l.get('name')}{mark}")

    log("BOUNDARIES (county polygons)")
    first = True
    for url in [TIGERWEB] + TIGERWEB_ALTERNATES:
        try:
            info = get_json(url, {"f": "json"}, tries=1, timeout=30)
            log(f"  OK    {url}")
            log(f"        name={info.get('name')!r}  "
                f"geometryType={info.get('geometryType')!r}")
            log(f"        vintage reported: {service_vintage(url, log=lambda *a: None)}")
            if first:
                show_service(url)
                first = False
        except Exception as e:                      # noqa: BLE001
            ok = False
            log(f"  DEAD  {url}\n        {e}")

    log("")
    log(f"POPULATION + HOUSING (ACS 5-year {ACS_YEAR})")
    k = census_key()
    if not k:
        ok = False
        log("  NO KEY  the Census API requires one; a keyless request comes back")
        log("          as an HTML page, not an error.")
        log(f"          Free and instant: {ACS_KEY_SIGNUP}")
        log("          then: export CENSUS_API_KEY=your_key_here")
    else:
        src = ("--census-key" if False else
               "$CENSUS_API_KEY" if os.environ.get("CENSUS_API_KEY") else KEY_FILE)
        log(f"  key found via {src} (...{k[-4:]})")
        try:
            raw = http_get(ACS_BASE.format(year=ACS_YEAR),
                           {"get": "NAME," + ACS_VARS["population"],
                            "for": "county:*", "in": "state:27", "key": k},
                           tries=1, timeout=30).decode("utf-8", "replace")
            if raw.lstrip().startswith("<"):
                ok = False
                log("  DEAD  key was rejected - the API returned an HTML page.")
                log(f"        Check the key, or request a new one: {ACS_KEY_SIGNUP}")
            else:
                rows = json.loads(raw)
                log(f"  OK    {len(rows) - 1} Minnesota county rows returned")
        except Exception as e:                      # noqa: BLE001
            ok = False
            log(f"  DEAD  ACS {ACS_YEAR}\n        {e}")

    log("")
    log("OPTIONAL ENRICHMENT (packs build without these)")
    try:
        get_json("https://query.wikidata.org/sparql",
                 {"query": "SELECT ?x WHERE { BIND(1 AS ?x) }", "format": "json"},
                 tries=1, timeout=30)
        log("  OK    Wikidata  (county seats - fetch_county_seats.py)")
    except Exception as e:                          # noqa: BLE001
        log(f"  DEAD  Wikidata  (county seats unavailable)\n        {e}")
    try:
        info = get_json("https://maps.nccs.nasa.gov/mapping/rest/services"
                        "/hifld_open/law_enforcement/FeatureServer",
                        {"f": "json"}, tries=1, timeout=30)
        names = [str(l.get("name")) for l in (info.get("layers") or [])]
        log(f"  OK    HIFLD LE  (sheriff contacts - seed_le_contacts.py)")
        log(f"        layers: {', '.join(names) or '(none)'}")
    except Exception as e:                          # noqa: BLE001
        log(f"  DEAD  HIFLD LE  (sheriff contacts unavailable)\n        {e}")

    log("")
    if ok:
        log("Boundaries and ACS are reachable - you can build.")
    else:
        log("A REQUIRED endpoint is down. If a county layer above has a different")
        log("id than the one in the URL, rerun with:")
        log("    --endpoint https://.../MapServer/<that id>")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build county-boundary KMZ packages for ATAK, one per state.")
    ap.add_argument("--state", help="two-letter abbreviation, e.g. MN")
    ap.add_argument("--all-states", action="store_true", help="every state + DC + PR")
    ap.add_argument("--out", default="./packs", help="output directory (default ./packs)")
    ap.add_argument("--acs-year", type=int, default=ACS_YEAR,
                    help=f"ACS 5-year vintage for population/housing (default {ACS_YEAR})")
    ap.add_argument("--per-county", action="store_true",
                    help="also write one KMZ per county")
    ap.add_argument("--endpoint", default=TIGERWEB, help="override the boundary endpoint")
    ap.add_argument("--precision", type=int, default=6,
                    help="coordinate decimals; 5 is about 1 m and makes smaller files")
    ap.add_argument("--census-key", help="Census API key (or set CENSUS_API_KEY, "
                                         f"or save it in {KEY_FILE}). "
                                         f"Free: {ACS_KEY_SIGNUP}")
    ap.add_argument("--probe", action="store_true", help="check endpoints and exit")
    ap.add_argument("--http-budget", type=int, default=HTTP_BUDGET_S,
                    metavar="SECONDS",
                    help=f"wall-clock budget per request url, retries included "
                         f"(default {HTTP_BUDGET_S}). Raise it on a slow link; "
                         f"lower it to fail fast.")
    args = ap.parse_args(argv)

    # http_get resolves this at call time, so setting it here reaches every
    # request without threading a parameter through the whole builder.
    globals()["HTTP_BUDGET_S"] = max(5, args.http_budget)

    if args.probe:
        return 0 if probe() else 2

    if args.all_states:
        targets = sorted(STATE_FIPS)
    elif args.state:
        s = args.state.strip().upper()
        if s not in STATE_FIPS:
            print(f"unknown state {args.state!r}; expected one of "
                  f"{', '.join(sorted(STATE_FIPS))}", file=sys.stderr)
            return 1
        targets = [s]
    else:
        ap.error("give --state XX or --all-states (or --probe)")

    os.makedirs(args.out, exist_ok=True)
    done, failed = [], []
    for st in targets:
        try:
            done.append(build_state(st, args.out, args.acs_year, args.per_county,
                                    args.endpoint, args.precision,
                                    census_api_key=args.census_key))
        except Exception as e:                      # noqa: BLE001 - keep going
            failed.append((st, e))
            print(f"[!] {st}: {e}", file=sys.stderr)

    print(f"\n{len(done)} pack(s) written to {os.path.abspath(args.out)}")
    for d in done:
        print(f"  {os.path.basename(d['path']):40} {d['counties']:>4} counties, "
              f"{d['with_population']:>4} with population")
    if failed:
        print(f"\n{len(failed)} state(s) failed:", file=sys.stderr)
        for st, e in failed:
            print(f"  {st}: {e}", file=sys.stderr)
    if not done:
        return 2 if failed else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
