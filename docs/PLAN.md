# Plan

The working list, kept current as things land. Newest state at the top of each
phase. `statepacks/README.md` §11 carries the handoff detail: what is untried,
which decisions are the maintainer's, and what looks like a bug but is not.

**Status key:** `DONE` verified live · `BUILT` written and tested, not yet run
against a real endpoint · `NEXT` the thing being worked on · `OPEN` not started

Last updated 2026-09-14.

---

## Phase 0 - the machinery everything else rides on

| | What | Status |
|---|---|---|
| 0.1 | County pack builder, one command per state | `DONE` MN, WI |
| 0.2 | Overpass fetch: tiling, 3 mirrors, 429 waits, split-on-timeout, per-tile cache | `DONE` measured over two states |
| 0.3 | Fetch machinery reusable by a second pack type (`query`, `prefix`, `parse`) | `BUILT` |
| 0.4 | ATAK folder inventory - what is installed, what is inside it, what is wrong | `BUILT` |
| 0.5 | Troubleshooting doc | `BUILT` see `docs/TROUBLESHOOTING.md` |

**0.3 note.** The tile cache was keyed `osm_police_<box>`. A second query over
the same boxes would have been served the first one's answer. The query
identity is now part of the key; the old default keeps every tile already on a
phone valid.

---

## Phase 1 - radio

The metadata that makes a repeater useful in the field: coordinates, the
frequency you listen on, the frequency you transmit on, the offset, and the
tone that opens it.

| | What | Status |
|---|---|---|
| 1.1 | Diagnostic: what does OSM actually carry for MN repeaters? | `DONE` and the answer is nothing |
| 1.2 | Decide sources from 1.1's numbers | `DONE` no redistributable source exists |
| 1.3 | `MN_Repeaters__<vintage>.kmz` from a public source | `BLOCKED` on permission, see below |
| 1.3b | Bring-your-own-data repeater layer | `OPEN` the only unblocked path |
| 1.4 | NOAA Weather Radio transmitters | `BUILT` 37 MN, coordinates found |

**1.4 UNBLOCKED and built 2026-09-14.** The coordinates exist after all, in
`https://www.weather.gov/source/nwr/JS/ccl-data.js` - the county-coverage file
that backs the station pages. It is JavaScript (`var cclData = [...];`), not
JSON, which is why a search for a structured download found nothing. It carries
callsign, frequency, power, status, lat/lon, site, WFO and the full SAME county
list for all 1,036 US transmitters, so it scales to 50 states by one filter.

`build_nwr_pack.py` builds it: 37 transmitters sited in MN, or 49 with
`--coverage` (adding border stations in ND/WI/IA/SD whose alerts reach MN).
Those two counts were derived independently here and match the supplied data
exactly. One folder per status, because an OUT OF SERVICE transmitter is
something a folder should say out loud - KXI45 Gunflint Lake is currently the
one, which independently reproduces the standing notice on weather.gov/nwr.

**The data confirmed the SAME-code rule empirically.** Four MN counties appear
as both a whole-county code and one or more partial codes - Hennepin is 027053
AND 127053 AND 327053. Partial County Alerting adds; it does not replace.

**1.4 NOAA Weather Radio, the licence.** Genuinely clean and settled:
<https://www.weather.gov/disclaimer> puts NWS web content in the public domain,
subject to three conditions an attributed KMZ satisfies. It is not a repeater
layer - NWR is one-way broadcast, so there is no input frequency, no offset and
no tone, and those columns stay empty. The 1050 Hz alert tone and SAME digital
headers are not CTCSS/DCS and must never be written into a tone column.

The blocker is coordinates. weather.gov publishes station tables as
server-rendered HTML only - callsign, frequency, site town, status, WFO, and
per-county SAME codes - with **no latitude or longitude and no power column**.
Endpoints read: `/nwr/station_listing`, `/nwr/stations?State=MN`,
`/nwr/county_coverage?State=MN`, `/nwr/sites?site=<CALL>`. Both state tables
carry their own "current on" timestamp, so a retrieval date rides in the page.

