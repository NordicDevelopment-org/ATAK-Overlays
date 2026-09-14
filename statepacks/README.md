# State Packs — county boundary KMZ packages for ATAK

One KMZ per US state. Every county in that state as an outline, with a popup
carrying population, housing units, land area, county seat and sheriff contact
— and **the year each of those numbers is from**, so a pack you find on a
device in two years can be judged on its age without opening anything else.

Runs on your phone in Termux. **Standard library only — no pip packages.**

---

## 1. Dependencies

Everything you need, and nothing else:

| What | Why | Installed by |
|---|---|---|
| **Census API key** | population + housing. **Required** — a keyless request returns an HTML page, not data | you, free + instant: [api.census.gov/data/key_signup.html](https://api.census.gov/data/key_signup.html) |
| **Termux** | the shell this all runs in | [F-Droid](https://f-droid.org/packages/com.termux/) — **not** the Play Store version, it is stale |
| **python** (3.10+) | runs the builders — 3.10 to 3.13 are what CI actually tests | `atak-setup.sh` |
| **git** | clone / update this repo | `atak-setup.sh` |
| **curl** | download prebuilt packs | `atak-setup.sh` |
| **Storage permission** | lets Termux write to the ATAK folder | `atak-setup.sh` (Android will prompt — **Allow**) |
| **ATAK-CIV**, opened once | creates `/storage/emulated/0/atak/overlays` | you |

No `pip install`. No `pyproj`, no `gdal`, no build toolchain. That is
deliberate — those need a C compiler and will eat your evening on Android.

---

## 2. First-time setup — paste this whole block

```bash
pkg update -y && pkg upgrade -y
pkg install -y python git curl
termux-setup-storage
mkdir -p ~/atak-packs && cd ~/atak-packs
git clone https://github.com/NordicDevelopment-org/ATAK-Overlays.git
cd ATAK-Overlays/statepacks
chmod +x termux/*.sh
./termux/atak-setup.sh
```

Android will pop a storage permission prompt. **Allow it.** If you miss it:
`Settings → Apps → Termux → Permissions → Files and media → Allow all`.

Open ATAK once before continuing, so it creates its folders.

---

## 3. Build your state — paste this whole block

**One command does all of it:**

```bash
cd ~/atak-packs/ATAK-Overlays/statepacks/termux
chmod +x *.sh
./make-state-pack.sh MN --install
```

That runs the four steps in the order their data depends on — county seats
(Wikidata), sheriff / primary LE (OpenStreetMap), the pack itself (TIGERweb +
ACS), then the install and the force stop. Swap `MN` for any state.

| flag | |
|---|---|
| `--install` | copy into ATAK and force-stop it afterwards |
| `--gaps` | also print the per-county gap report (§7) |
| `--skip-le` | skip the OpenStreetMap step |
| `--skip-seats` | skip the Wikidata step |
| `MATCH='...'` | override the LE name filter for this run |

**Every step is resumable.** Boundaries, OSM tiles and both CSVs are cached or
written locally, so re-running after a failure picks up where it stopped. A
step that fails stops the run — a pack built on half-fetched data would look
complete.

It only installs **that state's** pack, so a staging folder with ten states in
it does not get reinstalled every time.

### Or run the steps yourself

```bash
cd ~/atak-packs/ATAK-Overlays/statepacks
export CENSUS_API_KEY=your_key_here
python3 build_county_pack.py --probe
python3 build_county_pack.py --state MN --out ~/atak-packs/out
```

Make the key stick across Termux sessions:

```bash
echo 'export CENSUS_API_KEY=your_key_here' >> ~/.bashrc
```

Or save it once, outside the repo so it can never be committed:

```bash
mkdir -p ~/.config/atak-statepacks
echo your_key_here > ~/.config/atak-statepacks/census_key
```

**Without a key the pack still builds** — boundaries, FIPS and land/water area
all come from TIGERweb, which needs no key. Only population and housing read
`not in dataset`.

Swap `MN` for any state. `--probe` first is worth the ten seconds: it checks
the Census endpoints are answering before you spend a download on them.

**Optional — fill in the empty fields** (they ship empty on purpose, see §7).
Run these once per state, then rebuild:

```bash
python3 fetch_county_seats.py --state MN     # county seats, from Wikidata
python3 seed_le_contacts.py --state MN       # sheriff / primary LE, from OpenStreetMap
python3 build_county_pack.py --state MN --out ~/atak-packs/out
```

(HIFLD Open shut down in August 2025 and its NASA re-host is gone from DNS, so
the LE step reads OpenStreetMap. See §7 for exactly how much of it is there.)

Both write their own source and year into every row, so the popup shows where
each value came from. Neither overwrites a row you edited by hand.

They write to `data/<name>.local.csv`, **not** over the shipped template. The
template is tracked in git; the `.local.csv` is gitignored. So a `git pull` can
never conflict with data you fetched, and a pull can never wipe it. Where both
have a row for the same county, yours wins.

**Other builds:**

```bash
python3 build_county_pack.py --all-states --out ~/atak-packs/out   # all 52
python3 build_county_pack.py --state MN --per-county               # + 1 file per county
python3 build_county_pack.py --state MN --precision 5              # ~40% smaller
python3 build_county_pack.py --state MN --acs-year 2022            # older vintage
```

---

## 4. Install into ATAK — paste this whole block

```bash
cd ~/atak-packs/ATAK-Overlays/statepacks
./termux/atak-install.sh ~/atak-packs/out
```

That installs everything in the folder. To install one file, name it — the
builder prints the exact filename it wrote:

```bash
./termux/atak-install.sh ~/atak-packs/out/MN_Counties_2024.kmz
```

The `2024` in that name is **the boundary vintage the Census service reported**,
not a fixed string — so the filename tells you how old the boundaries are. If
the service reports no year, the pack is named `..._built<date>.kmz` instead and
the popup reads `vintage not reported` rather than claiming a year.

Copies go in through a temp file and an atomic rename, so a copy interrupted by
a full card leaves your existing pack intact rather than a truncated one.

Then: **open ATAK → Overlay Manager (stacked-layers button) → your pack is
listed by filename → tap the eye to toggle it.**

### Rebuilds retire the previous edition

A pack filename is `<identity>__<version>.kmz` — `MN_Counties__Current_2026-09-16.kmz`.
Everything left of the `__` says what the pack **is**; everything right of it says
which **edition**. Installing a new edition removes the older ones, so a rebuild
does not leave last week's copy drawing county lines underneath this week's, or
listing the same pack twice in Overlay Manager.

A filename with no `__` carries no version, so nothing is ever retired for it —
that is what stops `MN_Water.kmz` being read as a newer `MN_Energy-Electric.kmz`.

```bash
./termux/atak-install.sh --keep-old ~/atak-packs/out   # keep every edition
```

### The force stop is the whole trick

ATAK caches its overlay list in memory. Copy a file into the folder while ATAK
is running and **nothing appears** — that is the thing that makes people think
the build failed. `atak-install.sh` force-stops ATAK for you after copying, so
the next launch re-reads the folder.

If it can't (some Android builds refuse), do it by hand — this step is
**required**, not optional:

```
Settings → Apps → ATAK → Force stop
```

Find your package name if ATAK isn't the stock CIV build:

```bash
pm list packages | grep -i atak
export ATAK_PKG=com.your.atak.package
```

---

## 5. See what's installed

```bash
./termux/atak-list.sh
```

Newest first, with sizes and install dates. Run this before removing anything.

---

## 6. Remove files — paste what you need

```bash
./termux/atak-remove.sh MN_Counties_2024.kmz     # one file
./termux/atak-remove.sh 'MN_*'                   # a pattern — QUOTE IT
./termux/atak-remove.sh --dry-run 'MN_*'         # show, delete nothing
./termux/atak-remove.sh --all                    # everything (asks twice)
```

It lists exactly what it will delete and waits for you to type `yes`. `--all`
asks a second time. It force-stops ATAK afterwards for the same reason install
does — a deleted file can linger in the UI until the app restarts.

---

## 7. Data integrity — read this once

**Every value in a popup is followed by its source and year:**

| | |
|---|---|
| County: | **Chisago County, MN** <sub>`[TIGER Current]`</sub> |
| FIPS (GEOID): | **27025** <sub>`[TIGER Current]`</sub> |
| County seat: | **Center City** <sub>`[Wikidata (community-maintained) 2026-09-14]`</sub> |
| Population: | **58,241** <sub>`[ACS 5-year 2023]`</sub> |
| Housing units: | **23,110** <sub>`[ACS 5-year 2023]`</sub> |
| Land area: | **413.9 sq mi** <sub>`[TIGER ALAND Current]`</sub> |
| Water area: | **28.5 sq mi** <sub>`[TIGER AWATER Current]`</sub> |
| | *No data for: Sheriff / primary LE, LE non-emergency* |

The **value is bold** — that is what someone opened the popup to read. The
source and year are **grey and bracketed**: present for judgement, never
competing with the number. `<font color>` rather than a CSS span, because
ATAK's description renderer is not a full browser and the old tag is the one
constrained renderers reliably honour.

**A field nothing returned gets no row.** Nine lines of "not in dataset" bury
the six that carry real values, so an absent field is simply left out and the
ones with nothing are named once, compactly, at the end. An omitted row asserts
nothing — which is all the never-invent rule actually requires — and the
Document description still lists every source consulted, so a blank stays
explainable rather than looking like a value of zero.

The `2024` is read from the Census service at build time, not hardcoded. The
county is named exactly as its own source spells it — `Acadia Parish`,
`Nome Census Area`, `Juneau City and Borough`, `Adjuntas Municipio` — because
15 states do not call their county-equivalents counties.

**`not in dataset` means no source returned a value. It does not mean zero.**
Nothing here is estimated, rounded from memory, or filled in to look complete.
A plausible-looking invented number on a map someone may act on is a liability,
not a convenience.

### Where each field comes from

| Field | Source | Notes |
|---|---|---|
| Boundary, FIPS, land/water area | **US Census TIGERweb** | public domain; national — same call for all 52. **Verified live 2026-09-14**: layer 1, 87 MN counties, renders correctly in ATAK-CIV |
| Population, housing units | **US Census ACS 5-year API** | exact figures, explicit vintage. **Key required** - verified live 2026-09-14: 87 MN counties |
| County seat | **Wikidata** via `fetch_county_seats.py` | community-maintained, not a government register — labelled as such in the popup. **Verified live 2026-09-14**: 87/87 MN counties |
| Sheriff / LE + non-emergency | **OpenStreetMap** via `seed_le_contacts.py` | the only reachable source carrying phone numbers. Community-maintained: coverage varies, a number is as current as the last edit. Stamped with the fetch date. |
| Sheriff / LE (names only) | **USGS National Map Structures** (`--source usgs`) | live and maintained, but **no phone field exists** in that dataset |
| ~~HIFLD LE Locations~~ | ~~NASA NCCS re-host~~ | **host gone from DNS 2026-09-14** — see CLAUDE.md |

### Why sheriff contacts ship empty, and how to fill them

**A wrong non-emergency number is worse than a missing one — it fails at the
moment someone actually dials it.** So nothing is shipped unsourced.

`seed_le_contacts.py` fills them from **HIFLD Local Law Enforcement Locations**
(derived from DOJ BJS), the one public dataset that carries agency name, address
*and* telephone joined to a county FIPS code. It keeps the sheriff's office per
county, preferring a record that actually has a number:

```bash
python3 seed_le_contacts.py --probe                   # which sources answer
python3 seed_le_contacts.py --state MN --show 5       # see the real records first
python3 seed_le_contacts.py --state MN --dry-run      # preview, write nothing
python3 seed_le_contacts.py --state MN                # OSM by default
python3 seed_le_contacts.py --state MN --source usgs  # names only, no phones
python3 seed_le_contacts.py --state MN --all-agencies # every agency, not just sheriffs
```

**Look before you filter.** `--show N` prints the raw records, counts how many
names the current `--match` would hit, and lists the distinct values of the
classifying columns. A run that writes zero rows is usually a filter that does
not match how that dataset spells its names — not an absence of sheriffs.

**`--gaps` says why each county came back empty.** A count like "52 of 87" is
not a diagnosis. A county with no row is either one whose agencies are all
named something the filter missed — fixable by widening `--match` — or one with
no law-enforcement record in the source at all, which no filter fixes. Those
need opposite responses, so the report separates them and prints what the
unmatched records are actually called:

```bash
python3 seed_le_contacts.py --state MN --gaps
```

```
GAP REPORT for MN - 87 counties
  filter: /sheriff/i
  matched and written                   : 52
    ...of those carrying a phone number : 3
    ...of those carrying a website      : 5
  have records, none matched the filter : 33  <- widening --match may fix these
      27007 Beltrami County              Beltrami County Law Enforcement Center, Redlake Police Department
      27005 Becker County                Becker County Jail, Detroit Lakes Police Department
  no law-enforcement record at all      : 2   <- not in the source; no filter fixes this
```

Minnesota's own spelling, from that report: several counties file the sheriff
under **"<County> Law Enforcement Center"** or **"<County> Jail"**, not
"Sheriff". Widening the filter picks those up, and the agency is written
**exactly as the source spells it** — nothing is relabelled into "X County
Sheriff" because it looked like one:

```bash
python3 seed_le_contacts.py --state MN --gaps \
    --match 'sheriff|law enforcement cent|county jail'
```

When a county matches on more than one record, the one whose name actually
says "sheriff" wins, and only then does a phone number break the tie — a jail
must not outrank the sheriff's office just by being returned first, and a phone
number attached to the wrong agency is worse than no phone number on the right
one.

**The report works out the next widening for you.** It scans the names that
actually came back unmatched and prints the terms that would reach a real
county *in this state's data*, with a count each and a runnable command:

```
    these terms would reach 2 of those 23 counties:
      +1   county public safety
      +1   justice cent
    python3 seed_le_contacts.py --state MN --gaps \
        --match 'sherr?iff|county public safety|justice cent'
```

It can only ever suggest a term that reaches a county here, because it is
computed from the names rather than from a list of what such places are
usually called.

The default filter is `sherr?iff`, and the doubled `r` is deliberate: OSM
carries "Steele County **Sherriff**'s Office and Detention Center". That is the
same word misspelled by whoever typed it, not a different agency — and the name
is still written out exactly as the source has it.

The rest of the unmatched counties have only **city** police departments.
Writing one of those as a county's primary LE would be wrong, so they stay
empty.

### Minnesota, measured

| filter | counties with an agency |
|---|---|
| `sheriff` | 52 of 87 |
| `sherr?iff` (default — catches the typo) | 53 |
| `+ law enforcement cent \| county jail` | 63 |
| `+ county public safety \| justice cent` | 65 |
| counties with only city PDs, left empty | 20 |
| counties with no LE record at all | 2 (Cottonwood, Kanabec) |

Phone numbers, at every one of those filters: **3**.

### What OSM actually has for Minnesota, measured 2026-09-14

| | |
|---|---|
| police features in the state box | 517 |
| ...carrying a phone number | **32** |
| sheriff offices matched to a county | 52 of 87 |
| ...carrying a phone number | **3 of 87** |

So the phone column **does not fill from any public source**. It is not a bug
and no amount of widening fixes it: the numbers are on 87 separate county
websites and nowhere machine-readable. The popup omits the field entirely for
a county that has none rather than showing a blank or a guess, and
`data/le_contacts.local.csv` is there for the ones you verify by hand — the
seeder never overwrites a row you edited.

### How the OSM fetch is made fast

OSM is read from the public Overpass mirrors, which rate-limit hard and time
out under load. The state is fetched as a **3x3 grid of tiles**, because a
whole-state box gets a 504. On top of that:

| | |
|---|---|
| **Tiles run concurrently** | Three at a time, one in-flight request per mirror. Nine tiles one after another is nine round trips of waiting; three at a time is three. |
| **Every tile is cached on disk** | `~/.cache/atak-statepacks/`. A re-run only fetches what is actually missing. |
| **A 429 is waited out, never split** | Rate limiting is the one failure where waiting is the remedy. Splitting would turn one refused request into four against a server that just said "too many", and a mirror that says 429 is left alone — for the whole run, not just that tile — until its `Retry-After` has passed. |
| **A stuck tile is split, not repeated** | A tile that times out on two mirrors is retried as four quarters. Asking a busy mirror the same large question again is what turned one slow tile into a stalled run. |
| **A tile already served as quarters is not re-requested** | Otherwise every run pays the timeout for the one tile the mirrors would not serve. |
| **Two boxes when a state crosses the date line** | Alaska's Aleutians sit near +172 and the mainland near -130; min/max longitude over both is a 302-degree box — most of the northern hemisphere in one query. Detected from the coordinates, never from a list of states. |
| **County boundaries are cached 30 days** | 87 polygons was the largest download the seeder made, and it was being made twice per run. |
| **A dated, expiring tile cache** | Each entry records when it was fetched, and the CSV row is stamped with *that* date — not the date the file happened to be written. Entries expire after 30 days. |
| **One wall-clock budget** | 600s **per state** (`--all` gets that for each state, not in total). The response body is read in chunks with the budget checked between them, so a mirror that trickles bytes cannot outlive it either. |

Measured against a replay of a real run — eight healthy tiles and one that
times out on every mirror:

```
before                 39.1 min
now, cold cache         1.2 min
now, re-run             0 requests, instant
```

```bash
python3 seed_le_contacts.py --state MN                    # defaults
python3 seed_le_contacts.py --state MN --deadline 1200    # 20 minutes to play with
python3 seed_le_contacts.py --state MN --osm-timeout 120  # let each tile work longer
python3 seed_le_contacts.py --state MN --jobs 1           # one request at a time
python3 seed_le_contacts.py --state MN --no-split         # never split a failed tile
python3 seed_le_contacts.py --state MN --refresh-shapes   # redownload the boundaries
```

Each tile prints its result with time used and time left, so you can see
progress instead of a blank prompt. When the budget runs out or a tile cannot
be served, the run **fails and names the counties that would come back empty**
— it does not hand back a partial set that would read as "these counties have
no sheriff". `--allow-partial` accepts one knowingly, and `--gaps` then files
those counties under **`NOT FETCHED — their tile failed`**, separately from the
ones the source genuinely has nothing for.

**The server timeout is 90s because 90s is what works.** Measured on a real
Minnesota run: all nine tiles are served at `[timeout:90]`. At 30s — picked to
make a failure arrive sooner — five of the nine time out instead, and each of
those five then fans out into four quarter-requests, which is how a run that
had been fetching 517 features earned an HTTP 429. A timeout that turns
successes into failures is not a faster failure; it is a slower one with extra
steps. Lower it only against evidence from a real run.

**Overpass answers its own timeout with HTTP 200.** Not a 504 — a normal JSON
body with an empty `elements` list and a `remark` reading `runtime error: Query
timed out…`. Taken at face value that caches "no police stations here" forever,
so a remark naming an error is treated as an error, a remark naming a timeout
counts towards splitting the tile, and neither is ever written to the cache.

### Finding a state's own GIS server

No national dataset is going to be as good as the state's own. `--discover`
lists what an ArcGIS server actually publishes, so you never have to guess a
service name:

```bash
python3 seed_le_contacts.py --discover https://feat.gisdata.mn.gov/arcgis/rest/services
python3 seed_le_contacts.py --discover https://<your-state-server>/arcgis/rest/services --pattern ''
```

It walks the folders the server advertises and prints every MapServer and
FeatureServer with its layers, marking ones whose name matches `--pattern`
(default `law|police|sheriff|emergency`). Pass `--pattern ''` to list
everything. Point `--endpoint` at whatever you find.

**Read this before dialling anything it writes.** OpenStreetMap is edited by
volunteers: a number there is as current as whoever last touched it, and
nobody is checking. Agencies consolidate, dispatch moves to a regional PSAP,
numbers get reassigned. That is exactly why every row carries the date its
tile was fetched — not the date the CSV was written:

```
LE non-emergency: 651-257-4100  [OpenStreetMap (Overpass) 2026-09-14]
```

Treat those as a starting point to verify, not as verified. When you confirm
one, edit the row with your own source and the current year — the seeder will
not overwrite it on a later run (only `--overwrite` does):

```bash
nano data/le_contacts.local.csv
```

```csv
geoid,agency,phone,source,vintage
27025,Chisago County Sheriff's Office,651-257-4100,county website,2026
```

Put the year you verified it in `vintage` — it shows in the popup, so the next
person can see how stale it is. Same schema for `data/county_seats.csv` if you
would rather hand-enter seats than trust Wikidata.

---

## 8. What this fixes vs. a hand-rolled per-state script

If you came here from a script that hit one state's GIS server directly:

| Problem | What happens | Fixed by |
|---|---|---|
| `coordinates[0]` only | drops holes, and **every polygon after the first** — a county with an exclave or islands renders as a lie about its own shape | `rings_of()` walks every ring into a `<MultiGeometry>` |
| Hardcoded population/housing dicts | unsourced, undated, unverifiable | live Census ACS API with an explicit vintage |
| Area from `Shape__Area` | that is in the service's **projection** — in Web Mercator at 45°N it is off by roughly **2×** | TIGER `ALAND`, real square metres of land |
| One state's GIS server | a new schema for every state you add | TIGERweb — one schema, all 52 |
| No provenance in the KML | no way to tell where a pack came from or how old it is | source + vintage on every value, endpoint recorded in every placemark |
| Primary endpoint dies | build fails | falls back through alternates, and **records which one actually answered** |
| `" County"` appended to every name | `Acadia Parish County`, `District of Columbia County` | uses the source's own full name |
| Boundary year hardcoded | the pack asserts a vintage nothing returned | read from the service; `vintage not reported` when it says nothing |
| Paging stops at the requested page size | a service capped below 1000 silently truncates the state | pages on the server's `exceededTransferLimit`, with a loop guard |

---

### Fifteen states do not call them counties

Louisiana has parishes, Puerto Rico municipios, Alaska boroughs and census
areas. The pack reads the descriptor back out of TIGER's own `NAME` instead of
pluralising a word from a table, so the overlay is titled with whatever the
source actually says:

```
MN County boundaries and reference data (Current)
LA Parish boundaries and reference data (Current)
PR Municipio boundaries and reference data (Current)
DC boundaries and reference data (Current)
AK County boundaries and reference data (Current)   <- AK genuinely mixes them
```

The **filename** stays `<ST>_Counties__<edition>.kmz` for every state on
purpose: it is the identity the installer matches on when it retires an older
edition, so it has to be predictable rather than descriptive.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| New pack doesn't appear in ATAK | You skipped the force stop. `Settings → Apps → ATAK → Force stop`, reopen. |
| `ATAK overlays folder not found` | Open ATAK once so it creates it. Or `export ATAK_DIR=/your/path`. |
| `termux-setup-storage` does nothing | Grant by hand: `Settings → Apps → Termux → Permissions → Files and media` |
| `--probe` says DEAD | Census endpoint moved or you're offline. Try `--endpoint <url>`; the alternates are tried automatically during a real build. |
| Population all `not in dataset` | ACS didn't answer. Build still works. Re-run later, or `--acs-year 2022`. |
| Pack is huge / ATAK is sluggish | `--precision 5` (~1 m accuracy, roughly 40% smaller). |
| `permission denied` running a script | `chmod +x termux/*.sh` |
| `unknown option: --x` from remove | deliberate — an unrecognised flag is never treated as a filename pattern |
| Seats/contacts still say `not in dataset` after editing a CSV | check the row has a 5-digit `geoid` in the first column and that you kept the `geoid,...` header line |
| The LE fetch sits there for ages | It prints a line per tile with time used/left. It stops on its own at 600s; `--deadline 1800` gives it longer. Finished tiles are cached, so a re-run resumes where it stopped. |
| One tile keeps timing out | It is split into quarters automatically. If the quarters land, the parent is never asked for again. |
| Boundaries look out of date in the LE match | `--refresh-shapes`. The cache is only used to decide which county a station falls in; overlay boundaries are always fetched fresh by the builder. |
| `N of 9 tiles failed (... ran out of the 480s budget)` | The budget ran out, not "no data". Re-run — cached tiles are skipped — or raise `--deadline`. |
| `... never reached a mirror because this run's own other tiles were holding them` | Self-contention, not rate limiting. Lower `--jobs`. |
| `N were RATE-LIMITED (HTTP 429)` | The mirrors are asking for a pause, not refusing the query. The run already waits and retries; if it still fails, wait a few minutes and re-run — what succeeded is cached — or use `--jobs 1`. |
| A run that used to be instant refetches everything once | Tiles cached before they carried a fetch date are refetched once, so the rows built from them can be stamped with a date they actually have. It says so on screen, and the run after that is instant again. |
| `fetched, but could not cache this tile` | The data is fine; `~/.cache` is not writable. Nothing is lost, but every run will refetch. |
| The county build sits on one request | Each url gets 150s total, retries included, and every retry names the host. `--http-budget 600` on a slow link; `--http-budget 30` to fail fast. |

---

## 10. When an endpoint will not connect

### First: find the right layer

The TIGERweb State_County service holds **several vintages side by side** —
Current, ACS 2025, Census 2020, BAS 2026 — each with its own States and
Counties layer. A flat listing shows "Counties" a dozen times and tells you
nothing about which one you are querying. Run this before anything else:

```bash
python3 tiger_diagnose.py --state MN
```

It prints the layer tree (which group each layer belongs to), then runs a real
county query against every Counties-looking layer and reports the row count,
the field names, and whether the fields the builder needs are present. It ends
with the exact `--endpoint` to use.

> The output below is an **example of what you will see** — do not paste it
> into the shell.

<pre>
LAYER TREE
    0        States                      under: -
    1        Counties                    under: -
   17  GROUP  BAS 2026                   under: -
   19        Counties                    under: BAS 2026

TESTING 12 county layer(s) with a real query for MN (expect 87 counties)

  layer   1  (under (top level))  87 rows  OK
       fields: OID, GEOID, STATE, COUNTY, BASENAME, NAME, AREALAND, AREAWATER ...
       builder needs: have ['GEOID', 'NAME', 'BASENAME', 'AREALAND', ...]
       sample: {'GEOID': '27025', 'NAME': 'Chisago County', 'AREALAND': 1072...}

==============================================================
USE THIS:  --endpoint https://.../State_County/MapServer/1
           (87 MN counties, group: (top level))
</pre>

### Then check the rest

```bash
python3 build_county_pack.py --probe
```

| What you see | What to do |
|---|---|
| `tiger_diagnose.py` names a layer | use its `--endpoint` line verbatim |
| Row count is wrong for your state | that layer is a different vintage or geography — try the next one it lists |
| All three boundary URLs DEAD, everything else OK | the service moved. Browse `https://tigerweb.geo.census.gov/arcgis/rest/services?f=pjson` and find the current county service. |
| Everything DEAD | you are offline, or on a network that blocks Census. Try mobile data. |
| Boundaries OK, ACS DEAD | build anyway — population/housing render `not in dataset` and you can rerun later |
| `NO KEY` / `MISSING KEY` | the Census API needs a key: [get one free](https://api.census.gov/data/key_signup.html), then `export CENSUS_API_KEY=...` |
| `INVALID KEY` | the key was sent but rejected — check for a stray space or newline |
| `CONNECT tunnel failed, 403` | a proxy is blocking it, not the server |

Inspect any service by hand — this is just a URL:

```bash
curl -s 'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer?f=json' \
  | python3 -c 'import json,sys; [print(l["id"], l["name"]) for l in json.load(sys.stdin)["layers"]]'
```

There is nothing to sign up for and no API key. TIGERweb and the ACS API are
open public endpoints — if they resolve from your phone, you are connected.
(The ACS API accepts a free key for high volume; one state at a time is well
under the keyless limit.)

### Using a different source entirely

Any ArcGIS FeatureServer/MapServer layer with county polygons works, including
your state's own GIS server:

```bash
python3 build_county_pack.py --state MN \
  --endpoint https://feat.gisdata.mn.gov/arcgis/rest/services/MnGeo/mn_counties/FeatureServer/0
```

The builder keeps every ring, reads `ALAND`/`AREALAND` if the layer has it, and
drops any feature whose FIPS does not belong to the state you asked for — so a
server that ignores the filter cannot slip other states into your pack.

---

## 11. Roadmap — more pack types

This is the first of several. The layout is meant to be recycled: a new pack
type is a new builder next to `build_county_pack.py` that emits
`<STATE>_<Thing>_<vintage>.kmz` into the same output folder, and the same
`atak-install.sh` / `atak-remove.sh` handle it with no changes.

Planned next: municipal boundaries, PSAP/dispatch zones, public-safety
infrastructure. The critical-infrastructure sector packs (power, water, comms,
emergency) already exist in the parent repo — see the top-level `README.md`.

### Where this is now

Verified end to end on an Android device, 2026-09-14, for Minnesota:

| | |
|---|---|
| `./make-state-pack.sh MN --install` | runs clean, pack renders in ATAK-CIV |
| Boundary, FIPS, land/water area | 87 of 87 |
| Population, housing (ACS, needs a key) | 87 of 87 |
| County seat (Wikidata) | 87 of 87 |
| Sheriff / primary LE (OSM) | 65 of 87 |
| LE non-emergency phone | 4 of 87 — this is the ceiling, see §7 |

Untried, and the most likely places for the next surprise:

- **Any state other than MN.** Nothing is MN-specific by design, and LA / PR /
  DC / AK packs build correctly offline, but no other state has been fetched
  live. Alaska is the interesting one: it is the only state that straddles the
  antimeridian, and the two-bounding-box path has never met real data.
- **`--all-states`.** Each state gets its own `--deadline`, so 52 states is 52
  budgets, and the Overpass mirrors will rate-limit long before the end. Run
  states in small batches until that is measured.
- **A state much bigger than MN** (TX, CA). The tile grid is a fixed 3x3, so
  those tiles are far larger; they will lean on the split-on-timeout path much
  harder than Minnesota does.
- **`--per-county`** builds one KMZ per county. It works, but 87 files has not
  been installed into ATAK in anger.
