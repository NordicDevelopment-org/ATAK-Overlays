"""Driver base: build context and the driver registry (HTTP lives in ..http).

A driver turns one catalog source spec into a LayerResult whose geometry is in
EPSG:4326. Register with @driver("name"); resolve with get_driver(name).
"""
import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from ..aoi import Aoi
from ..http import HttpStatusError, configure_http, get_json, http_get  # noqa: F401 (re-export)

@dataclass
class Context:
    """Everything a driver needs about the target AOI + run options.

    County-era fields (state_fp/county_fp/...) are kept as properties so the
    original drivers keep working unchanged; new drivers should read `aoi`.
    """
    aoi: Aoi
    tiger_year: int = 2024
    cache_dir: str = ".cache"
    http_cache: bool = True          # cache GET bodies on disk (TIGER zips, bulk CSVs)
    bbox: Optional[tuple] = None     # refined envelope (set from boundary layer)
    boundary: Optional[dict] = None  # GeoJSON polygon used for clipping
    boundary_index: Any = None       # aoi.BoundaryIndex built from `boundary`
    clip: bool = True
    options: Dict[str, Any] = field(default_factory=dict)  # free-form CLI passthrough

    def __post_init__(self):
        if self.bbox is None:
            self.bbox = self.aoi.bbox

    # --- back-compat accessors -------------------------------------------
    @property
    def state_fp(self): return self.aoi.state_fp
    @property
    def county_fp(self): return self.aoi.county_fp
    @property
    def county_name(self): return self.aoi.county_name or ""
    @property
    def state_abbr(self): return self.aoi.state_abbr or ""
    @property
    def fips5(self): return self.aoi.fips5

    def render(self, s: Any) -> Any:
        """Substitute {fips5}, {state_abbr}, {states_sql}, ... in a string."""
        if not isinstance(s, str):
            return s
        import os
        s = os.path.expandvars(s)            # ${NREL_API_KEY} style secrets from the environment
        try:
            return s.format(**self.aoi.template_vars())
        except (KeyError, IndexError):
            return s

    def vars(self) -> Dict[str, str]:
        return self.aoi.template_vars()


def today() -> str:
    return _dt.date.today().isoformat()


# --- registry ---------------------------------------------------------------
_REGISTRY: Dict[str, Callable] = {}


def driver(name: str):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn
    return deco


def get_driver(name: str) -> Callable:
    if name not in _REGISTRY:
        raise KeyError(f"unknown driver '{name}'. known: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def known_drivers():
    return sorted(_REGISTRY)