Two unverified leads for coordinates: an ArcGIS Online item
`f399e8e588c64cdc898ed70dd7782fba` ("National NOAA Weather Radio Sites", owned
by NWS.HUN_noaa), whose REST URL, fields and `licenseInfo` nobody has read -
"Sharing: Everyone" is a visibility setting, not a licence, and the weather.gov
public-domain sentence is scoped to weather.gov, not to an Esri-hosted service.
And `/nwr/station_search`, a find-stations-near-me page which cannot work
without server-side coordinates; capturing its request would settle it.

**SAME codes, corrected.** A county has ONE whole-county code (`0` + 5-digit
FIPS) AND zero or more partial-county codes (`1`-`9` + FIPS). Partial County
Alerting adds sub-area codes, it does not replace the whole-county one - the MN
table shows Aitkin as both `027001` and `227001`. Any note claiming a county's
code is the partial one *instead of* the whole-county one is wrong and would
drop every whole-county code in the state.

**1.1, measured live 2026-09-14 over 7 of 9 MN tiles.** One object in the whole
state: a `man_made=antenna` node with `communication:amateur_radio=yes`, no
frequency, no callsign, no name. **Zero objects carried a listen frequency.**
The query is confirmed working - `communication:amateur_radio` is one of its
anchors and it matched - so this measures OSM, not the code.

**1.1 settled with `--deep`, 2026-09-14.** A key regex matching ANY key
containing `amateur_radio`, `repeater` or `gmrs`, over the Twin Cities metro -
the densest ham population in the state - returned **0 objects** across 6
successful tiles. No spelling anyone failed to predict can hide from a key
regex, so OSM is closed as a repeater source. Not proof for every acre of
Minnesota, and the report says so; it is as strong as cheap evidence gets.

**1.2, the answer is no.** There is no source of MN amateur or GMRS repeater
data with output frequency, input/offset and tone that this project can
redistribute in a public KMZ on a licence anyone has read. Every candidate
fails on one of: an explicit prohibition, a login or per-person token wall, or
a permission that does not cover republication.

**Settled.** No coverage circles. ERP and HAAT are absent from essentially
every candidate source, so a radius would be a number we chose presented as a
source fact, and a filled circle in ATAK reads as a guarantee.

**Settled.** Frequencies and tones are strings end to end. `023N` is a DCS code
whose leading zero and N suffix are load-bearing.

**Sources, researched:**

| Source | Verdict |
|---|---|
| OpenStreetMap | Licence fine (ODbL), **data does not exist**. Measured: 1 object statewide, 0 with a frequency |
| hearham.com | **Closest to a yes, and still not one.** Real public JSON API, 493 MN records by its own count. The grant on /repeaters is "Free to use and free to use in your application" - a redistributable pack is not "your application". /terms is silent on redistribution in both directions, and no contributor-licence clause is documented, so its authority to sublicense crowd-sourced rows is unestablished. Needs an email |
| RepeaterBook | Ruled out without written permission, which they have a channel for. Their wiki names "offline bundling" - a KMZ pack, exactly - among the uses requiring it |
| RadioReference | Ruled out. Terms prohibit use of database tables "in any form, media or technology" without written consent. Full API is paid |
| myGMRS | Ruled out. Login-gated, and for closed repeaters the tone is emailed only after the owner grants a per-person request |
| openrepeater.org | **UNVERIFIED - I previously overstated this.** Two different things share the name: openrepeater**.com** is Raspberry Pi repeater *controller software* with no data at all, and openrepeater**.org** is a separate new directory. "Repeater data is licensed under CC0." appears only in what looks like site-wide footer chrome, not in /terms, and /register suggests a login wall. No MN evidence of any kind |
| repeatermap.de | Ruled out. API needs a per-person token requested by contact form. That is access control, not a licence |
| artscipub | Ruled out. Browse-only, and the bulk product is a printed book they sell |
| ARRL Directory | Ruled out. Powered by RepeaterBook, and a paid product |
| FCC ULS `l_amat` / `l_gmrs` | Ruled out for locations. Licensee mailing address, not transmitter site |

