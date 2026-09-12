"""Cheap liveness probes for catalog sources.

Answers "would this source work right now?" without downloading the data, so
`overlaybuilder doctor` can check a whole AOI in a minute and the build can
decide whether to fall back to an alternate.

Each probe returns a Probe: ok/status/detail plus a feature count when the
server volunteers one. Probes never raise; a failure is a result.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .http import HttpStatusError, get_json, http_get

# Probes must be fast and bounded: one attempt, short timeout, no backoff ladder.
# A health check of 100 sources should take a minute, not an afternoon.
TRIES = 1
TIMEOUT = 20

OK = "ok"
DEAD = "dead"
AUTH = "auth"
SKIP = "skip"
WARN = "warn"


def _oneline(text: str, limit: int = 160) -> str:
    """Collapse an exception into one table-safe line."""
    t = " ".join(str(text).split()).replace("|", "/")
    return t[:limit]


@dataclass
class Probe:
    status: str                       # ok | warn | auth | dead | skip
    detail: str = ""
    count: Optional[int] = None
    url: str = ""
    fields: List[str] = field(default_factory=list)
    layer_name: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (OK, WARN, SKIP)

    def __post_init__(self):
        self.detail = _oneline(self.detail)

    def line(self) -> str:
        n = f"  n={self.count:,}" if isinstance(self.count, int) else ""
        return f"{self.status.upper():5}{n}  {self.detail}"


def _arcgis(spec: dict, ctx) -> Probe:
    url = ctx.render(spec["url"]).rstrip("/")
    try:
        meta = get_json(url, cache=False, tries=TRIES, timeout=TIMEOUT)
    except HttpStatusError as e:
        return Probe(AUTH if e.code in (401, 403) else DEAD, f"HTTP {e.code} on the service", url=url)
    except Exception as e:  # noqa: BLE001
        return Probe(DEAD, f"unreachable: {str(e)[:120]}", url=url)
    if "error" in meta:
        err = meta["error"]
        code = err.get("code")
        status = AUTH if code in (401, 403, 499) else DEAD
        return Probe(status, f"service error {code}: {str(err.get('message'))[:80]}", url=url)

    layers = (meta.get("layers") or []) + (meta.get("tables") or [])
    lid = spec.get("layer_id")
    name = ""
    if lid is None and spec.get("layer_match"):
        rx = re.compile(spec["layer_match"], re.I)
        hits = [l for l in layers if not l.get("subLayerIds") and rx.search(l.get("name", ""))]
        if not hits:
            avail = ", ".join(str(l.get("name")) for l in layers[:12])
            return Probe(DEAD, f"no layer matches /{spec['layer_match']}/ (have: {avail[:140]})", url=url)
        lid, name = hits[0]["id"], hits[0].get("name", "")
    elif lid is None and "fields" in meta:
        lid = ""                       # the url already points at a layer
    if lid == "" and "fields" in meta:
        info = meta
    else:
        try:
            info = get_json(f"{url}/{lid}", cache=False, tries=TRIES, timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001
            return Probe(DEAD, f"layer {lid} unreachable: {str(e)[:100]}", url=f"{url}/{lid}")
        if "error" in info:
            return Probe(DEAD, f"layer {lid} error: {str(info['error'].get('message'))[:80]}", url=f"{url}/{lid}")
    name = name or info.get("name", "")
    flds = [f.get("name", "") for f in info.get("fields") or []]

    count = None
    detail = f"layer {lid} '{name}'"
    try:
        from .drivers.arcgis import _spatial_params, _where
        params = dict(_where_params(spec, ctx), returnCountOnly="true")
        js = get_json(f"{url}/{lid}/query", params, cache=False, tries=TRIES, timeout=TIMEOUT)
        if "error" in js:
            return Probe(WARN, detail + f"; count query rejected: {str(js['error'].get('message'))[:60]}",
                         url=f"{url}/{lid}", fields=flds, layer_name=name)
        count = js.get("count")
    except Exception as e:  # noqa: BLE001
        return Probe(WARN, detail + f"; count failed: {str(e)[:80]}", url=f"{url}/{lid}", fields=flds, layer_name=name)

    missing = _missing_fields(spec, flds)
    if missing:
        return Probe(WARN, detail + f"; mapped fields absent: {', '.join(missing[:8])}",
                     count=count, url=f"{url}/{lid}", fields=flds, layer_name=name)
    return Probe(OK, detail, count=count, url=f"{url}/{lid}", fields=flds, layer_name=name)


def _where_params(spec: dict, ctx) -> Dict[str, Any]:
    from .drivers.arcgis import _spatial_params, _where
    p = {"where": _where(spec, ctx)}
    p.update(_spatial_params(spec, ctx))
    return p


def _mapped_fields(spec: dict) -> List[str]:
    out = []
    for cfg in (spec.get("fields") or {}).values():
        if not isinstance(cfg, dict) or "const" in cfg:
            continue
        frm = cfg.get("from") or []
        if isinstance(frm, str):
            frm = [frm]
        for f in frm:
            out.append(f.split("@", 1)[0])
    return out


def _missing_fields(spec: dict, available: List[str]) -> List[str]:
    """Canonical mappings whose every candidate is absent from the server schema."""
    if not available:
        return []
    have = {a.lower() for a in available}
    missing = []
    for key, cfg in (spec.get("fields") or {}).items():
        if not isinstance(cfg, dict) or "const" in cfg:
            continue
        frm = cfg.get("from") or []
        if isinstance(frm, str):
            frm = [frm]
        cands = [f.split("@", 1)[0].lower() for f in frm]
        if cands and not any(c in have for c in cands):
            missing.append(key)
    return missing


def _file(spec: dict, ctx) -> Probe:
    url = ctx.render(spec["url"])
    if "{month}" in url or "{year}" in url:
        from .drivers.file import _month_candidates
        url = next(iter(_month_candidates(url)))
    if not url.startswith(("http://", "https://")):
        import os
        return Probe(OK if os.path.exists(url) else DEAD, f"local path {url}", url=url)
    try:
        http_get(url, tries=TRIES, timeout=TIMEOUT, cache=False, headers={"Range": "bytes=0-2047"})
        return Probe(OK, "download reachable", url=url)
    except HttpStatusError as e:
        if e.code in (401, 403):
            return Probe(AUTH, f"HTTP {e.code} (login or API key required)", url=url)
        return Probe(DEAD, f"HTTP {e.code}", url=url)
    except Exception as e:  # noqa: BLE001
        return Probe(DEAD, f"unreachable: {str(e)[:120]}", url=url)


def _overpass(spec: dict, ctx) -> Probe:
    """One tiny query per endpoint, no retry ladder (the drivers retry, probes do not)."""
    from urllib.parse import quote

    from .drivers.overpass import DEFAULT_ENDPOINTS
    eps = (spec.get("endpoints") or DEFAULT_ENDPOINTS)
    last = ""
    for ep in eps:
        try:
            http_get(ep, data=("data=" + quote("[out:json][timeout:10];node(1);out count;", safe="")).encode(),
                     tries=TRIES, timeout=TIMEOUT, cache=False,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
            return Probe(OK, f"endpoint responding ({ep.split('/')[2]})", url=ep)
        except Exception as e:  # noqa: BLE001
            last = str(e)[:90]
    return Probe(DEAD, f"no Overpass endpoint responded: {last}", url=eps[0])


def _tiger(spec: dict, ctx) -> Probe:
    from .drivers.census_tiger import _url_for_probe
    try:
        url = _url_for_probe(spec, ctx)
    except Exception as e:  # noqa: BLE001
        return Probe(SKIP, str(e)[:120])
    try:
        http_get(url, tries=TRIES, timeout=TIMEOUT, cache=False, headers={"Range": "bytes=0-1023"})
        return Probe(OK, f"TIGER {spec.get('product')} file present", url=url)
    except HttpStatusError as e:
        return Probe(DEAD, f"HTTP {e.code} - try a different --tiger-year", url=url)
    except Exception as e:  # noqa: BLE001
        return Probe(DEAD, f"unreachable: {str(e)[:100]}", url=url)


def _fcc(spec: dict, ctx) -> Probe:
    from .drivers.fcc_asr import DEFAULT_URL
    url = spec.get("url", DEFAULT_URL)
    try:
        http_get(url, tries=TRIES, timeout=TIMEOUT, cache=False, headers={"Range": "bytes=0-1023"})
        return Probe(OK, "FCC ASR archive reachable", url=url)
    except Exception as e:  # noqa: BLE001
        return Probe(DEAD, f"unreachable: {str(e)[:100]}", url=url)


PROBERS = {"arcgis": _arcgis, "file": _file, "overpass": _overpass,
           "census_tiger": _tiger, "fcc_asr": _fcc}


def probe_source(spec: dict, ctx) -> Probe:
    fn = PROBERS.get(spec["driver"])
    if fn is None:
        return Probe(SKIP, f"no probe for driver '{spec['driver']}'")
    try:
        return fn(spec, ctx)
    except Exception as e:  # noqa: BLE001  probes never raise
        return Probe(DEAD, f"probe crashed: {str(e)[:120]}")
