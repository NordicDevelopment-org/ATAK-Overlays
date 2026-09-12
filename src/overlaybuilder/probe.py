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
        # the catalog points straight at a layer; the arcgis driver needs an id,
        # so say so rather than reporting a healthy service
        return Probe(WARN, f"url is a layer, not a service: add layer_id or layer_match "
                           f"(this layer is '{meta.get('name', '')}')",
                     url=url, fields=[f.get("name", "") for f in meta.get("fields") or []],
                     layer_name=meta.get("name", ""))
    if lid is None:
        return Probe(WARN, "no layer_id or layer_match on this source; the driver cannot pick a layer",
                     url=url)
    try:
        info = get_json(f"{url}/{lid}", cache=False, tries=TRIES, timeout=TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return Probe(DEAD, f"layer {lid} unreachable: {str(e)[:100]}", url=f"{url}/{lid}")
    if "error" in info:
        return Probe(DEAD, f"layer {lid} error: {str(info['error'].get('message'))[:80]}", url=f"{url}/{lid}")
    name = name or info.get("name", "")
    flds = [f.get("name", "") for f in (info.get("fields") or [])]   # may be null on group layers

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
    """Canonical mappings that this server can satisfy from no column at all.

    Mirrors normalize's lookup exactly - the explicit `from:` candidates AND the
    built-in DEFAULT_FROM fallbacks, compared ignoring case and punctuation -
    so doctor does not warn about a field normalize would in fact find.
    """
    if not available:
        return []
    from .normalize import DEFAULT_FROM, _split_candidate, _squash
    have = {_squash(a) for a in available}
    missing = []
    for key, cfg in (spec.get("fields") or {}).items():
        if cfg is False or not isinstance(cfg, dict) or "const" in cfg:
            continue
        frm = cfg.get("from") or []
        if isinstance(frm, str):
            frm = [frm]
        cands = list(frm)
        if cfg.get("defaults", True):
            cands += DEFAULT_FROM.get(key, [])
        names = [_squash(_split_candidate(c)[0]) for c in cands]
        if names and not any(n in have for n in names):
            missing.append(key)
    return missing


def _declared(spec: dict) -> Optional[str]:
    f = spec.get("format")
    return None if (not f or f == "auto") else f


def _file(spec: dict, ctx) -> Probe:
    url = ctx.render(spec["url"])
    if "{month}" in url or "{year}" in url:
        # the newest monthly release may not be published yet; the driver walks
        # back a year, so the probe must not call the feed dead on month one
        from .drivers.file import _month_candidates
        last = Probe(DEAD, "no monthly release resolved", url=url)
        for cand in list(_month_candidates(url, months_back=3)):
            pr = _probe_download(cand, _declared(spec))
            if pr.ok:
                return pr
            last = pr
        return last
    return _probe_download(url, _declared(spec))


def _probe_download(url: str, declared_format: Optional[str] = None) -> Probe:
    if not url.startswith(("http://", "https://")):
        import os
        return Probe(OK if os.path.exists(url) else DEAD, f"local path {url}", url=url)
    if "{" in url or "$" in url:
        return Probe(SKIP, "URL still holds a placeholder (an env secret?); cannot probe", url=url)
    try:
        body = http_get(url, tries=TRIES, timeout=TIMEOUT, cache=False,
                        max_bytes=4096, headers={"Range": "bytes=0-2047"})
    except HttpStatusError as e:
        if e.code in (401, 403):
            return Probe(AUTH, f"HTTP {e.code} (login or API key required)", url=url)
        return Probe(DEAD, f"HTTP {e.code}", url=url)
    except Exception as e:  # noqa: BLE001
        return Probe(DEAD, f"unreachable: {str(e)[:120]}", url=url)
    from .drivers.file import _guess, sniff
    kind = sniff(body)
    if kind == "html":
        # the build refuses this too, so doctor must not green-light it
        return Probe(DEAD, "server answered with an HTML page, not the data file", url=url)
    if kind is None and _guess(url) is None and not declared_format:
        return Probe(WARN, "reachable, but neither the URL nor the first bytes say what format "
                           "this is; set `format:` on the source", url=url)
    return Probe(OK, "download reachable", url=url)


def _overpass(spec: dict, ctx) -> Probe:
    """One tiny query per endpoint, no retry ladder (the drivers retry, probes do not)."""
    from urllib.parse import quote

    from .drivers.overpass import DEFAULT_ENDPOINTS
    eps = (spec.get("endpoints") or DEFAULT_ENDPOINTS)
    last = ""
    for ep in eps:
        try:
            http_get(ep, data=("data=" + quote("[out:json][timeout:10];node(1);out count;", safe="")).encode(),
                     tries=TRIES, timeout=TIMEOUT, cache=False, min_interval=1.0,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
            return Probe(OK, f"endpoint responding ({ep.split('/')[2]})", url=ep)
        except HttpStatusError as e:
            if e.code in (429, 504):
                # the public instances throttle aggressively; alive, just busy
                return Probe(WARN, f"{ep.split('/')[2]} is rate-limiting (HTTP {e.code}); "
                                   "builds retry and rotate endpoints", url=ep)
            last = f"HTTP {e.code}"
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
        http_get(url, tries=TRIES, timeout=TIMEOUT, cache=False,
                 max_bytes=2048, headers={"Range": "bytes=0-1023"})
        return Probe(OK, f"TIGER {spec.get('product')} file present", url=url)
    except HttpStatusError as e:
        return Probe(DEAD, f"HTTP {e.code} - try a different --tiger-year", url=url)
    except Exception as e:  # noqa: BLE001
        return Probe(DEAD, f"unreachable: {str(e)[:100]}", url=url)


def _fcc(spec: dict, ctx) -> Probe:
    from .drivers.fcc_asr import DEFAULT_URL
    url = spec.get("url", DEFAULT_URL)
    try:
        http_get(url, tries=TRIES, timeout=TIMEOUT, cache=False,
                 max_bytes=2048, headers={"Range": "bytes=0-1023"})
        return Probe(OK, "FCC ASR archive reachable", url=url)
    except Exception as e:  # noqa: BLE001
        return Probe(DEAD, f"unreachable: {str(e)[:100]}", url=url)


PROBERS = {"arcgis": _arcgis, "file": _file, "overpass": _overpass,
           "census_tiger": _tiger, "fcc_asr": _fcc}


def probe_source(spec: dict, ctx) -> Probe:
    fn = PROBERS.get(spec.get("driver"))
    if fn is None:
        return Probe(SKIP, f"no probe for driver '{spec['driver']}'")
    try:
        return fn(spec, ctx)
    except Exception as e:  # noqa: BLE001  probes never raise
        return Probe(DEAD, f"probe crashed: {str(e)[:120]}")
