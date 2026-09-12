"""Normalized feature model and provenance.

Everything a driver returns is a list of Feature. Geometry is GeoJSON-shaped and
ALWAYS in EPSG:4326 (lon, lat). Provenance is attached per layer, not per
feature, to keep memory down on large pulls.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Provenance:
    """Where a layer's data came from. Recorded in output, per PROJECT RULES."""
    source_name: str                 # human label, e.g. "Census TIGER/Line 2023"
    source_url: str                  # the exact endpoint/file queried
    license: str                     # license / terms string
    retrieved: str                   # ISO date the build ran
    driver: str                      # driver id that fetched it
    notes: str = ""                  # any uncertainty flags


@dataclass
class Feature:
    geometry: Optional[Dict[str, Any]]   # GeoJSON geometry in EPSG:4326, or None
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LayerResult:
    """One logical layer pulled from one source."""
    logical: str                     # e.g. "roads", "parcels"
    features: List[Feature]
    provenance: Provenance
    group_by: Optional[List[str]] = None   # fields to fold into sub-toggles
    server_count: Optional[int] = None     # count reported by source, if any
