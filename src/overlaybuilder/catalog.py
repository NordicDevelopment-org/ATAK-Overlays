"""Load source specs from the tiered catalog and pick the ones that apply to
an AOI.

    catalog/global/*.yaml                         coverage: world   (OSM etc.)
    catalog/national/us/*.yaml                    coverage: us      (EIA, TIGER, NID, FCC...)
    catalog/states/<abbr>/*.yaml                  coverage: state   (state GIS portals)
    catalog/states/<abbr>/counties/<FIPS>_*.yaml  coverage: county  (county GIS)
    catalog/regions.yaml                          named state lists for --aoi region:

Each YAML has a top-level `sources:` list; every source needs `layer` and
`driver`. Optional per-source keys used here: `coverage` (override the tier),
`sector`, `enabled` (default true), `aoi_kinds` (restrict to some AOI kinds,
e.g. [county] for TIGER roads), `priority` (lower first; boundary layers 0).

Resolution order: global -> national -> state -> county, so a county pack
gets everything above it. A layer key may appear in several tiers (EIA and OSM
power plants both as `power_plants`); the output writer keeps them as separate
documents, and the reconcile step compares them.
"""
import glob
import os
from typing import Dict, List, Optional

import yaml

from .aoi import Aoi

TIERS = ("global", "national", "state", "county")


def _load_sources(path: str, tier: str) -> List[dict]:
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    out = []
    defaults = doc.get("defaults") or {}
    for s in doc.get("sources", []) or []:
        merged = dict(defaults)
        merged.update(s)
        if "layer" not in merged or "driver" not in merged:
            raise ValueError(f"{path}: each source needs 'layer' and 'driver': {s}")
        merged.setdefault("coverage", {"global": "world", "national": "us", "state": "state",
                                       "county": "county"}[tier])
        merged["_tier"] = tier
        merged["_file"] = os.path.relpath(path)
        merged.setdefault("id", f"{merged['layer']}@{os.path.splitext(os.path.basename(path))[0]}")
        out.append(merged)
    return out


def global_sources(catalog_dir: str) -> List[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(catalog_dir, "global", "*.yaml"))):
        out.extend(_load_sources(p, "global"))
    return out


def national_sources(catalog_dir: str, country: str = "US") -> List[dict]:
    out = []
    # both catalog/national/*.yaml (legacy) and catalog/national/<cc>/*.yaml
    paths = sorted(glob.glob(os.path.join(catalog_dir, "national", "*.yaml")))
    paths += sorted(glob.glob(os.path.join(catalog_dir, "national", country.lower(), "*.yaml")))
    for p in paths:
        out.extend(_load_sources(p, "national"))
    return out


def state_sources(catalog_dir: str, abbr: str) -> List[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(catalog_dir, "states", abbr.lower(), "*.yaml"))):
        out.extend(_load_sources(p, "state"))
    return out


def county_file(catalog_dir: str, fips5: str) -> Optional[str]:
    hits = glob.glob(os.path.join(catalog_dir, "states", "*", "counties", f"{fips5}_*.yaml"))
    hits += glob.glob(os.path.join(catalog_dir, "states", "*", "counties", f"{fips5}.yaml"))
    return sorted(hits)[0] if hits else None


def county_sources(catalog_dir: str, fips5: str) -> List[dict]:
    p = county_file(catalog_dir, fips5)
    return _load_sources(p, "county") if p else []


def _applies(s: dict, aoi: Aoi) -> bool:
    if s.get("enabled", True) is False:
        return False
    kinds = s.get("aoi_kinds")
    if kinds and aoi.kind not in kinds:
        return False
    cov = str(s.get("coverage", "world"))
    if cov == "world":
        return True
    if cov == "us":
        return aoi.country == "US"
    if cov.startswith("country:"):
        return aoi.country == cov.split(":", 1)[1].upper()
    if cov == "state" or cov.startswith("state:"):
        want = cov.split(":", 1)[1].upper() if ":" in cov else None
        return aoi.kind in ("county", "state", "region") and (want is None or want in aoi.state_abbrs)
    if cov == "county":
        return aoi.kind == "county"
    return True


