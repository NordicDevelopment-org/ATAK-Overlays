# Troubleshooting

Organised by what you are looking at when it goes wrong. Build-time symptoms
and their fixes are also in `statepacks/README.md` §9 and §10, which stay the
reference while you are in the middle of a run; this is the one to open when
something is wrong on the tablet.

---

## Start here: ask the folder what is in it

```bash
cd ~/atak-build/statepacks/termux && ./atak-list.sh
```

It opens every overlay and reports the placemark count, whether the document
says where it came from, and anything fighting with anything else. It only
reads - removal lines are printed for you to run, never run for you.

```
  file                                        size  placemarks  when             src
  MN_Counties__Current_2026_09_14.kmz         1.2M          87  2026-09-14 03:33 yes
  parcels.kmz                                 7.4M       8,000  2026-09-13 18:49 -

  2 thing(s) worth looking at:
    [NO SOURCE] parcels.kmz
        no source, licence or date anywhere in the document
```

`--quick` lists without opening the files. `--json FILE` writes the findings.

---

## On the tablet

### A new pack does not appear

The force stop was skipped. `Settings > Apps > ATAK > Force stop`, then reopen.
ATAK caches the overlay list and will not re-read the folder on its own. The
installer tries to do this for you and usually cannot on a non-rooted phone -
it says so when it fails.

### A layer is drawn twice, or a county has two outlines

Two editions of the same pack are installed. Run `./atak-list.sh`; it names
them and prints the removal line.

The case that is easy to miss: a pack built before the `__` convention. `__`
separates a pack's identity from its version, so
`MN_Counties__Current_2026_09_14.kmz` supersedes an older `MN_Counties__*`. A
file with only ONE underscore carries no version, matches neither the retire
glob nor its own, and nothing will ever retire it. That is a real thing that
happened - `MN_Counties_Current_2026_09_14.kmz` drew every Minnesota county a
second time for a day. `atak-install.sh` now warns when it sees one, and the
inventory flags it `STALE`.

### A pack is listed but draws nothing

The inventory reports it as `EMPTY`: the file parses and contains no
placemarks. That is a build that failed and got installed anyway. Rebuild it;
if it builds empty again, the fetch it depends on returned nothing and the
build log will say which one.

### ATAK is sluggish after installing a pack

Placemark count is the thing to look at, and `./atak-list.sh` prints it.
`--precision 5` on the build gives roughly 1 m accuracy and about 40% smaller
files. Dense layers (parcels, building footprints, address points) are worth
keeping switched off until you need them.

### A placemark has no source on it

The inventory flags the file `NO SOURCE`. It means the document carries no
source, licence or retrieval date anywhere - a dot on a map you cannot check,
which rule 4 of this project exists to prevent. Packs built by
`build_county_pack.py` always carry provenance; a file flagged this way came
from somewhere else and needs rebuilding rather than patching.

---

## During a fetch

### It sits there for a long time

It prints a line per tile with time used and time left. The first fetch of a
state has nothing cached and is the slow one - budget roughly 3x a re-run.
Measured: Minnesota with four of nine tiles cached took 364s; Wisconsin with
nothing cached took 1042s and did not finish in one pass.

`--deadline 1800` gives it longer. Finished tiles are cached, so a re-run
resumes rather than starting over.

### `N of 9 tiles failed`

Not "no data". The tiles that worked are cached, so **re-run the same command**
- it only refetches the failures. Wisconsin's second pass cost two
sub-requests, because successful quarters are cached individually and a tile
whose four quarters are all cached is served from them.

### `every mirror is rate-limiting; waiting 20s`

HTTP 429 is a request to wait, not a failure to retry differently. The run
already waits. Splitting a rate-limited tile would answer "too many requests"
with four more requests, so it deliberately does not.

If it still fails after the wait, give it a few minutes, or `--jobs 1`.

### `... never reached a mirror because this run's own other tiles were holding them`

Self-contention, not the server. Lower `--jobs`.

### One tile keeps timing out

It is split into quarters automatically, because a smaller box is a cheaper
question than the same one again. If the quarters land, the parent is never
asked for again.

### A run that used to be instant refetches everything once

Tiles cached before they carried a fetch date are refetched once so the rows
built from them can be stamped with a date they actually have. It says so on
screen. The run after that is instant again.

### `fetched, but could not cache this tile`

The data is fine; `~/.cache` is not writable. Nothing is lost, but every run
will refetch. Check storage permissions.

---

## Sheriff and contact data

### A county has no sheriff and you know it has one

Run with `--gaps`. The report separates four things that need opposite
responses and must not be conflated:

| Report line | What it means |
|---|---|
| `have records, none matched the filter` | Widening `--match` may fix it. The unmatched names are printed |
| `one small slip from a name that matched` | Probably a source typo. Wisconsin spells one "Clark County Sherrif" |
| `records present but every one unnamed` | No filter can match a blank name. Widening will never help |
| `no law-enforcement record at all` | Not in the source. No filter fixes this |
| `NOT FETCHED - their tile failed` | Unknown, not absent. Re-run |

`--gaps-dump FILE` writes every unmatched county and all its agency names. The
printed report samples 20 to stay readable on a phone, and on a first run for a
state the names it does not print are the evidence that decides the filter.

### The phone number column is nearly empty

That is the ceiling, not a work in progress. Measured at 4 of 87 counties in
Minnesota and 4 of 72 in Wisconsin. The numbers are on individual county
websites, not in any public dataset. Fill them by hand in
`data/le_contacts.local.csv`, which the seeder never overwrites.

### A city police department was written as the county's agency

It should not have been, and a test holds that line. If you see one, it is a
bug - report the county and the name. The rule is that an agency is
county-level when it carries the county's own name, read from TIGER rather
than from a word list, so parishes and municipios work too.

---

## Scripts and permissions

| Symptom | Fix |
|---|---|
| `permission denied` running a script | `chmod +x termux/*.sh` |
| `ATAK overlays folder not found` | Open ATAK once so it creates it, or `export ATAK_DIR=/your/path` |
| `termux-setup-storage` does nothing | Grant by hand: `Settings > Apps > Termux > Permissions > Files and media` |
| `unknown option: --x` from remove | Deliberate. An unrecognised flag is never treated as a filename pattern |
| Two clones of this repo on one phone | Delete one. A run using the wrong clone looks like a code bug and is not |

---

## When an endpoint will not connect

`statepacks/README.md` §10 covers this in full: finding the right TIGERweb
layer with `tiger_diagnose.py`, checking ACS with a key, and using a different
source entirely. The short version:

```bash
python3 tiger_diagnose.py --state MN     # which layer, and does it answer
python3 acs_diagnose.py --state MN       # is the Census key working
python3 build_county_pack.py --probe     # is anything reachable at all
```

A keyless ACS request answers **HTTP 200 with an HTML page**, not an error
status and not JSON. The key is free and instant at
<https://api.census.gov/data/key_signup.html>.
