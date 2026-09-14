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
| 1.1 | Diagnostic: what does OSM actually carry for MN repeaters? | `NEXT` run it |
| 1.2 | Decide sources from 1.1's numbers | `OPEN` |
| 1.3 | `MN_Repeaters__<vintage>.kmz` - ham and GMRS | `OPEN` |
| 1.4 | NOAA Weather Radio transmitters, its own diagnostic first | `OPEN` |

**Settled.** No coverage circles. ERP and HAAT are absent from essentially
every candidate source, so a radius would be a number we chose presented as a
source fact, and a filled circle in ATAK reads as a guarantee.

**Settled.** Frequencies and tones are strings end to end. `023N` is a DCS code
whose leading zero and N suffix are load-bearing.

**Sources, researched:**

| Source | Verdict |
|---|---|
| OpenStreetMap | Usable. ODbL, fetch machinery already exists |
| OpenRepeater.org | States CC0 on its own pages. Coverage unknown, terms need reading live |
| RepeaterBook | Ruled out. Terms forbid bulk extraction, redistribution, offline bundling, or using it to build another dataset |
| ARRL Directory | Ruled out. Powered by RepeaterBook, and a paid product |
| FCC ULS `l_amat` / `l_gmrs` | Ruled out for locations. Licensee mailing address, not transmitter site |
| hearham.com | Disabled pending a written yes. Prose licence, coords self-described as approximate |

**GMRS.** The FCC licenses the operator, not the site, so GMRS repeater
locations are largely not in any public federal database. Whatever OSM has may
be all there is, and the pack has to say so rather than look thin for no
stated reason.

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
