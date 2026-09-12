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
        url = s.get("url")
        if not url:  # tiger/overpass build URLs dynamically; skip
            continue
        try:
            url = url.format(**SAMPLE_VARS)
        except (KeyError, IndexError):
            pass
        if "{month}" in url or "{year}" in url:
            from overlaybuilder.drivers.file import _month_candidates
            url = next(iter(_month_candidates(url)))     # newest monthly release
        arcgis = s["driver"] == "arcgis"
        if arcgis and s.get("layer_id") is not None:
            url = url.rstrip("/") + f"/{s['layer_id']}"
        status = ping(url, arcgis)
        print(f"{s['_file']:52} {s['layer']:22} {status:28} {url[:90]}")
        if status.startswith("DEAD"):
            bad += 1
    print(f"\n{bad} dead source(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
