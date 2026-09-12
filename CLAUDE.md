# overlaybuilder - working notes

ATAK-ready KMZ overlays of public critical infrastructure, for any area of
interest: one county, a state, a region, the US, a country, or the world.

## Data-handling rules (non-negotiable)

1. **Never invent a value.** A canonical field is filled only from a mapped
   source field that is present and parseable. Missing stays missing. Do not
   fabricate endpoints, layer ids, field names, coordinates or capacities. If
   an endpoint or column is unverified, say so in `confidence:` and `notes:`,
   and ship it `enabled: false` rather than guessing.
2. **Units are explicit.** Every `FIELD@unit` must have a conversion in
   `normalize._UNIT_TO_CANON`; an unknown unit raises rather than silently
   mis-scaling. `validate` catches it offline.
3. **Keep raw attributes.** The full source attribute table rides in every
   placemark; a colliding raw key is preserved as `src_<key>`.
4. **Provenance everywhere.** Source name, exact URL, licence and retrieval
   date in each KML Document, each placemark footer, `manifest.json` and the
   GeoJSON metadata. A fallback endpoint is recorded as such.
5. **Cross-check, don't trust.** Two sources for one `entity:` are matched by
   proximity and their capacity/voltage/height/bed deltas reported in
   `reconcile.md`. Line layers are excluded (midpoints are meaningless).
6. **Public, exterior data only.** No interior layouts, no security details,
   no restricted feeds (HIFLD Secure, PHMSA NPMS downloads, utility portals).
   See CONTRIBUTING.md.

## Layout

```
src/overlaybuilder/
  aoi.py         AOI parsing, state envelopes, clipping, BoundaryIndex
  catalog.py     tiered source resolution + offline lint (validate)
  normalize.py   canonical unit-labelled fields, name templates, headlines
  build.py       orchestrator: boundary first, then parallel fetch, reconcile, write
  reconcile.py   cross-source matching and the report
  probe.py       cheap liveness probes per driver
  doctor.py      health report over every source for an AOI
  demo.py        synthetic sample pack (offline) + the `demo` driver
  convert/kmz.py KML/KMZ writer: sector > layer > class folders, icons, popups
  drivers/       arcgis, file (shp/geojson/gpkg/csv/xlsx), overpass, osm_pbf,
                 census_tiger, fcc_asr
catalog/         global/ national/us/ states/<abbr>/ states/<abbr>/counties/
```

## Before you commit

```bash
python3 -m pytest -q                 # must be green
overlaybuilder validate              # catalog lint, must be 0 problems
overlaybuilder demo                  # sample pack still builds and parses
```

CI runs the suite on Python 3.10-3.13 (the versions actually tested), so keep
the syntax and standard-library usage compatible with the oldest of those.

## Conventions that bite

- **Overpass element types** are `node`/`way`/`rel`/`nwr`/`nw`/`nr`/`wr`.
  Bare `n`, `w`, `r` are invalid QL; the driver normalises them, `validate`
  rejects anything else.
- **Overpass regexes are POSIX ERE**, which has no inline flags. Write
  `key~(?i)pattern` in the catalog and the driver emits Overpass's `,i`
  modifier; never let `(?i)` reach the server.
- **An alternate endpoint rewrites provenance.** A pack must never claim it
  came from the primary URL when a mirror answered.
- **The most specific tier owns the plain document name** (county, then state,
  national, global). EIA writes `power_plants.kmz`, OSM writes
  `power_plants__osm.kmz`.
- **A lowercase OSM tag candidate must be written `!tag@unit`** so it is
  matched exact-spelling-only; otherwise it captures an unrelated uppercase
  column (`length@m` would eat a source's `LENGTH` in feet).
- **Field lookup is three passes**: exact, case-insensitive, then ignoring
  punctuation, so `DAM_NAME` finds `Dam Name`.
- **`alternates:`** are real fallbacks, not documentation. An alternate that
  changes `url`/`driver` clears the parent's endpoint-selection keys.
- **Boundary layers build first** and set the clip polygon; everything else
  can then run in parallel (`--jobs N`, capped per host).

## State of the data

The catalog was assembled in September 2026 from documentation and
search-verified endpoints; the data hosts were unreachable from the build
environment, so nothing has been confirmed live. `docs/SOURCES.md` records
per-source confidence and 2026 status. Run
`overlaybuilder doctor --aoi <aoi> [--include-disabled]` on a connected
machine before trusting a pack.

HIFLD Open shut down in August 2025: its substation, transmission and
emergency-services layers are frozen archives here, with OpenStreetMap and
USGS/FEMA/NCES as the maintained alternatives.
