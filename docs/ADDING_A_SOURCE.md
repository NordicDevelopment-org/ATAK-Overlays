# Adding a source (catalog reference)

A catalog file has a top-level `sources:` list (and an optional `defaults:`
map merged into every source). Each source is one logical layer pulled by one
driver.

| tier | path | coverage default |
|---|---|---|
| global | `catalog/global/*.yaml` | world |
| national | `catalog/national/us/*.yaml` | us |
| state | `catalog/states/<abbr>/*.yaml` | state (the folder's state) |
| county | `catalog/states/<abbr>/counties/<FIPS>_<slug>.yaml` | county |

## Common fields (all drivers)

| field | required | meaning |
|---|---|---|
| `layer` | yes | logical layer key: `power_plants`, `substations`, `transmission_lines`, `pipelines`, `dams`, `hospitals`, ... (styling, icons, headline fields, default visibility key off this; see `convert/kmz.py` LAYER_STYLE and `normalize.py` HEADLINES) |
| `driver` | yes | `arcgis`, `file`, `overpass`, `osm_pbf`, `census_tiger` |
| `id` | no | unique id for logs/manifest (default `<layer>@<file>`) |
| `sector` | no | which sector KMZ the layer lands in, and its folder in `ALL.kmz` (default from the layer key) |
| `coverage` | no | `world`, `us`, `state`, `state:MN`, `country:<ISO2>`, `county` (default from tier) |
| `aoi_kinds` | no | restrict to some AOI kinds, e.g. `[county, bbox]` for dense layers |
| `priority` | no | build order (boundaries 0, default 50) |
| `enabled` | no | `false` keeps the entry documented but skipped |
| `group_by` | no | fields folded into nested eye-toggles; first present + low-cardinality wins. Canonical keys (`voltage_kv`, `fuel`) get unit-labelled, numerically sorted folders |
| `name` | no | placemark title template over canonical + raw fields: `"{name} ({capacity_mw})"` |
| `fields` | no | canonical field mapping (below) |
| `entity` | no | entity type for cross-source reconcile (`power_plant`, `substation`, `dam`, `hospital`...) |
| `match_radius_m` | no | reconcile match radius (default 800) |
| `hidden` | no | import hidden in ATAK |
| `style` | no | `{color: aabbggrr, fill: aabbggrr, width: n, icon: circle|square|diamond|triangle|hexagon|ring|plus|star}` |
| `style_rules` | no | list of `{when: "voltage_kv >= 345", color, width, icon, fill}`; first match wins |
| `title` | no | document/folder title (default layer key) |
| `provider` | no | short source tag used in file names when a layer has several sources (`eia`, `osm`, `hifld`) |
| `source_name`, `source_url`, `license`, `notes` | license required for non-OSM/TIGER | provenance written into every output |
| `alternates` | no | backup endpoints, tried in order when the primary fails (see below) |
| `confidence` | no | `high`/`medium`/`low` - how well the endpoint and field names were verified; shown by `doctor` |

### `alternates:` - backup endpoints

Government endpoints move. List mirrors and bulk downloads and the build falls
back automatically when the primary raises:

```yaml
- layer: dams
  driver: file
  url: https://nid.sec.usace.army.mil/api/nation/csv
  format: csv
  fields: {name: {from: ["Dam Name"]}}
  alternates:
    - driver: arcgis                       # a different driver is fine
      url: https://geospatial.sec.usace.army.mil/dls/rest/services/NID/National_Inventory_of_Dams_Public_Service/FeatureServer
      layer_id: 0
      note: USACE FeatureServer
    - driver: arcgis
      url: https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/NID_v1/FeatureServer
      layer_id: 0
      note: Esri weekly cache
```

Rules:

- An alternate **inherits** everything from its parent: `fields`, `group_by`,
  `style_rules`, `entity`, `name`, `license`, `sector`.
- When it sets `url` or `driver`, the parent's endpoint-selection keys
  (`layer_id`, `layer_match`, `product`, `table`, `zip_member`, `format`,
  `sheet`, `header_row`, `lat_field`, `lon_field`, `skip_lines`) are cleared so
  a stale layer id never rides along. Attribute filters (`where`,
  `where_by_aoi`) are inherited on purpose - they express the AOI.
- `note:` is appended to the layer's provenance so the output says which
  endpoint answered. A fallback build also stamps
  `FALLBACK: primary source <id> failed (...)` into the KMZ provenance and
  sets `"fallback": true` in `manifest.json`.
- Build with `--no-fallbacks` to make a primary failure fatal for that layer,
  and check them all ahead of time with `overlaybuilder doctor --aoi ...`.

### `fields:` canonical mapping

```yaml
fields:
  capacity_mw: {from: [Total_MW, Install_MW]}          # first present wins
  voltage_kv:  {from: ["voltage@V"]}                    # @unit converts (V->kV, W/kW/GW->MW, m->ft, mm->in)
  operator:    {from: [Utility_Name]}
  type:        {const: "peaker"}                        # constant
  height_ft:   false                                    # suppress a default mapping
```

Canonical keys and units are listed in `normalize.py` (`CANONICAL`). Every key
has default candidates (`DEFAULT_FROM`) covering EIA / HIFLD / NID / FCC / OSM
naming, so most sources need only a few overrides. Numbers with OSM suffixes
(`"1.2 MW"`, `"345 kV"`, `"115000;34500"`) parse correctly.

Field lookup tries three passes per candidate: exact spelling, then
case-insensitive, then ignoring punctuation - so a candidate `DAM_NAME` still
finds a column named `Dam Name`, and a server that re-spells `Cap_MMcfd` as
`CAP MMCFD` keeps working. Add `nulls: [-999999]` to a mapping to treat a
source's sentinel as missing (common in HIFLD voltage columns).

## Driver: `arcgis`

```yaml
- layer: substations
  driver: arcgis
  url: https://<host>/arcgis/rest/services/<Service>/FeatureServer
  layer_id: 0                # explicit id, OR:
  layer_match: "substation"  # case-insensitive regex on the layer name
  where_by_aoi:              # attribute filter by AOI kind (templated)
    county: "COUNTYFIPS = '{fips5}'"
    state:  "STATE = '{state_abbr}'"
    region: "STATE IN ({states_sql})"
  where: "STATUS <> 'RETIRED'"   # used when no where_by_aoi key matches
  spatial: bbox              # bbox (default) sends the AOI envelope; none = attribute filter only
  out_fields: "*"
  token_env: MY_TOKEN        # for services that need a token (read from env, never stored)
  page: 2000
  min_interval: 0.5          # seconds between requests
```

Template vars: `{fips5} {state_fp} {county_fp} {county_name} {state_abbr}
{state_name} {states_sql} {country}`. Tip: `overlaybuilder probe <url>` lists
layers; `overlaybuilder probe <url>/<id> --sample` prints fields and one
record. Use `layer_match` unless the id is stable.

## Driver: `file`

```yaml
- layer: dams
  driver: file
  url: https://host/path/dams_{state_abbr}.csv     # templated; .zip/.geojson/.gpkg/.csv
  format: csv                 # auto | shp | geojson | gpkg | csv
  lat_field: Latitude
  lon_field: Longitude
  delimiter: ","
  skip_lines: 0
  zip_member: "RA\\.dat$"      # pick a file inside a zip
  header: [REC, REG, ...]     # for headerless files
  dms: {lat: [LAT_D, LAT_M, LAT_S, LAT_H], lon: [LON_D, LON_M, LON_S, LON_H]}
  table: dams                 # gpkg table
  filter: {field: STATE, in: ["{state_abbr}"]}
```

Zipped shapefiles reproject from `.prj` (default NAD83). GeoJSON is assumed
WGS84. GeoPackage reprojects from its SRS. No GDAL needed.

## Driver: `overpass` (OSM, world tier)

```yaml
- layer: power_plants
  driver: overpass
  tags: ["power=plant", "power=generator;generator:source=nuclear"]   # OR of AND-groups
  elements: nwr              # n, w, r, nw, wr, nwr
  geometry: auto             # auto | point | line | polygon
  represent: both            # shape | point | both (polygon + centre marker)
  tile_deg: 1.0
  overpass_timeout: 180
  endpoints: [https://overpass-api.de/api/interpreter]
```

Selector syntax: `key=value`, `key!=value`, `key~regex`, `key` (present),
`key>=number` (client-side numeric, OSM suffixes understood). Write a
case-insensitive regex as `key~(?i)pattern`: Overpass itself uses POSIX
regexes and rejects inline flags, so the driver translates it to Overpass's
own `,i` modifier (and to `re.I` for the `osm_pbf` driver). Country AOIs use
an Overpass area; `world` is refused - use `osm_pbf`.

## Driver: `osm_pbf` (OSM, offline extracts)

Same `tags`/`geometry`/`represent` as overpass, plus `path:` or `url:` to an
`.osm.pbf` (e.g. `https://download.geofabrik.de/north-america/us/minnesota-latest.osm.pbf`).
Needs `pip install osmium`.

## Driver: `census_tiger` (US baseline)

`product: county | state | cousub | roads`. Year from `--tiger-year`.

## Rules

- Never invent field names. Use `layer_match`, default field candidates, and
  `group_by` fallbacks rather than asserting; confirm with `probe`.
- Always set `license` and `source_name` for non-TIGER/OSM sources.
- Public, exterior, non-sensitive layers only (see CONTRIBUTING).
- Run `overlaybuilder validate` and `pytest -q` before opening a PR.
