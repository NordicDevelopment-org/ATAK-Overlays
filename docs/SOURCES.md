# Source registry

Every source the catalog knows about, its status as of **September 2026**, and
how much to trust the URL/field names before you run `overlaybuilder probe` on
it. Status was researched via web search only (data hosts were unreachable
from the research environment), so **confirm before a production pack**.

Legend: confidence *high* = URL and field names both corroborated; *medium* =
URL corroborated, some fields from documentation; *low* = best available
knowledge, verify first. Status *live* / *frozen* (archive, no updates) /
*retired* / *restricted* (not usable).

## Key 2025-2026 facts

- **HIFLD Open is gone.** DHS shut the public portal on 26 Aug 2025. Remaining
  layers moved to HIFLD Secure (GII login + data-use agreement): not usable for
  a public overlay and deliberately excluded here. Frozen archives of the last
  open vintage exist (HIFLD Next at hifld.publicenvirodata.org, SeerAI on
  source.coop, DataLumos, NASA NCCS ArcGIS mirror at maps.nccs.nasa.gov).
- **EIA no longer publishes substations** (EIA FAQ 567). Substation coverage is
  OSM plus frozen HIFLD mirrors, cross-checked in `reconcile.md`.
- **PHMSA NPMS pipelines** are view-only for the public; EIA's major-pipeline
  layers (via USDOT BTS NTAD mirrors) are the public equivalent.
- **Public water supply well locations** are withheld by EPA, MN, WI. The tool
  maps service areas, treatment plants, towers and works instead.
- **Minnesota's state transmission layer** (Commerce EERA) was withdrawn in
  2022; Wisconsin and Iowa have no statewide equivalent. National archive + OSM.

## Global tier (any AOI on Earth) - OpenStreetMap, ODbL

| layer | OSM selectors | capacity / rating tags | status |
|---|---|---|---|
| power_plants | `power=plant` | `plant:output:electrical` (W/kW/MW/GW), `plant:source`, `plant:method` | live |
| generators | `power=generator` with output or thermal/hydro source | `generator:output:electrical`, `generator:source` | live |
| substations | `power=substation` | `voltage` (volts, `;`-separated), `substation=` | live |
| transmission_lines | `power=line`, `power=cable` | `voltage`, `circuits`, `cables`, `frequency` | live |
| power_towers | `power=tower` (county scale) | `height`, `design` | live |
| battery_storage | `power=plant/generator` + `*:source=battery`, `power=storage` | `*:output:electrical`, `*:storage` | live |
| pipelines | `man_made=pipeline` | `substance`, `diameter` (mm), `pressure`, `usage` | live |
| compressor_stations | `pipeline=substation`, pumping stations | - | live |
| gas_storage, refineries, fuel_terminals, ethanol_plants, fuel_stations | `man_made=gasometer/storage_tank`, `industrial=refinery/oil_depot/ethanol`, `amenity=fuel` | `content`, `product` | live |
| dams, water_treatment, wastewater_treatment, water_towers, water_wells, reservoirs, levees | `waterway=dam`, `man_made=water_works/wastewater_plant/water_tower/reservoir_covered/dyke`, `landuse=reservoir` | `height` (m) | live |
| comm_towers, broadcast_towers, data_centers, telecom_exchanges | `man_made=mast/tower` + `tower:type=communication`, `telecom=data_center/exchange` | `height` (m), `communication:*` | live |
| hospitals, urgent_care, fire_stations, police, ems, eoc, shelters, nursing_homes | `amenity=hospital/fire_station/police`, `emergency=ambulance_station`, ... | `beds`, `emergency`, `helipad` | live |
| correctional, government, schools | `amenity=prison/townhall/courthouse/school` | - | live |
| airports, heliports, railways, rail_facilities, bridges, ports, industrial | `aeroway=*`, `railway=*`, `man_made=bridge`, `landuse=port/industrial` | `tracks`, `ele`, `icao` | live |

Overpass etiquette: public instances allow ~2 concurrent queries; the driver
tiles large AOIs (`tile_deg`), waits `min_interval` between calls, rotates
endpoints, and backs off on 429/504. For a whole country or the planet use the
`osm_pbf` driver with a Geofabrik extract instead.

## US national tier

