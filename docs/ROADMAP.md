# Roadmap

## v0.2 (this release) - critical infrastructure, any AOI
- AOI model (county / state / region / us / country / bbox / world), clipping
- Tiered catalog: global OSM, US national (EIA, FEMA/NRC, HIFLD archives,
  USDOT NTAD, NID, NLD, EPA, FCC, FAA), MN / WI / IA state tiers, Chisago County
- Drivers: arcgis (envelope + AOI filters), file (shp / geojson / gpkg / csv /
  xlsx, monthly-URL resolution), overpass (tiled, relations), osm_pbf, fcc_asr,
  census_tiger
- Canonical unit-labelled attributes, style rules, embedded icons, provenance
- Cross-source reconcile report, manifest, release packs by AOI

## v0.3 - verification and depth
- **Live verification pass**: run `probe`/builds from a machine with internet
  and pin `layer_id`s / field names for every medium- and low-confidence
  source in `docs/SOURCES.md`; replace the HIFLD emergency-services
  placeholders with a confirmed mirror or state sources
- Generator join: attach EIA-860M unit lists (count, sizes, fuel) to the
  plant popup instead of separate points
- USGS stream gauges (NWIS RDB and OGC API), NLD pump stations / floodwalls,
  EPA SDWIS attributes joined to water service areas by PWSID
- More state tiers: ND, SD, IL, MI, NE, MO portals (hospitals, fire, EOCs,
  PSAP boundaries, state dam inventories, state transmission where published)
- More county entries (Washington, Anoka, Isanti, Pine, Polk WI, Burnett WI)
- `--jobs N` parallel source fetches; resumable builds from cache

## v0.4 - nation and world scale
- `osm_pbf` as the default for `us`, `conus`, and `country:` AOIs with
  automatic Geofabrik extract selection and caching
- WRI Global Power Plant Database and national open-data tiers for CA, MX, EU
- Per-state pack matrix in CI (`pack-state-*` for all 50) with size budgets
- Vector tiles (PMTiles) for state-wide dense layers; KMZ `NetworkLink`
  packs that reference per-county KMZs
- Change detection: diff a new build against the previous manifest and emit
  an "added / removed / changed capacity" report

## Ideas
- ATAK data package (`.zip` with MANIFEST) wrapping the KMZs plus a
  README for one-tap import
- CoT / TAK Server publishing of selected layers
- Icon set with sector glyphs (SVG-rendered at build time)