**GMRS is out, and not only on licensing.** The FCC licenses the operator, not
the site, so no federal register of GMRS repeater locations exists. More
importantly: on myGMRS the tone for a closed repeater is structurally withheld
until the individual owner grants a per-person request. A KMZ cannot carry that
consent, so shipping those tones would be wrong even if a licence allowed it.
Whether a GMRS layer should exist at all is a maintainer judgement, recorded in
README section 11.

**Settled about modelling, whatever the source turns out to be.** GMRS pairs are
fixed by 47 CFR 95 subpart E at output +5.000 MHz, so a GMRS input frequency is
computed from a rule and never fetched. hearham has no input field either, so
transmit is `frequency + offset` and must be labelled derived, never presented
as a source field.

---

## Phase 2 - emergency services and infrastructure

| | What | Status |
|---|---|---|
| 2.1 | Emergency services | `OPEN` |
| 2.2 | Jails and prisons | `OPEN` |
| 2.3 | Power plants | `OPEN` |

Note for 2.3: `psap` lost its only source when the NASA HIFLD re-host left DNS,
and the FCC Master PSAP Registry is tabular only. There is no polygon
replacement. See CLAUDE.md.

---

## Phase 3 - the files already on the device

Found by `atak-list.sh`. The 87 `MN_<County>_County_rev2.kmz` files, the `CI_*`
Chisago set, and `address_points` / `building_footprints` / `parcels` / `roads`
all carry no source, licence or retrieval date.

| | What | Status |
|---|---|---|
| 3.1 | Inventory that names every file with no provenance | `BUILT` |
| 3.2 | `city_boundaries_rev1.kmz` removed entirely - unusable | `NEXT` run the removal |
| 3.3 | `MN_Counties_Current_2026_09_14.kmz` removed - stale duplicate | `NEXT` run the removal |
| 3.4 | Give the keepers metadata | `OPEN` needs 3.1's output first |

**3.3 was a real bug, not clutter.** `__` separates a pack's identity from its
version. The single-underscore file carries no version, so it matched neither
the retire glob nor its own, nothing could ever retire it, and ATAK drew every
Minnesota county twice. `atak-install.sh` now warns when it sees one.

---

## Phase 4 - more states

| | What | Status |
|---|---|---|
| 4.1 | A second state, live | `DONE` WI, 2026-09-14 |
| 4.2 | Alaska - the antimeridian path has never seen real polygons | `OPEN` deferred |
| 4.3 | Bound `--all-states` | `OPEN` |
| 4.4 | A state much bigger than MN (TX, CA) | `OPEN` |

**Measured, and it sets the budget for 4.3.** A cold state costs roughly 3x a
warm one and may not finish in one pass. MN with four of nine tiles cached took
364s. WI with nothing cached took 1042s, lost four tiles to Overpass 504s and
read timeouts, waited out four separate rate-limits, and needed a second run.
That second run cost two sub-requests, because successful quarters are cached
individually.

---

## Carried over from the other half of the repo

`src/overlaybuilder/` is a separate pipeline from `statepacks/` and shares no
code with it. These were confirmed present by a full audit and are unfixed.
None of them affect the statepacks path.

| | What | Status |
|---|---|---|
| A | `<Data name="name">` overwrites the KML `<name>` via LIBKML reserved fields - every unit-labelled map label is lost on device | `OPEN` |
| B | `reconcile.md` stamps "agree" when the pair shares none of the five `COMPARE_KEYS` | `OPEN` |
| C | `146.94 MHz` normalizes to `frequency_hz: 146.94`, off by 10^6 | `OPEN` |
| D | `validate` never lints `layer:`, `icon:` or `alternates:` | `OPEN` |

C is the same mistake Phase 1 is deliberately avoiding: statepacks keeps
frequencies as strings so it cannot happen there.
