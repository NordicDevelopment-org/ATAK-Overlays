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
the syntax and standard-library usage compatible with the oldest of those. Run
all four before pushing - a green 3.11 is not a green gate.

**A green suite is not the same as a tested behaviour.** After fixing anything
that a test should have caught, add it to `statepacks/tools/mutation_check.py`
and run it:

```bash
python3 statepacks/tools/mutation_check.py    # break each rule, expect a failure
```

It breaks one behaviour at a time and checks that some test notices. Six
entries survived when they were first written - including "if no sheriff phone,
then don't" and `OSM_JOBS = 1`, which silently undoes concurrent tile fetching.
It reverts with `git checkout --`, so commit first; it refuses to run against a
dirty tree because it has eaten uncommitted work once.

## Picking this up

`statepacks/` is the working end of this repo right now: ATAK county packs,
one per state, built on a phone via Termux. Minnesota is verified end to end;
nothing else has been fetched live.

**Read `docs/PLAN.md` first** - the working list, kept current as things
land, with what is done, what is next and what has been settled.

**Then `statepacks/README.md` §11.** It carries what is verified, what is
untried, the decisions that are the maintainer's rather than the code's, and a
short list of things that look like bugs but are deliberate - don't "fix"
those.

`docs/TROUBLESHOOTING.md` is the one to open when something is wrong on the
tablet. `termux/atak-list.sh` opens every installed overlay and reports what is
inside it and what is fighting with what.

Everything is on `claude/critical-infra-kmz-overlays-cnv8nj`. No PR has been
opened; don't open one unless asked.

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
- **OpenStreetMap sheriff / primary LE, measured for MN** (`seed_le_contacts.py
  --gaps`). This is the ceiling, not a work in progress:

  | | |
  |---|---|
  | police features in the state box | 517 |
  | ...carrying a phone number | 32 |
  | counties with an agency, filter `sherr?iff` | 53 of 87 |
  | ...plus `law enforcement cent\|county jail` | 63 |
  | ...plus `county public safety\|justice cent` | **65** |
  | ...of those carrying a phone number | **4 of 87** |
  | counties whose only records are city PDs | 20 - left empty on purpose |
  | counties with no LE record at all | 2 (Cottonwood, Kanabec) |

  These counts were measured with `sherr?iff`, the default at the time. The
  default is now `sherr?if` (see Wisconsin below), which is a superset, so
  MN's numbers can only go up - they have NOT been re-measured and are left
  as recorded rather than adjusted on paper.

  The phone column does not fill from any public source and no wider filter
  changes that: the numbers are on 87 separate county websites. MN files
  several sheriffs as "<County> Law Enforcement Center", "<County> Jail" or
  "<County> Public Safety Center". Writing a CITY police department as a
  county's primary LE would be wrong, so those counties stay empty. `--gaps`
  reports each empty county as one of four things: filter missed it, records
  present but all nameless, source has nothing, or its tile was never fetched
  - which need different responses and must not be conflated.
- **Wisconsin, fetched live 2026-09-14** - the second state ever fetched, and
  the first test of whether any of this was MN-specific. It was not: 72 of 72
  counties for boundaries, ACS population and Wikidata county seats, first
  try. The LE step found three things MN could not have:

  | | |
  |---|---|
  | police features in the state box | 444 |
  | ...carrying a phone number | 60 |
  | counties with an agency (5-term filter) | 40 of 72 |
  | ...of those carrying a phone number | 4 of 72 |
  | ...carrying a website | 6 |
  | have records, none matched the filter | 25 |
  | records present but every one unnamed | 3 (Fond du Lac, Forest, Green Lake) |
  | no LE record at all | 7 |

  1. **OSM spells one "Clark County Sherrif"** - doubled r, single f. The old
     `sherr?iff` required the second f and read past it, so the county went
     empty with nothing to show anything had been missed. The default is now
     `sherr?if`, which reaches sheriff, sherriff, sherrif and sherif; nothing
     else in these names begins "sherif" (Sheridan, Sherwood, Shelby are all
     untouched).
  2. **A misspelling is invisible to `discover_terms` by construction.** It
     offers a phrase only when it reaches two or more counties, because one is
     that county's own name - and a typo reaches exactly one. `near_misses`
     covers that: it learns the vocabulary from the agencies that DID match in
     this state and flags an unmatched word within TWO edits of one. Two, not
     one: "sherrif" to "sheriff" is two substitutions.
  3. **Three counties had records where every record was nameless.** A blank
     name cannot match any filter, so counting them as "widening may fix
     these" promised a fix that does not exist. They are their own line now.

  Still open for WI, and a maintainer call rather than a code one: Price
  County files its agency as "Price County Safety Building". MN's
  `county public safety` term requires "public" and misses it. Same class of
  question as the jail one in README section 11.

  **Cold state vs warm state, measured.** MN with 4 of 9 tiles cached took
  364s and lost one tile. WI with nothing cached took 1042s, lost four tiles,
  waited out four separate rate-limits, and did NOT finish in one pass - two
  tiles were still short at the end. The re-run cost two sub-requests, because
  successful quarters are cached individually and a tile whose four quarters
  are all cached is served from them. Budget a cold state at roughly 3x a warm
  one and expect a second pass.
- Overpass mirrors: `overpass-api.de`, `overpass.kumi.systems` and
  `overpass.private.coffee` all answer; a whole-state box gets HTTP 504 under
  load but a 3x3 tile does not. `overpass.osm.jp` serves a certificate that is
  invalid for its own hostname and is deliberately not in the list.
  **Overpass reports its own server-side timeout as HTTP 200** with an empty
  `elements` list and a `remark` - never as an error status. Anything that
  caches an Overpass response has to check for that or it stores "there is
  nothing here" permanently.
  **`[timeout:90]` serves all nine MN tiles; `[timeout:30]` fails five of
  them** - measured 2026-09-14, same tiles, same mirrors. Lowering it to make
  failures arrive sooner turned successes into failures, and each failure then
  split into four more requests, which earned an HTTP 429. Do not lower the
  server timeout without evidence from a real run.
  **HTTP 429 is not a failure to retry differently** - it is a request to wait.
  Splitting a rate-limited tile answers "too many requests" with four more.
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
