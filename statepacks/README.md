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
| **Termux** | the shell this all runs in | [F-Droid](https://f-droid.org/packages/com.termux/) — **not** the Play Store version, it is stale |
| **python** (3.8+) | runs the builders | `atak-setup.sh` |
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

```bash
cd ~/atak-packs/ATAK-Overlays/statepacks
python3 build_county_pack.py --probe
python3 build_county_pack.py --state MN --out ~/atak-packs/out
```

Swap `MN` for any state. `--probe` first is worth the ten seconds: it checks
the Census endpoints are answering before you spend a download on them.

**Optional — fill in county seats** (they ship empty on purpose, see §7):

```bash
python3 fetch_county_seats.py --state MN
python3 build_county_pack.py --state MN --out ~/atak-packs/out
```

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

```
County:               Chisago County, MN   [TIGER 2024]
FIPS (GEOID):         27025                [TIGER 2024]
County seat:          Center City          [Wikidata (community-maintained) 2026-09-14]
Population:           58,241               [ACS 5-year 2023]
Housing units:        23,110               [ACS 5-year 2023]
Land area:            413.9 sq mi          [TIGER ALAND 2024]
Water area:           28.5 sq mi           [TIGER AWATER 2024]
Sheriff / primary LE: not in dataset
LE non-emergency:     not in dataset
```

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
| Boundary, FIPS, land/water area | **US Census TIGERweb** | public domain; national — same call for all 52 |
| Population, housing units | **US Census ACS 5-year API** | exact figures, explicit vintage; keyless at low volume |
| County seat | **Wikidata** via `fetch_county_seats.py` | community-maintained, not a government register — labelled as such in the popup |
| Sheriff / LE + non-emergency | **`data/le_contacts.csv`** — ships empty | see below |

### Why sheriff contacts ship empty

There is no clean national machine-readable source for sheriff office names and
non-emergency numbers. FBI UCR tables have agency names but no phone numbers;
state directories vary in format and licence.

**A wrong non-emergency number is worse than a missing one — it fails at the
moment someone actually dials it.** So the file starts empty. Add rows you have
verified yourself:

```bash
nano data/le_contacts.csv
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

---

## 10. Roadmap — more pack types

This is the first of several. The layout is meant to be recycled: a new pack
type is a new builder next to `build_county_pack.py` that emits
`<STATE>_<Thing>_<vintage>.kmz` into the same output folder, and the same
`atak-install.sh` / `atak-remove.sh` handle it with no changes.

Planned next: municipal boundaries, PSAP/dispatch zones, public-safety
infrastructure. The critical-infrastructure sector packs (power, water, comms,
emergency) already exist in the parent repo — see the top-level `README.md`.
