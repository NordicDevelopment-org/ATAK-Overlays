# Runbook: Chisago County pack, then scale out

Step-by-step from a clean machine to a verified ATAK pack. Times assume a
normal broadband connection; the first build downloads and caches several
national files (TIGER county polygons ~80 MB, NID CSV ~40 MB, FCC ASR
~120 MB, EIA zips a few MB each).

## 1. Install

```bash
git clone https://github.com/NordicDevelopment-org/ATAK-Overlays
cd ATAK-Overlays
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
overlaybuilder validate            # 0 problem(s)
pytest -q                          # all green, offline
```

## 2. See what will build

```bash
overlaybuilder sources --aoi county:27025
```

Prints every source by tier (global / national / state / county), driver and
sector. Use `--sectors energy water comm emergency transport base` or
`--layers power_plants substations ...` to narrow.

## 3. Verify the medium/low-confidence endpoints (10 minutes, once)

The catalog was assembled without live access to the data hosts. Probe the
ones marked medium/low in `docs/SOURCES.md` before trusting a pack:

```bash
overlaybuilder probe https://services7.arcgis.com/FGr1D95XCGALKXqM/arcgis/rest/services/Power_Plants_Testing/FeatureServer
overlaybuilder probe https://services7.arcgis.com/FGr1D95XCGALKXqM/arcgis/rest/services/Power_Plants_Testing/FeatureServer/0 --sample
overlaybuilder probe https://services.arcgis.com/G4S1dGvn7PIgYd6Y/ArcGIS/rest/services/HIFLD_electric_power_substations/FeatureServer
overlaybuilder probe https://geo.dot.gov/server/rest/services/Hosted/Natural_Gas_Pipelines_US_EIA/FeatureServer/0 --sample
overlaybuilder probe https://geodata.epa.gov/arcgis/rest/services/OEI/FRS_Wastewater/MapServer/1 --sample
overlaybuilder probe https://app.gisdata.mn.gov/arcgis/rest/services/EUSA/EUSA/FeatureServer/0 --sample
overlaybuilder probe https://gis.chisagocountymn.gov/arcgis/rest/services/DynamicData/MapServer
```

For each: confirm the layer exists, note `maxRecordCount`, and compare the
field list with the `fields:` block in the YAML. If a field is spelled
differently, edit the YAML (`catalog/national/us/*.yaml`) - the engine never
guesses a number from a field it cannot find, it just leaves that attribute
out of the headline.

If a service is dead (HTTP error or `"error"` JSON), switch to one of its
`alternates:` listed in the same YAML entry, or set `enabled: false`.

## 4. Build Chisago

```bash
overlaybuilder build --aoi county:27025 --format kmz geojson
```

Watch the summary. Typical outcomes per row:

| row status | meaning | action |
|---|---|---|
| `ok  N feat` | fetched, clipped, written | none |
| `ok  0 feat` | endpoint answered but nothing in the AOI, or a wrong filter/field | expected for LNG/refineries/ports in Chisago; otherwise probe the source |
| `ERROR: HTTP 4xx` | endpoint moved or needs a token | use an alternate / disable |
| `ERROR: no layer matched` | `layer_match` regex did not hit | probe the service, set `layer_id` |
| `ERROR: overpass ...` | Overpass busy | rerun; the driver retries and rotates endpoints |

Outputs: `overlays/us/mn/27025_chisago/` with one KMZ per layer, `ALL.kmz`,
`manifest.json`, `reconcile.md`.

Open `reconcile.md`: it lists EIA vs OSM plants and HIFLD vs OSM substations
that disagree by more than 5% or exist in only one source. Those are the
records to eyeball before a briefing.

## 5. Load into ATAK

1. Copy `ALL.kmz` (or the per-layer files) to the device:
   `/sdcard/atak/imports/` triggers auto-import, or use
   Import Manager > Local SD and pick the file.
2. Overlay Manager: expand the pack, toggle sectors / layers / classes with
   the eye. Dense layers start hidden.
3. Tap a placemark: the details pane shows the headline (Capacity, Voltage,
   Fuel, Operator, ...), the full source attribute table, and the source /
   retrieval date / license.

## 6. Scale out

```bash
overlaybuilder build --aoi state:MN --sectors energy water comm     # state-wide, ~10-20 min
overlaybuilder build --aoi region:mn-neighbors --layers power_plants substations transmission_lines pipelines dams
overlaybuilder build --aoi us --layers power_plants transmission_lines dams refineries   # national (non-OSM layers)
overlaybuilder build --aoi country:CA --sectors energy                                # OSM world tier
```

Notes:
- State builds use bbox tiles for OSM layers (a few dozen queries); national
  OSM layers are refused by the tile guard - use `osm_pbf` with a Geofabrik
  extract (`pip install osmium`, set `path:` on the source) instead.
- The FCC tower file and NID CSV download once and are cached in `.cache/`.
- Add `--precision 5` for large packs; add `--exclude building_footprints
  parcels` to skip the dense base layers.

## 7. Keep it fresh

- `python scripts/health_check.py` pings every catalog URL (CI runs it weekly).
- Re-run a build monthly; EIA updates monthly-quarterly, NID and FCC weekly.
  Compare `manifest.json` counts between builds to spot dead sources.
- Prebuilt packs: push a tag `pack-county-27025` or `pack-state-MN`; the
  release workflow attaches the zip to a GitHub Release.