| layer | source | endpoint | rating fields | conf. | status |
|---|---|---|---|---|---|
| power_plants | EIA U.S. Energy Atlas - Power Plants | `services7.arcgis.com/FGr1D95XCGALKXqM/.../Power_Plants_Testing/FeatureServer/0` | Total_MW (net summer), Install_MW (nameplate), per-fuel *_MW, PrimSource, tech_desc | high | live, monthly-quarterly |
| power_plants (alt) | Esri fedmaps copy | `services2.arcgis.com/FiaPA4ga0iQKduv3/.../Power_Plants_in_the_US/FeatureServer/0` | Total_MW, Install_MW | high | live |
| power_plants (alt) | EIA bulk shapefile | `eia.gov/maps/map_data/PowerPlants_US_EIA.zip` | same | medium | live |
| generators (not yet wired) | EIA-860M monthly generator XLSX | `eia.gov/electricity/data/eia860m/xls/<month>_generator<YYYY>.xlsx` | Nameplate Capacity (MW), Technology, Status, Operating Year; join Plant ID = Plant_Code | medium | live (needs an xlsx reader: roadmap) |
| nuclear_reactors | FEMA / NRC reactor status | `gis.fema.gov/arcgis/rest/services/Partner/Nuclear_Plant_Power_Reactor_Status/FeatureServer/0` | power (% daily), epz10m, epz50m | high | live, daily |
| transmission_lines | HIFLD archive (Esri Federal User Community) | `services2.arcgis.com/FiaPA4ga0iQKduv3/.../US_Electric_Power_Transmission_Lines/FeatureServer/0` | VOLTAGE (kV, -999999 = unknown), VOLT_CLASS, OWNER, STATUS, INFERRED | high | frozen 2024-09-30 |
| transmission_lines (alt) | NASA NCCS HIFLD mirror | `maps.nccs.nasa.gov/mapping/rest/services/hifld_open/energy/FeatureServer/21` | same | medium | frozen |
| substations | HIFLD third-party mirror | `services.arcgis.com/G4S1dGvn7PIgYd6Y/.../HIFLD_electric_power_substations/FeatureServer` | MAX_VOLT, MIN_VOLT, LINES, TYPE (TAP = line tap) | low | frozen, vintage unknown |
| service_territories (off) | HIFLD ERST via NASA mirror | `.../hifld_open/energy/FeatureServer/26` | SUMMR_PEAK, WINTR_PEAK (MW), CUSTOMERS | medium | frozen |
| rto_regions (off) | EIA RTO Regions | `services7.arcgis.com/FGr1D95XCGALKXqM/ArcGIS/rest/services/RTO_Regions/FeatureServer/0` | - | medium | live |
| pipelines (gas) | EIA via BTS NTAD | `geo.dot.gov/server/rest/services/Hosted/Natural_Gas_Pipelines_US_EIA/FeatureServer/0` | TYPEPIPE, Operator, Status | high | live |
| pipelines (crude) | EIA via BTS NTAD | `geo.dot.gov/.../hosted/Crude_Oil_Pipelines_US_EIA/FeatureServer/0` | Operator | medium | live |
| pipelines (HGL) | EIA via BTS NTAD | `geo.dot.gov/.../hosted/HGL_Pipelines_US_EIA/FeatureServer/0` | Operator | high | live |
| pipelines (products) | EIA bulk shapefile | `eia.gov/maps/map_data/PetroleumProduct_Pipelines_US_EIA.zip` | Operator | medium | live |
| compressor_stations | HIFLD via NASA mirror | `.../hifld_open/energy/FeatureServer/6` | none (no HP) | medium | frozen |
| gas_processing | EIA bulk shapefile | `eia.gov/maps/map_data/NaturalGas_ProcessingPlants_US_EIA.zip` | Cap_MMcfd, Plant_Flow | medium | live |
| gas_storage | EIA Atlas | `services7.arcgis.com/FGr1D95XCGALKXqM/.../Natural_Gas_Underground_Storage/FeatureServer/39` | working gas (Bcf) | medium | live (may need token; zip alt) |
| lng_terminals | EIA bulk shapefile | `eia.gov/maps/map_data/LNG_ImportExportTerminals_US_EIA.zip` | Bcf/d capacities | medium | live |
| refineries | EIA bulk shapefile | `eia.gov/maps/map_data/Petroleum_Refineries_US_EIA.zip` | AD_Mbpd (x1000 = b/d), VDist_Mbpd | high | live, annual |
| fuel_terminals | EIA bulk shapefile | `eia.gov/maps/map_data/PetroleumProduct_Terminals_US_EIA.zip` | storage bbl | medium | live |
| ethanol_plants | EIA Atlas | `services7.arcgis.com/FGr1D95XCGALKXqM/.../Ethanol_Plants_US_EIA/FeatureServer/112` | Cap_Mmgal | high | live, annual |
| biodiesel_plants | EIA bulk shapefile | `eia.gov/maps/map_data/Biodiesel_Plants_US_EIA.zip` | Cap_Mmgal | medium | live |
| fuel_stations (off) | NREL/AFDC alt-fuel API | `developer.nrel.gov/api/alt-fuel-stations/v1.geojson?api_key=...` | EV ports, fuel types | medium | live, key required |
| dams | USACE National Inventory of Dams CSV | `nid.sec.usace.army.mil/api/nation/csv` | NID Height (Ft), Max Storage (Acre-Ft), Hazard Potential Classification, Condition Assessment | high | live |
| levees, leveed_areas | USACE National Levee Database | `geospatial.sec.usace.army.mil/dls/rest/services/NLD/Public/FeatureServer/15,16` | SYSTEM_NAME | medium | live |
| wastewater_treatment | EPA FRS / ICIS-NPDES | `geodata.epa.gov/arcgis/rest/services/OEI/FRS_Wastewater/MapServer/1` | CWP_TOTAL_DESIGN_FLOW_NMBR (MGD), CWP_FACILITY_TYPE_INDICATOR | high | live |
| water_service_areas | EPA CWS Service Area Boundaries | `services.arcgis.com/cJ9YHowT8TU7DUyn/.../Water_System_Boundaries/FeatureServer/0` | Population Served Count | medium | live |
| comm_towers | FCC Antenna Structure Registration | `data.fcc.gov/download/pub/uls/complete/r_tower.zip` | height AGL/AMSL (m), structure type, owner, status | medium | live, weekly |
| hospitals, fire_stations, police, ems, eoc (off) | HIFLD via NASA mirror (placeholders) | `maps.nccs.nasa.gov/mapping/rest/services/hifld_open/...` | BEDS, TRAUMA, HELIPAD | low | frozen; confirm path |
| airports | FAA AIS Airports | `services6.arcgis.com/ssFJjBXIUyZDrSYZ/.../Airports/FeatureServer/0` | TYPE_CODE, ELEVATION | medium | live |
| railways | BTS NTAD North American Rail Network | `geo.dot.gov/.../Hosted/North_American_Rail_Network_Lines/FeatureServer/0` | RROWNER1, TRACKS, NET | medium | live |
| rail_facilities | BTS NTAD Amtrak Stations | `geo.dot.gov/.../Hosted/Amtrak_Stations/FeatureServer/0` | - | medium | live |
| bridges | FHWA NBI via NTAD | `geo.dot.gov/.../Hosted/National_Bridge_Inventory/FeatureServer/0` | YEAR_BUILT_027, ADT_029, condition | medium | live |
| ports | BTS NTAD Principal Ports | `geo.dot.gov/.../Hosted/Principal_Ports/FeatureServer/0` | - | medium | live |
| roads, county/state/city boundaries | Census TIGER/Line | `www2.census.gov/geo/tiger/TIGER2024/...` | RTTYP, MTFCC | high | live, annual |
| building_footprints | OSM via Overpass (county scale) | - | - | high | live |

