"""Shared HTTP: retries, 429/5xx backoff, per-host politeness, on-disk cache."""
import hashlib
import json
import os
import ssl
import time
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_SSL = ssl.create_default_context()
_UA = "overlaybuilder/0.2 (+https://github.com/NordicDevelopment-org/ATAK-Overlays)"


class HttpStatusError(RuntimeError):
    def __init__(self, code: int, url: str, body: bytes = b""):
        super().__init__(f"HTTP {code} for {url}")
        self.code, self.url, self.body = code, url, body


_CACHE_DIR: Optional[str] = None
_CACHE_ON = False
_LAST_CALL: Dict[str, float] = {}
MIN_INTERVAL_S = 0.0   # per-host politeness delay; drivers may raise it


def configure_http(cache_dir: Optional[str], enabled: bool):
    global _CACHE_DIR, _CACHE_ON
    _CACHE_DIR, _CACHE_ON = cache_dir, enabled


def _cache_path(url: str) -> Optional[str]:
    if not (_CACHE_ON and _CACHE_DIR):
        return None
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    d = os.path.join(_CACHE_DIR, "http")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h)


def _host(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0]


def http_get(url: str, params: Optional[dict] = None, tries: int = 4, timeout: int = 120,
             cache: Optional[bool] = None, headers: Optional[dict] = None,
             data: Optional[bytes] = None, min_interval: float = 0.0) -> bytes:
    """GET (or POST when `data` given) with retries, 429/5xx backoff, optional
    on-disk cache. Raises HttpStatusError for final 4xx, RuntimeError otherwise."""
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    key = url if data is None else url + "|" + hashlib.sha256(data).hexdigest()
    cp = _cache_path(key) if (cache if cache is not None else True) else None
    if cp and os.path.exists(cp):
        with open(cp, "rb") as fh:
            return fh.read()

    hdrs = {"User-Agent": _UA}
    hdrs.update(headers or {})
    last: Optional[Exception] = None
    host = _host(url)
    for i in range(tries):
        gap = max(min_interval, MIN_INTERVAL_S)
        if gap:
            since = time.time() - _LAST_CALL.get(host, 0)
            if since < gap:
                time.sleep(gap - since)
        _LAST_CALL[host] = time.time()
        try:
            req = Request(url, headers=hdrs, data=data)
            with urlopen(req, timeout=timeout, context=_SSL) as r:
                body = r.read()
            if cp:
                tmp = cp + ".tmp"
                with open(tmp, "wb") as fh:
                    fh.write(body)
                os.replace(tmp, cp)
            return body
        except HTTPError as e:
            last = e
            code = e.code
            body = b""
            try:
                body = e.read()
            except Exception:
                pass
            if code in (429, 500, 502, 503, 504) and i < tries - 1:
                ra = e.headers.get("Retry-After") if e.headers else None
                wait = float(ra) if (ra and ra.isdigit()) else 3.0 * (2 ** i)
                time.sleep(min(wait, 120))
                continue
            if 400 <= code < 500:
                raise HttpStatusError(code, url, body)
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
        except (URLError, OSError, TimeoutError) as e:
            last = e
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed after {tries} tries: {url}\n  {last}")


def get_json(url: str, params: Optional[dict] = None, **kw) -> dict:
    p = dict(params or {})
    p.setdefault("f", "json")
    return json.loads(http_get(url, p, **kw).decode("utf-8", "replace"))


