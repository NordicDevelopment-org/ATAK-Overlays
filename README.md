# ATAK-Overlays / overlaybuilder

Build **ATAK-ready critical-infrastructure overlays (KMZ)** from public GIS data,
for **any area**: one county today, a state, a multi-state region, the whole
US, another country, or the world.

Power plants with their MW and fuel. Substations and transmission lines by kV.
Gas, crude, HGL and product pipelines. Dams with height, storage and hazard
class. Wastewater plants with design flow. Registered towers with height and
owner. Hospitals, fire, police, EMS. Airports, rail, bridges, ports. Every
placemark carries the unit-labelled numbers, the full source attribute table,
and where it came from.

> Public, exterior data only. Facility locations and public operating
> attributes from EIA, USACE, EPA, FCC, Census, state portals, county GIS and
> OpenStreetMap. No interiors, no security details, no restricted feeds
> (see CONTRIBUTING.md).

## Quick start

```bash
git clone https://github.com/NordicDevelopment-org/ATAK-Overlays
cd ATAK-Overlays
pip install -e .                      # pyshp, pyproj, PyYAML; no GDAL
                                      # the catalog lives in the repo, not the wheel:
                                      # run from the checkout, or pass --catalog /path
                                      # (or set OVERLAYBUILDER_CATALOG)

overlaybuilder build --aoi county:27025           # Chisago County, MN - everything
overlaybuilder build --aoi state:MN --sectors energy water --jobs 6
overlaybuilder build --aoi region:upper-midwest --layers power_plants substations transmission_lines
overlaybuilder build --aoi us --layers power_plants dams
overlaybuilder build --aoi country:CA --sectors energy      # world tier (OpenStreetMap)
overlaybuilder build --aoi bbox:-93.2,45.3,-92.6,45.8
```

Output lands in `overlays/<aoi>/` (e.g. `overlays/us/mn/27025_chisago/`):

```
power_plants.kmz            EIA plants, folders by fuel, "Name (1,146.4 MW)"
power_plants__osm.kmz       OSM plants, same layer from a second source (the most
                            specific tier - county, then state, national, global -
                            owns the plain name; others get a provider suffix)
substations.kmz             folders by type, styled by max kV
transmission_lines.kmz      folders "345 kV (12)", line width by class
pipelines.kmz  dams.kmz  wastewater_treatment.kmz  comm_towers.kmz  hospitals.kmz ...
ALL.kmz                     one pack: Sector > Layer > class folders (eye-toggles)
manifest.json               counts, bbox, provenance per layer, build time
reconcile.md                EIA vs OSM plants, HIFLD vs OSM substations: matches, deltas, misses
ATTRIBUTION.txt             every source with its licence, the OpenStreetMap share-alike
                            notice, and the sources that failed - keep it with the pack
```

Load into ATAK: Import Manager > Local SD > pick the `.kmz` (or drop it in
`atak/imports/`). Toggle sectors, layers and classes with the eye in Overlay
Manager. Dense layers (parcels, buildings, towers, generators) start hidden.

## What it does

```
AOI ─► catalog tiers ─► drivers ─► normalize ─► clip ─► reconcile ─► KMZ / GeoJSON
        global (OSM, world)          arcgis      capacity_mw   county/state   EIA vs OSM     icons, styles,
        national/us (EIA, NID...)    file        voltage_kv    polygon        deltas > 5%    unit-labelled
        states/mn (MnGeo)            overpass    height_ft ...                               popups, folders
        states/mn/counties/27025     osm_pbf, fcc_asr, census_tiger
```

- **Scales by design.** Sources declare their coverage (`world`, `us`,
  `state:MN`, `county`); the AOI picks what applies and every driver scopes its
  query to it (ArcGIS envelope + attribute filters, Overpass bbox tiles or
  country area, whole-file downloads clipped to the boundary). See
  `docs/ARCHITECTURE.md`.
- **Keeps the numbers honest.** Canonical fields (`capacity_mw`, `voltage_kv`,
  `storage_acre_ft`, `flow_mgd`, `beds`, ...) are filled only from mapped source
  fields with explicit unit conversion (OSM volts to kV, `"1.2 MW"` strings,
  meters to feet). Nothing is invented; raw attributes are always kept.
- **Cross-checks sources.** When two sources describe the same thing (EIA and
  OSM power plants; HIFLD and OSM substations; NID and state dam inventories),
  features are matched by proximity and capacity/voltage/height deltas are
  reported in `reconcile.md` and stamped on the placemarks.
- **Survives dead endpoints.** Government services move constantly, so sources
  carry `alternates:` (mirrors, bulk downloads, other drivers). A failure falls
  through them automatically and the pack records which endpoint answered.
  `overlaybuilder doctor --aoi county:27025` checks them all up front.
- **Records provenance** on every document, placemark, manifest and GeoJSON.

## Sectors and layers