Not wired yet (roadmap): USGS stream gauges (NWIS RDB / OGC API), EPA ECHO
SDWIS systems (attributes only), EIA-860M generator XLSX, NLD pump stations,
WRI Global Power Plant Database (CC BY, 2021 vintage) for non-US plants.

## State tiers

| state | layer | source | endpoint | conf. |
|---|---|---|---|---|
| MN | service_territories | MN PUC Electric Utility Service Areas (MnGeo) | `app.gisdata.mn.gov/arcgis/rest/services/EUSA/EUSA/FeatureServer/0` | medium |
| MN | wastewater_treatment | MPCA Wastewater Facilities | `enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_pca/util_wastewater_facilities/FeatureServer/0` | medium |
| MN | dams | MN DNR Inventory of Dams (GeoPackage) | `resources.gisdata.mn.gov/pub/gdrs/data/pub/us_mn_state_dnr/struc_mn_dams_inventory_pub/gpkg_struc_mn_dams_inventory_pub.zip` | medium |
| WI | service_territories | WI PSC Electric Service Territories (3 layers) | `maps.psc.wi.gov/server/rest/services/Electric/PSC_ElectricServiceTerritories/MapServer/0-2` | high |
| WI | dams | WI DNR Repository of Dams | `dnrmaps.wi.gov/arcgis2/rest/services/WT_DAM/WT_Dam_WTM_Ext/MapServer` | medium |
| IA | wastewater_treatment | Iowa DNR NPDES facilities | `programs.iowadnr.gov/geospatial/rest/services/OneStop/QueryEnvFacs/MapServer/12` | medium |
| IA | service_territories (off) | Iowa Utilities Commission boundaries | resolve from geodata.iowa.gov hub item | low |

MnGeo Commons pattern for adding more MN layers:
`https://resources.gisdata.mn.gov/pub/gdrs/data/pub/<agency>/<dataset>/{shp,gpkg,fgdb}_<dataset>.zip`
with metadata at `.../<dataset>/metadata/metadata.html`.

## County tier

| county | layer | source | endpoint |
|---|---|---|---|
| Chisago, MN (27025) | parcels | Chisago County GIS DynamicData | `gis.chisagocountymn.gov/arcgis/rest/services/DynamicData/MapServer` (layer matched by name) |
| Chisago, MN (27025) | address_points, fire_stations (off) | same server - enable after `probe` confirms layer names | |

## Licensing summary

| source family | terms | redistribution in packs |
|---|---|---|
| EIA, Census TIGER, USACE NID/NLD, EPA, FCC, FEMA/NRC, BTS/FAA | US Government work, public domain | yes, cite the agency |
| HIFLD archives / mirrors | public domain data; mirror host terms (Esri ArcGIS Online, NASA) apply to the hosted copy | yes for the data; do not scrape mirrors at volume |
| OpenStreetMap | ODbL 1.0: attribution + share-alike for derived databases | yes, keep OSM layers as separate documents and credit "(c) OpenStreetMap contributors" |
| MnGeo Commons, WI DNR / PSC, Iowa DNR | state open data, attribution requested | yes |
| County GIS | that county's terms | verify per county |
| HIFLD Secure, PHMSA NPMS, utility portals | restricted | never |