def resolve_sources(catalog_dir: str, aoi: Aoi, only_layers: Optional[List[str]] = None,
                    sectors: Optional[List[str]] = None, exclude_layers: Optional[List[str]] = None,
                    include_disabled: bool = False) -> List[dict]:
    srcs: List[dict] = []
    srcs += global_sources(catalog_dir)
    if aoi.country == "US":
        srcs += national_sources(catalog_dir, "US")
    elif aoi.country:
        srcs += national_sources(catalog_dir, aoi.country)
    for ab in (aoi.state_abbrs if aoi.kind in ("county", "state", "region") else []):
        srcs += state_sources(catalog_dir, ab)
    if aoi.kind == "county" and aoi.fips5:
        srcs += county_sources(catalog_dir, aoi.fips5)

    out = []
    for s in srcs:
        if not include_disabled and not _applies(s, aoi):
            continue
        if only_layers and s["layer"] not in set(only_layers):
            continue
        if exclude_layers and s["layer"] in set(exclude_layers):
            continue
        if sectors:
            sec = str(s.get("sector", "")).lower().replace(" ", "_")
            if not any(sec.startswith(x.lower().replace(" ", "_")) for x in sectors):
                continue
        out.append(s)
    out.sort(key=lambda s: (int(s.get("priority", 50)), TIERS.index(s["_tier"])))
    return out


def list_counties(catalog_dir: str) -> List[str]:
    out = []
    for p in glob.glob(os.path.join(catalog_dir, "states", "*", "counties", "*.yaml")):
        out.append(os.path.basename(p))
    return sorted(out)


def all_sources(catalog_dir: str) -> List[dict]:
    """Every source in every tier (for validation / listing)."""
    out = global_sources(catalog_dir)
    for p in sorted(glob.glob(os.path.join(catalog_dir, "national", "**", "*.yaml"), recursive=True)):
        out.extend(_load_sources(p, "national"))
    for p in sorted(glob.glob(os.path.join(catalog_dir, "states", "*", "*.yaml"))):
        out.extend(_load_sources(p, "state"))
    for p in sorted(glob.glob(os.path.join(catalog_dir, "states", "*", "counties", "*.yaml"))):
        out.extend(_load_sources(p, "county"))
    return out


REQUIRED_BY_DRIVER: Dict[str, List[str]] = {
    "arcgis": ["url"], "file": ["url"], "overpass": [], "census_tiger": ["product"], "osm_pbf": [],
    "fcc_asr": [],
}


def validate(catalog_dir: str) -> List[str]:
    """Offline lint of every catalog file. Returns a list of problems."""
    from .drivers import known_drivers
    problems = []
    try:
        srcs = all_sources(catalog_dir)
    except Exception as e:  # noqa: BLE001
        return [f"catalog load failed: {e}"]
    drivers = set(known_drivers())
    for s in srcs:
        where = f"{s['_file']} [{s['layer']}]"
        if s["driver"] not in drivers:
            problems.append(f"{where}: unknown driver {s['driver']}")
            continue
        for k in REQUIRED_BY_DRIVER.get(s["driver"], []):
            if k not in s:
                problems.append(f"{where}: driver {s['driver']} needs '{k}'")
        if s["driver"] == "arcgis" and s.get("layer_id") is None and not s.get("layer_match"):
            problems.append(f"{where}: arcgis needs layer_id or layer_match")
        if s["driver"] in ("overpass", "osm_pbf") and not (s.get("tags") or s.get("tag")):
            problems.append(f"{where}: {s['driver']} needs tags: [...]")
        if s["_tier"] != "national" or s["driver"] not in ("census_tiger", "overpass"):
            if not s.get("license"):
                problems.append(f"{where}: missing license")
        if not s.get("source_name") and s["driver"] in ("arcgis", "file"):
            problems.append(f"{where}: missing source_name")
        for r in s.get("style_rules") or []:
            if "when" not in r:
                problems.append(f"{where}: style rule without 'when'")
    return problems