| sector | layers |
|---|---|
| Energy - Electric | power_plants, generators, nuclear_reactors, battery_storage, substations, transmission_lines, power_towers, service_territories, rto_regions |
| Energy - Oil & Gas | pipelines (gas / crude / HGL / products), compressor_stations, gas_processing, gas_storage, lng_terminals, refineries, fuel_terminals, ethanol_plants, biodiesel_plants, fuel_stations |
| Water | dams, levees, leveed_areas, water_treatment, wastewater_treatment, water_towers, water_wells, reservoirs, water_service_areas |
| Communications | comm_towers (FCC ASR + OSM), broadcast_towers, data_centers, telecom_exchanges |
| Emergency & Health | hospitals, urgent_care, fire_stations, police, ems, eoc, shelters, nursing_homes |
| Government | correctional, government, schools |
| Chemical & Hazmat | chemical_plants, hazmat_storage (anhydrous ammonia, chlorine, fertiliser), explosives_storage |
| Agriculture & Food | grain_storage, food_processing, agri_facilities, livestock_operations |
| Mining | mines (quarries, pits, shafts) |
| Transportation | airports, heliports, railways, rail_facilities, rail_crossings, bridges, ports |
| Base | county/state/city boundaries, roads, parcels, building_footprints, address_points |

Some sources ship **switched off**: their endpoint is documented but was never
verified, so they stay out of packs until you confirm them. Find the ones that
work on your network with `overlaybuilder doctor --aoi state:MN --include-disabled`,
then set `enabled: true` in the YAML.

`docs/SOURCES.md` lists every endpoint with its 2026 status, rating fields,
confidence and license. Important context: DHS shut down **HIFLD Open** in
August 2025, so its substation / transmission / emergency-services layers are
frozen archives here and OpenStreetMap is the maintained fallback; **EIA** is
the authoritative, maintained source for generation and fuel.

## Commands

```
overlaybuilder build     --aoi ... [--sectors ...] [--layers ...] [--exclude ...]
                         [--format kmz geojson] [--out overlays] [--flat] [--precision 6]
                         [--no-clip] [--no-combined] [--no-reconcile] [--no-fallbacks]
                         [--jobs N] [--max-per-host N] [--no-http-cache] [--fail-fast]
overlaybuilder sources   --aoi ... [--include-disabled]   what would build, by tier
overlaybuilder doctor    --aoi ... [--include-disabled]  probe every endpoint -> doctor.md
overlaybuilder demo                           synthetic sample pack, offline, to test ATAK rendering
overlaybuilder probe     <arcgis url>[/<id>] [--sample]   list layers / fields / count / one record
overlaybuilder validate                       lint the catalog offline
overlaybuilder list-drivers | list-regions | list-counties
```

`--aoi` accepts `county:FIPS`, `state:XX`, `region:NAME` (see
`catalog/regions.yaml`: upper-midwest, mn-neighbors, fema-region-5, miso-north, ...),
`us`, `conus`, `country:XX`, `bbox:W,S,E,N`. (`world` needs `osm_pbf` extracts;
the Overpass API cannot serve a planet-wide query.) Legacy
`--fips 27025` / `--state MN --county Chisago` still work.

## Scaling path

| target | how | status |
|---|---|---|
| Chisago County, MN | `--aoi county:27025` - global + national + MN + county tiers (~100 sources) | ready |
| Minnesota / Wisconsin / Iowa | `--aoi state:MN` etc. - state tiers for MnGeo, WI PSC/DNR, Iowa DNR | ready; add more state portal layers in `catalog/states/<abbr>/` |
| Surrounding states / regions | `--aoi region:mn-neighbors` (MN WI IA ND SD) | ready |
| United States | `--aoi us --layers ...` (national tier: EIA, NID, EPA, FCC, BTS) | ready for national layers; OSM layers are skipped at `us` scale - switch them to `osm_pbf` with a US extract |
| World | `--aoi country:XX` (OSM via Overpass area, tiled) | ready; add `catalog/national/<cc>/` for other countries' open data |
| Planet | switch the global sources to `osm_pbf` with a Geofabrik/planet extract | `--aoi world` is refused by Overpass and says so |

Prebuilt packs: push a tag like `pack-county-27025` or `pack-state-MN` and the
release workflow attaches the zip to a GitHub Release.

## Accuracy and verification

The catalog was assembled in September 2026 from published documentation and
search-verified endpoints; **the data hosts could not be reached from the
build environment**, so run `overlaybuilder doctor --aoi <your aoi>` once on a
connected machine before relying on a pack. It probes every endpoint without
downloading data, flags dead and login-only services, warns when a server no
longer has a column the catalog maps, names the alternate that works, and
writes `doctor.md`. Field-name defaults are
broad (EIA / HIFLD / NID / FCC / OSM spellings), so a renamed column degrades
to "attribute shown in the raw table" rather than a wrong number. Every
placemark shows the source, retrieval date and license.

## See it in ATAK first

```bash
overlaybuilder demo          # writes ./demo/DEMO_SAMPLE_ALL.kmz, no network needed
```

A small pack of **synthetic, clearly-labelled sample features** that exercises
every rendering path: sector folders, nested eye-toggles, embedded icons,
transmission lines styled by kV, dams by hazard class, a hidden dense layer,
and the full popup layout. Load it, confirm your ATAK version behaves, delete
it, then build the real thing. Nothing in it is real infrastructure.

## Add your county or state

One YAML, no code: see `CONTRIBUTING.md` and `docs/ADDING_A_SOURCE.md`.

## Data sources and licensing

MIT covers the **code**. Generated data carries each source's license, and
every pack ships an `ATTRIBUTION.txt` naming each layer's publisher, licence
and endpoint, so the terms travel with the file when you share it. US federal
data is public domain; OpenStreetMap is ODbL, meaning attribution always and
share-alike if you redistribute a *derived database* (a map or briefing made
from it does not trigger that, which is why OSM layers stay in their own
documents); state and county GIS carry that jurisdiction's terms. See
`docs/SOURCES.md`.
