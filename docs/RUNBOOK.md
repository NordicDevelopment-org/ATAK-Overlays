# Runbook: Chisago County pack, then scale out

Step-by-step from a clean machine to a verified ATAK pack. Times assume a
normal broadband connection; the first build downloads and caches several
national files (TIGER county polygons ~80 MB, NID CSV ~40 MB, FCC ASR
~120 MB, EIA zips a few MB each).

## 0. See the output shape first (no network)

```bash
overlaybuilder demo
```

Writes `demo/DEMO_SAMPLE_ALL.kmz` from **synthetic** sample data. Load it into
ATAK to confirm the folder tree, eye-toggles, icons, voltage-styled lines and
popup layout look right on your device before spending a real build. Delete it
afterwards - none of it is real infrastructure.

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

## 3. Check the endpoints (one minute)

The catalog was assembled without live access to the data hosts, so verify
before trusting a pack:

```bash
overlaybuilder doctor --aoi county:27025
```

It probes every source without downloading data and writes `doctor.md`:

| status | meaning | what to do |
|---|---|---|
| `ok` | service answered, layer found, count returned | nothing |
| `warn` | answered, but a column the catalog maps is missing | fix that source's `fields:` block; the attribute is just absent from the popup headline until you do |
| `auth` | needs a login, token or API key | leave disabled, or export the key the YAML names |
| `dead` | gone, renamed, or unreachable | doctor names the alternate that works; builds already fall back to it automatically |
| `skip` | no probe for that driver | nothing |

Exit code 1 means at least one source has no working endpoint at all; the
report lists them with the YAML file to edit.

Some sources ship switched off because their endpoint was never verified
(EPA chemical facilities, MSHA mines, the MnGeo emergency-services layers).
Test those too and turn on whichever answer:

```bash
overlaybuilder doctor --aoi state:MN --include-disabled
``` To inspect one service by hand:

```bash
overlaybuilder probe https://gis.chisagocountymn.gov/arcgis/rest/services/DynamicData/MapServer
overlaybuilder probe https://.../FeatureServer/0 --sample     # fields + one record
```

## 4. Build Chisago

```bash
overlaybuilder build --aoi county:27025 --format kmz geojson
```

Watch the summary. Typical outcomes per row:

| row status | meaning | action |
|---|---|---|
| `ok  N feat` | fetched, clipped, written | none |
| `ok  0 feat` | endpoint answered but nothing in the AOI, or a wrong filter/field | expected for LNG/refineries/ports in Chisago; otherwise probe the source |
| `ok  N feat  (fallback endpoint)` | the primary failed, a catalog alternate answered | check `doctor.md`; promote the alternate in the YAML |
| `ERROR: HTTP 4xx` | endpoint moved or needs a token, and no alternate worked | run `doctor`, then edit the YAML |
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
overlaybuilder build --aoi state:MN --sectors energy water comm --jobs 6   # state-wide
overlaybuilder build --aoi region:mn-neighbors --layers power_plants substations transmission_lines pipelines dams
overlaybuilder build --aoi us --layers power_plants transmission_lines dams refineries   # national (non-OSM layers)
overlaybuilder build --aoi country:CA --sectors energy                                # OSM world tier
```

Notes:
- `--jobs N` fetches N sources at once. Boundary layers always run first (they
  scope everything else), and no more than `--max-per-host` (default 2)
  requests hit any one server, with Overpass spacing preserved - so a state
  build finishes in a fraction of the time without hammering anyone. Output is
  identical to a serial build, including layer order.
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
