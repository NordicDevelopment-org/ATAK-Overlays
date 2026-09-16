# Architecture

`overlaybuilder` turns public GIS sources into ATAK-ready KMZ packs of
critical infrastructure, for **any area of interest** - one county today, a
state, a multi-state region, the whole US, another country, or the world.

```
--aoi county:27025 | state:MN | region:upper-midwest | us | country:<ISO2> | bbox:W,S,E,N
        │
        ▼
   AOI (aoi.py)            kind, bbox envelope, boundary polygon, template vars
        │
        ▼
   catalog (catalog.py)    global/  national/us/  states/mn/  states/mn/counties/
        │                  -> ordered list of source specs that apply to this AOI
        ▼
   drivers (drivers/)      arcgis | file(shp,geojson,gpkg,csv,xlsx) | overpass | osm_pbf |
        │                  census_tiger | fcc_asr | demo
        │                  each -> LayerResult(features in EPSG:4326, provenance)
        │                  a failure falls through the source's `alternates:` mirrors
        ▼
   normalize (normalize.py) canonical, unit-labelled fields: capacity_mw, voltage_kv, ...
        │
        ▼
   clip + dedupe (build.py) boundary polygon from county/state layer; drop outside; dedupe ids
        │                  (--jobs N fetches sources concurrently after the boundary resolves,
        │                   capped per host so one server is never hammered)
        │
        ▼
   reconcile (reconcile.py) same entity from two sources -> match by proximity, flag deltas
        │
        ▼
   convert (convert/)      KMZ (sector > layer > class folders, icons, rich popups) | GeoJSON
        │
        ▼
   overlays/<aoi slug>/    <AOI>_<Sector>.kmz  ALL.kmz  manifest.json  reconcile.md
```

## The scaling model

There is no global GIS schema, so the tool separates **where data comes from**
(catalog tiers keyed by coverage) from **how it is scoped** (the AOI):

| Tier | Path | Coverage | Typical drivers | Examples |
|---|---|---|---|---|
| global | `catalog/global/` | world | overpass, osm_pbf | OSM power plants, substations, lines, pipelines, hospitals, towers |
| national | `catalog/national/us/` | US | arcgis, file, census_tiger | EIA plants, TIGER boundaries, NID dams, FCC ASR towers, BTS NTAD |
| state | `catalog/states/<abbr>/` | one state | arcgis, file | MnGeo Commons transmission lines, MN fire stations |
| county | `catalog/states/<abbr>/counties/` | one county | arcgis, file | county parcels, address points, fire districts |

Resolution for an AOI is *global + national (if US) + each covered state +
county (if county AOI)*, so a Chisago pack inherits everything above it. A
`coverage:` key on any source overrides the tier default, and `aoi_kinds:`
restricts sources that only make sense at some scales (TIGER roads are
per-county; OSM building footprints are too dense above a county).

Scoping is per driver:

- **arcgis**: AOI envelope sent as an `esriGeometryEnvelope` spatial filter,
  plus optional attribute filters templated per AOI kind
  (`where_by_aoi: {county: "COUNTYFIPS='{fips5}'", state: "STATE='{state_abbr}'",
  region: "STATE IN ({states_sql})"}`). One national layer serves every AOI.
- **overpass**: bbox split into `tile_deg` tiles, results merged and de-duplicated
  by OSM id; `country:XX` AOIs use an Overpass area on ISO3166-1.
- **osm_pbf**: local Geofabrik extract filtered by tags and bbox (offline,
  planet-scale).
- **file**: whole-file download with `filter: {field: STATE, in: ["{state_abbr}"]}`,
  then clipped.
- **census_tiger**: per-county or national files filtered by FIPS.

After fetching, every layer is **clipped** to the AOI's true boundary
(county/state polygon from TIGER; bbox otherwise) using a pure-Python
point-in-polygon test, so envelope over-fetch never leaks into the pack.

## Accuracy rules (PROJECT RULES)

1. **Never invent a value.** Canonical fields are filled only from a mapped
   source field that is present and parseable. Missing stays missing.
2. **Every unit is explicit.** `voltage@V` converts volts to kV;
   `plant:output:electrical@W` converts `"1.2 MW"`/`"500 kW"` to MW;
   `height@m` converts to ft. Popups and folder labels carry the unit.
3. **Raw fields are kept.** The full source attribute table rides in every
   placemark; a colliding raw key is preserved as `src_<key>`.
4. **Provenance on everything.** Source name, exact URL, license and retrieval
   date are in each KML Document, each placemark footer, `manifest.json`, and
   GeoJSON metadata.
5. **Cross-check, don't trust.** When two sources describe the same entity
   (`entity: power_plant` from EIA and from OSM), features are matched within
   `match_radius_m` and capacity/voltage/height/beds deltas over 5% are flagged
   in `reconcile.md` and stamped on the features as `xcheck`.
6. **A dead endpoint is visible, never silent.** Sources carry `alternates:`;
   the build tries them in order, stamps the fallback into provenance and the
   manifest, and reports the *primary's* error if they all fail.
   `overlaybuilder doctor --aoi ...` probes every endpoint for an AOI in about
   a minute and says which alternate would work.
7. **Public exterior data only.** No interior layouts, no access/security
   details, no non-public endpoints (see CONTRIBUTING.md).

## ATAK output conventions

- One KMZ per sector by default (`--group-by sector`), named `<AOI>_<Sector>.kmz`,
  plus `ALL.kmz`. `--group-by layer` gives the old one-file-per-source layout.
  Inside a sector pack the tree is Layer/source > class folders. Each folder
  is an eye-toggle in Overlay Manager; dense layers (parcels, buildings,
  towers, generators) import with `<visibility>0`.
- Points use PNG icons embedded in the KMZ (`icons/<style>.png`, generated by
  `icons.py`), so nothing depends on network image hosts. Shape + color encode
  the sector; `style_rules` add per-class styles (line width by kV).
- Names are templated (`"{name} ({capacity_mw})"`), popups show a headline
  table (unit-labelled), then all source attributes, then provenance.
  `ExtendedData` carries the canonical fields for tools that read it.
- Coordinates default to 6 decimals; `--precision 5` (~1 m) shrinks big packs.

## Adding scale

- **A new county**: one YAML under `catalog/states/<abbr>/counties/`.
- **A new state**: one or more YAML under `catalog/states/<abbr>/` for the
  state portal; national + global tiers already apply.
- **A new country**: `catalog/national/<cc>/` for its national open data;
  global OSM tier already applies (`--aoi country:XX`).
- **Nation/continent/planet runs**: switch `driver: overpass` to `driver: osm_pbf`
  with a Geofabrik extract; the selector syntax is identical.
- **Bulk packs**: the release workflow builds any AOI on a tag push
  (`pack-state-MN`) and attaches the zip to a GitHub Release.
