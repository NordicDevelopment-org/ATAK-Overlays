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
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from overlaybuilder import catalog  # noqa: E402
from overlaybuilder.aoi import parse_aoi  # noqa: E402

CATALOG = os.path.join(ROOT, "catalog")
SAMPLE_VARS = parse_aoi("state:MN").template_vars()


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
    try:
        u = url + ("&" if "?" in url else "?") + "f=json" if arcgis else url
        req = Request(u, headers={"User-Agent": "overlaybuilder-health/0.2"}, method="GET")
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
        return f"DEAD {e}"


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
            bad += 1
    print(f"\n{bad} dead source(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
