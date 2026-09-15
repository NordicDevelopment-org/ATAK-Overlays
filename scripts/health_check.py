#!/usr/bin/env python3
"""Catalog health check: ping every source URL in the catalog and report dead
ones. Government and county GIS URLs rot constantly; CI runs this weekly so the
crowd-sourced catalog stays honest. Exit non-zero if any source is unreachable.

ArcGIS services are probed with ?f=json and the response must not carry an
"error" object (a 200 with {"error": ...} is how ArcGIS says "gone").
Templated URLs ({state_abbr} etc.) are rendered for Minnesota.
"""
import json
import os
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from overlaybuilder import catalog  # noqa: E402
from overlaybuilder.aoi import parse_aoi  # noqa: E402

CATALOG = os.path.join(ROOT, "catalog")
SAMPLE_VARS = parse_aoi("state:MN").template_vars()

# A timeout says the server was slow to answer, not that it refused or does
# not exist - unlike a 403, a 404, an ArcGIS error body, or a DNS failure,
# which are the server (or the resolver) giving a definite, retry-proof
# answer. Measured 2026-09-15: three Wisconsin state GIS sources timed out
# in CI while search-engine caches showed their exact URLs answering with
# real, current data - a clean rejection was never involved, only a slow or
# momentarily rate-limited server. Retrying a definite DEAD would just be
# noise; retrying a timeout is the one case where trying again might be the
# actually correct answer.
RETRIES_ON_TIMEOUT = 2
RETRY_BACKOFF_S = 3


def _is_timeout(e):
    if isinstance(e, TimeoutError):
        return True
    return isinstance(e, URLError) and isinstance(e.reason, TimeoutError)


def ping_monthly(url: str, arcgis: bool) -> str:
    """EIA-style {month}/{year} feeds: the newest release may not be published
    yet, so only report DEAD when no recent month resolves."""
    from overlaybuilder.drivers.file import _month_candidates
    last = ""
    for cand in list(_month_candidates(url, months_back=3)):
        last = ping(cand, arcgis)
        if not last.startswith("DEAD"):
            return last + f"  ({cand.rsplit('/', 1)[-1]})"
    return last


def ping(url: str, arcgis: bool) -> str:
    u = url + ("&" if "?" in url else "?") + "f=json" if arcgis else url
    req = Request(u, headers={"User-Agent": "overlaybuilder-health/0.2"}, method="GET")
    for attempt in range(RETRIES_ON_TIMEOUT + 1):
        try:
            with urlopen(req, timeout=45) as r:
                if arcgis:
                    body = r.read(200000)
                    try:
                        js = json.loads(body)
                    except json.JSONDecodeError:
                        return "DEAD non-json"
                    if "error" in js:
                        return f"DEAD {js['error'].get('code')} {js['error'].get('message', '')[:60]}"
                    if "fields" not in js and "layers" not in js:
                        return "WARN no layers/fields in response"
                return f"ok {r.status}"
        except Exception as e:  # noqa: BLE001
            if _is_timeout(e) and attempt < RETRIES_ON_TIMEOUT:
                time.sleep(RETRY_BACKOFF_S)
                continue
            return f"DEAD {e}"


def ping_alternates(alternates, where):
    """Try each alternate in order; True on the first one that answers.

    build.py already falls through to alternates when a primary fails - that
    is the whole point of the field existing. This script used to test only
    the primary, so a source whose primary had gone dead but whose alternate
    still worked (exactly the case alternates: exists for) was reported DEAD
    anyway - a false positive this script itself could resolve, not a real
    catalog problem.
    """
    for alt in alternates or []:
        url = alt.get("url")
        if not url or "{" in url:            # templated alternates: not rendered here
            continue
        arcgis = alt.get("driver") == "arcgis"
        if arcgis and alt.get("layer_id") is not None:
            url = url.rstrip("/") + f"/{alt['layer_id']}"
        status = ping(url, arcgis)
        if not status.startswith("DEAD"):
            print(f"{where} {'ok (alternate)':28} {url[:90]}")
            return True
    return False


def main():
    bad = 0
    for s in catalog.all_sources(CATALOG):
        where = f"{s['_file']:52} {s['layer']:22}"
        if s.get("enabled", True) is False:
            # documented but unverified candidates; `doctor --include-disabled` tests these
            print(f"{where} {'SKIP disabled':28}")
            continue
        url = s.get("url")
        if not url:  # tiger/overpass/fcc build URLs dynamically; skip
            continue
        try:
            url = url.format(**SAMPLE_VARS)
        except (KeyError, IndexError) as e:
            print(f"{where} {'SKIP unrenderable ' + str(e):28}")
            continue
        if "{month}" in url or "{year}" in url:
            status = ping_monthly(url, s["driver"] == "arcgis")
            print(f"{where} {status:28} {url[:70]}")
            bad += 1 if status.startswith("DEAD") else 0
            continue
        if "{" in url:                       # a placeholder we do not fill (e.g. an env secret)
            print(f"{where} {'SKIP templated':28} {url[:70]}")
            continue
        arcgis = s["driver"] == "arcgis"
        if arcgis and s.get("layer_id") is not None:
            url = url.rstrip("/") + f"/{s['layer_id']}"
        status = ping(url, arcgis)
        print(f"{where} {status:28} {url[:90]}")
        if status.startswith("DEAD"):
            if ping_alternates(s.get("alternates"), where):
                continue          # rescued - not counted as dead
            bad += 1
    print(f"\n{bad} dead source(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
