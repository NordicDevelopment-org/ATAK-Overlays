# ATAK notes: how the KMZ is built and why

Conventions the writer follows so packs behave well in ATAK-CIV (and WinTAK /
iTAK), based on documented KML import behaviour. Verify on your device
version; ATAK's KML importer is strict about a few things.

## Structure

- **One KMZ per sector + `ALL.kmz`.** A state pack is ~11 files, not ~60, which is
  what Import Manager can actually handle. Each KMZ is a top-level entry in Overlay
  Manager. Inside a sector pack, `<Folder>` nesting becomes the tree of
  eye-toggles: `Layer/source (count) > class (count) > placemarks` - the sector
  level is dropped because the file is already named for it. `ALL.kmz` keeps the
  extra `Sector >` level on top.
- **`<visibility>0</visibility>`** on dense layers/folders so a 50k-parcel or
  20k-tower layer imports switched off and ATAK stays responsive. Toggle it
  on when you need it.
- **`<open>0</open>`** keeps the tree collapsed on import.
- **`doc.kml` at the root** of the zip plus `icons/*.png`. ATAK resolves
  relative icon hrefs inside the KMZ; remote http icons would need network.

## Styling

- Colors are KML `aabbggrr`. Line width scales with voltage class
  (`style_rules`), fill alpha is low so polygons stay readable over imagery.
- Point icons are 32 px PNGs generated in-process (`icons.py`): shape encodes
  the layer (star = plant, square = substation / station, triangle = tower or
  dam, hexagon = process plant, diamond = terminal / airport, plus = hospital,
  ring = storage). ATAK draws them at `IconStyle/scale`.
- `LabelStyle` scale 0.8: ATAK shows placemark names on the map for points;
  names are kept short (`"Name (1,146.4 MW)"`, `"Owner 345 kV"`).

## Attributes

- `<description>` is CDATA HTML: a headline table with unit-labelled canonical
  values, then "Source attributes (n)" with every raw field, then a footer
  with source / retrieval date / license. ATAK renders this in the details
  pane when you tap a placemark.
- `<ExtendedData>` carries the same canonical fields (`capacity_mw`,
  `voltage_kv`, ...) with display names for tools that read ExtendedData;
  raw fields are appended up to a cap of 80 to keep files small.
- `xcheck` (from `reconcile.md`) appears as an attribute when a feature was
  compared with another source: `agree`, `capacity_mw Δ 12% (...)`, or
  `unmatched in <source>`.

## Size and performance

- Coordinates are written with 6 decimals (`--precision 5` for ~1 m and ~15%
  smaller files). KMZ is deflate-compressed; a state-wide transmission layer
  is a few MB, a county building layer can be tens of MB.
- Above roughly 50k placemarks in one document ATAK gets sluggish; split by
  sector/layer (default) and keep dense layers hidden. For nation-scale packs
  build per state (`--aoi state:XX`) and load only what the mission needs.
- `MultiGeometry` is used for multi-part lines/polygons; polygons carry
  `<tessellate>1</tessellate>` so long edges follow the terrain.

## Import paths

- Import Manager > Local SD > select the `.kmz`, or copy files into
  `atak/imports/` (auto-import) or `atak/overlays/`.
- Re-importing a file with the same name replaces the overlay.
- iTAK and WinTAK import the same KMZ; folder toggles and description HTML
  render similarly, custom icon support differs by version.
