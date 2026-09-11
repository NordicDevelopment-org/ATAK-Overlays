"""Read delimited text (CSV/TSV/pipe) with lat/lon columns into point features.

Handles the common government bulk formats: FCC ASR pipe-delimited with
DMS components, USACE NID CSV with a preamble line, EIA-860 XLSX-exported CSV.
"""
import csv
import io
import re
from typing import Callable, Iterable, List, Optional

from ..model import Feature


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def dms_to_dd(deg, minutes, seconds, hemi: str) -> Optional[float]:
    d, m, s = _to_float(deg), _to_float(minutes) or 0.0, _to_float(seconds) or 0.0
    if d is None:
        return None
    dd = abs(d) + m / 60.0 + s / 3600.0
    if str(hemi).strip().upper() in ("S", "W") or d < 0:
        dd = -dd
    return dd


def read_delimited(raw: bytes, lat_field: str, lon_field: str, delimiter: str = ",",
                   encoding: str = "utf-8", skip_lines: int = 0, header: Optional[List[str]] = None,
                   keep: Optional[Callable[[dict], bool]] = None,
                   dms: Optional[dict] = None) -> List[Feature]:
    """dms: {"lat": ["LAT_DEG","LAT_MIN","LAT_SEC","LAT_DIR"], "lon": [...]} for DMS sources."""
    text = raw.decode(encoding, "replace")
    lines = text.splitlines()[skip_lines:]
    reader = csv.reader(lines, delimiter=delimiter)
    if header:
        cols = header
    else:
        cols = next(reader, [])
        cols = [c.strip().lstrip("﻿") for c in cols]
    out: List[Feature] = []
    for row in reader:
        if not row or all(not c.strip() for c in row):
            continue
        props = {cols[i]: (row[i].strip() if i < len(row) else "") for i in range(len(cols))}
        if keep and not keep(props):
            continue
        if dms:
            la = dms_to_dd(*[props.get(k) for k in dms["lat"]])
            lo = dms_to_dd(*[props.get(k) for k in dms["lon"]])
        else:
            la, lo = _to_float(props.get(lat_field)), _to_float(props.get(lon_field))
        if la is None or lo is None or not (-90 <= la <= 90 and -180 <= lo <= 180):
            continue
        out.append(Feature({"type": "Point", "coordinates": [lo, la]}, props))
    return out
