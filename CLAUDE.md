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
environment, so almost nothing has been confirmed live.

**Confirmed live 2026-09-14, on an Android device via Termux** (the build
environment still cannot reach these):
- TIGERweb `State_County/MapServer/1` - 87 Minnesota counties, carrying GEOID,
  NAME, BASENAME, AREALAND, AREAWATER. The service stacks four vintages
  (top level = Current, plus groups "BAS 2026", "ACS 2025", "Census 2020") and
  repeats each at seven generalization levels, which is why "Counties" appears
  28 times. `statepacks/tiger_diagnose.py` prints the tree and tests each one.
- A full MN county pack built from it renders correctly in ATAK-CIV: boundaries
  follow the real county lines, the metadata popup shows every field with its
  vintage, and ALAND-derived land area checks out (Kanabec 521.6 sq mi).
- `api.census.gov` ACS 5-year, **with a key**: 87 Minnesota county records,
  population and housing populated. A keyless request answers HTTP 200 with an
  HTML page titled "Missing Key" - not an error status and not JSON - so the
  key is required, free, and instant at
  https://api.census.gov/data/key_signup.html
- Wikidata county seats: 87 of 87 Minnesota counties resolved, spot-checked
  correct (Chisago/Center City, Kanabec/Mora, St. Louis/Duluth).
- Boundary detail: layers 1/3/5/7/9/11/13 all return an identical 1906 vertices
  for the same county, so the repeats within a vintage are not generalization
  levels and there is no sharper layer to switch to. Layer 67 has 1928 (+1.2%)
  but is the older Census 2020 vintage. `docs/SOURCES.md` records
per-source confidence and 2026 status. Run
`overlaybuilder doctor --aoi <aoi> [--include-disabled]` on a connected
machine before trusting a pack.

HIFLD Open shut down in August 2025. **Its NASA NCCS re-host
(`maps.nccs.nasa.gov`) stopped resolving entirely** - verified 2026-09-14
on-device: `ping: unknown host`, `curl: Could not resolve host`. Not blocked,
not an IPv6 problem; the host is gone from DNS.

The 13 sources whose primary url was that host are `enabled: false` with
`confidence: dead`. One more (`transmission_lines@hifld-archive`) keeps it only
as an alternate and stays enabled, since its primary is live. Coverage after
disabling them, for `state:MN`:

- Still covered by USGS and/or OSM: hospitals, fire_stations, police, ems,
  nursing_homes, urgent_care, correctional, eoc, comm_towers,
  service_territories, compressor_stations.
- `psap` lost its only source. The FCC Master PSAP Registry is tabular only,
  so there is no polygon replacement; the layer has no live source.
- `pharmacies` was already disabled before this.

`validate` now rejects duplicate YAML keys. Appending a second `notes:` or
`confidence:` to a source used to parse fine and silently discard the first -
which is how a provenance note or a licence string disappears without a word.
