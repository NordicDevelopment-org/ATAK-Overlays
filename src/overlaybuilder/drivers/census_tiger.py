"""Census TIGER/Line driver - national baseline that works for EVERY US AOI.

Products (spec "product"):
  roads   -> per-county roads file(s)                (county AOI; state AOI = all its counties)
  cousub  -> county subdivisions (cities/townships)  filtered to the AOI
  county  -> national county file filtered to the AOI (county -> 1, state -> all)
  state   -> national state file filtered to the AOI's state(s)

TIGER is public domain (US Census Bureau). Year is ctx.tiger_year (override
with --tiger-year); bump it when a newer vintage is published. Downloads are
cached on disk (the national county file is ~80 MB).
"""
from typing import List

from ..model import Feature, LayerResult, Provenance
from ._shp import read_zipped_shapefile
from .base import Context, driver, http_get, today

_BASE = "https://www2.census.gov/geo/tiger/TIGER{yr}"


def _b(ctx: Context) -> str:
    return _BASE.format(yr=ctx.tiger_year)


def _state_fps(ctx: Context) -> List[str]:
    from ..fips import state_fp
    return [state_fp(a) for a in ctx.aoi.state_abbrs]


@driver("census_tiger")
def fetch(logical: str, spec: dict, ctx: Context) -> LayerResult:
    product = spec["product"]
    aoi = ctx.aoi
    if aoi.country != "US":
        raise RuntimeError("census_tiger only covers US AOIs")
    b = _b(ctx)
    yr = ctx.tiger_year
    feats: List[Feature] = []
    urls: List[str] = []

    if product == "roads":
        if aoi.kind != "county":
            raise RuntimeError("TIGER roads are per-county; build roads with --aoi county:FIPS "
                               "(state-wide roads would be thousands of files - use OSM/PBF instead)")
        url = f"{b}/ROADS/tl_{yr}_{aoi.fips5}_roads.zip"
        urls.append(url)
        feats = read_zipped_shapefile(http_get(url, timeout=600))

    elif product == "cousub":
        sfps = _state_fps(ctx) if aoi.kind != "county" else [aoi.state_fp]
        for sfp in sfps:
            url = f"{b}/COUSUB/tl_{yr}_{sfp}_cousub.zip"
            urls.append(url)
            if aoi.kind == "county":
                keep = lambda p, c=aoi.county_fp: str(p.get("COUNTYFP", "")).zfill(3) == c
            else:
                keep = None
            feats.extend(read_zipped_shapefile(http_get(url, timeout=600), keep))

    elif product == "county":
        url = f"{b}/COUNTY/tl_{yr}_us_county.zip"
        urls.append(url)
        if aoi.kind == "county":
            keep = lambda p: f"{p.get('STATEFP','')}{p.get('COUNTYFP','')}" == aoi.fips5
        elif aoi.kind in ("state", "region", "us"):
            want = set(_state_fps(ctx))
            keep = lambda p: str(p.get("STATEFP", "")) in want
        else:
            keep = None
        feats = read_zipped_shapefile(http_get(url, timeout=600), keep)

    elif product == "state":
        url = f"{b}/STATE/tl_{yr}_us_state.zip"
        urls.append(url)
        want = set(_state_fps(ctx)) if aoi.kind != "bbox" else None
        keep = (lambda p: str(p.get("STATEFP", "")) in want) if want else None
        feats = read_zipped_shapefile(http_get(url, timeout=600), keep)
    else:
        raise RuntimeError(f"unknown TIGER product '{product}'")

    prov = Provenance(
        source_name=f"US Census TIGER/Line {yr} ({product})",
        source_url="; ".join(urls),
        license="Public domain (US Census Bureau)",
        retrieved=today(), driver="census_tiger",
        notes=spec.get("notes", ""))
    return LayerResult(logical, feats, prov, spec.get("group_by"), len(feats))
