# Contributing

The fastest way to help: **add your county or state** so others can build
critical-infrastructure overlays for it. Most contributions are a single small
YAML file - no Python.

## Add a county (no code)

1. Find your county FIPS (state 2 digits + county 3).
   `overlaybuilder build --state XX --county Name` prints it.
2. Copy the reference entry
   `catalog/states/mn/counties/27025_chisago.yaml`
   to `catalog/states/<abbr>/counties/<FIPS>_<slug>.yaml`.
3. Point it at your county's public ArcGIS REST server (or a file source).
   `overlaybuilder probe <url>` lists layers and fields. See
   `docs/ADDING_A_SOURCE.md`.
4. Test: `overlaybuilder validate`, then `overlaybuilder build --aoi county:<FIPS>`.
5. Open a pull request. CI lints the catalog, runs tests, and pings your URLs.

You usually do NOT add power plants, substations, dams, hospitals, roads or
boundaries - the global (OSM) and national (EIA, NID, FCC, TIGER, ...) tiers
already cover every US county. Add what those tiers lack: parcels, address
points, fire/EMS districts, local utility layers, PSAP boundaries, and better
local versions of national layers.

## Add a state

Create `catalog/states/<abbr>/<topic>.yaml` pointing at the state GIS portal
(MnGeo Commons, GeoData@Wisconsin, Iowa Geodata, ...). State sources apply to
every county in the state and to `--aoi state:XX`.

## Scope guardrail (please read)

This project maps **public, exterior** infrastructure from **open, official or
community** sources, for situational awareness. Do not add:

- Interior building schematics, floor plans, or one-line diagrams.
- Internal layouts, security details, guard posts, or access information for
  any facility.
- Anything from a non-public or access-restricted endpoint (HIFLD Secure/GII,
  PHMSA NPMS downloads, utility customer portals), or data whose terms forbid
  redistribution.
- Personal data (owner names beyond what the public record shows, phone lists).

Facility locations, footprints, and their public operating attributes
(capacity, voltage, fuel, operator, beds, hazard class) from EIA, USACE, FCC,
state portals, county GIS, and OpenStreetMap are the target. PRs adding
restricted or sensitive material will be declined.

## Code contributions

- New driver? Add `src/overlaybuilder/drivers/<name>.py`, register with
  `@driver("<name>")`, return a `LayerResult` with geometry in EPSG:4326, and
  add an offline test with a fixture.
- New layer key? Add a style in `convert/kmz.py` (`LAYER_STYLE`) and headline
  fields in `normalize.py` (`HEADLINES`).
- Keep the data-handling rules: never invent fields or specs, record provenance
  on every layer, flag uncertainty in `notes`, keep raw fields, and don't
  silently overwrite conflicting source data.
- Run `overlaybuilder validate` and `pytest -q` before opening a PR.
