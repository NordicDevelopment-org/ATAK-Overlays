#!/data/data/com.termux/files/usr/bin/bash
# make-state-pack.sh - one command for a whole state's county pack.
#
#   ./make-state-pack.sh MN                # fetch everything, build into ~/atak-packs
#   ./make-state-pack.sh MN --install      # ...and install it, force-stopping ATAK
#   ./make-state-pack.sh MN --skip-le      # skip the OpenStreetMap sheriff step
#   ./make-state-pack.sh MN --gaps         # print the per-county gap report,
#                                          # and keep the full list as JSON
#   ./make-state-pack.sh MN --deadline 1800 # more wall clock for the OSM step
#
# It runs the four steps that make a pack, in the order their data depends on:
#
#   1. county seats      Wikidata          -> data/county_seats.local.csv
#   2. sheriff / LE      OpenStreetMap     -> data/le_contacts.local.csv
#   3. the pack          TIGERweb + ACS    -> ~/atak-packs/<ST>_Counties__<ed>.kmz
#   4. install           (only with --install)
#
# EVERY STEP IS RESUMABLE. Boundaries, OSM tiles and the CSVs are all cached or
# written locally, so re-running after a failure picks up where it stopped
# rather than starting over.
#
# STEPS 1 AND 2 ARE ENRICHMENT, AND THEY DO NOT STOP THE RUN. They write
# nothing when they fail, so the pack after them is complete except for the
# fields they fill - and every one of those says "No data for" per county
# rather than guessing. Losing a working pack because the public Overpass
# mirrors were busy is the wrong trade. STEP 3 IS THE PACK: if it fails there
# is nothing to install and the run stops.
#
# REQUIREMENTS
#   pkg install python git            (that is all - no pip, no gdal, no pyproj)
#   export CENSUS_API_KEY=...         free and instant:
#                                     https://api.census.gov/data/key_signup.html
#                                     Without it the pack still builds; population
#                                     and housing read "not in dataset".
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$0")" && pwd -P)"
. "$HERE/atak-env.sh"
SP="$(cd -- "$HERE/.." && pwd -P)"          # statepacks/

# The default filter for step 2. "sheriff" alone misses the county agency in a
# lot of Minnesota: several counties file theirs as "<County> Law Enforcement
# Center", "<County> Jail" or "<County> Public Safety Center", and one is
# spelled "Sherriff". Each term here was read off a real --gaps report, never
# guessed at, and the agency is always written out exactly as the source spells
# it. Override with MATCH=... to be stricter or looser.
MATCH="${MATCH:-sherr?iff|law enforcement cent|county jail|county public safety|justice cent}"

# The OSM step's wall-clock budget. The default inside seed_le_contacts.py is
# tuned for a state whose tiles are already cached; the FIRST fetch of a state
# has none, and a rate-limited mirror is waited out rather than hammered, so
# the first run of a new state is the slow one. Raise it rather than watching
# the step soft-fail and re-running.
state=""; install=0; skip_le=0; skip_seats=0; gaps=""; deadline=""
while [ $# -gt 0 ]; do
  case "$1" in
    --install)     install=1 ;;
    --skip-le)     skip_le=1 ;;
    --skip-seats)  skip_seats=1 ;;
    --gaps)        gaps="--gaps" ;;
    --deadline)    shift; [ $# -gt 0 ] || die "--deadline needs a number of seconds"
                   case "$1" in (*[!0-9]*|"") die "--deadline wants seconds, got '$1'" ;; esac
                   deadline="--deadline $1" ;;
    -h|--help)     sed -n '2,34p' "$0"; exit 0 ;;
    -*)            die "unknown option: $1" ;;
    *)             [ -z "$state" ] || die "one state at a time, got '$state' and '$1'"
                   state="$1" ;;
  esac
  shift
done
[ -n "$state" ] || die "usage: $0 <STATE> [--install] [--skip-le] [--skip-seats] [--gaps] [--deadline S]
  e.g. $0 MN --install"
state="$(printf '%s' "$state" | tr '[:lower:]' '[:upper:]')"
case "$state" in
  [A-Z][A-Z]) ;;
  *) die "expected a two-letter state abbreviation, got '$state'" ;;
esac

command -v python3 >/dev/null || die "python3 not found:  pkg install python"
mkdir -p "$STAGE_DIR"

step=0
banner() { step=$((step+1)); printf '\n'; say "[$step/$total] $*"; }
total=3; [ "$install" -eq 1 ] && total=4

if [ -z "${CENSUS_API_KEY:-}" ] && [ ! -s "$HOME/.config/atak-statepacks/census_key" ]; then
  warn "No Census API key found. The pack will still build, but population and"
  warn "housing will read 'not in dataset'. It is free and instant:"
  warn "    https://api.census.gov/data/key_signup.html"
  warn "    export CENSUS_API_KEY=your_key_here"
  printf '\n'
fi

# An enrichment step that fails writes nothing, so the pack is still worth
# building - it just says "No data for" on the fields that step fills.
# soft <name> <paste-able re-run line> <command...>
# The hint is passed in rather than rebuilt from "$@": the LE filter contains
# pipes, and a line someone is meant to paste has to arrive quoted.
soft() {
  what="$1"; hint="$2"; shift 2
  if "$@"; then return 0; fi
  warn ""
  warn "  [!] $what did not complete. NOTHING WAS WRITTEN by it, so no wrong"
  warn "      value can reach the pack - those fields will read \"No data for\""
  warn "      per county instead. Carrying on with the build."
  warn "      To fill them in, re-run just that step (it resumes from cache):"
  warn "        $hint"
  warn ""
  failed_steps="${failed_steps}${what}, "
  return 0
}

failed_steps=""
if [ "$skip_seats" -eq 0 ]; then
  banner "county seats for $state (Wikidata)"
  soft "county seats" \
       "python3 $SP/fetch_county_seats.py --state $state" \
       python3 "$SP/fetch_county_seats.py" --state "$state"
else
  banner "county seats - SKIPPED (--skip-seats)"
fi

if [ "$skip_le" -eq 0 ]; then
  banner "sheriff / primary LE for $state (OpenStreetMap)"
  say "      filter: /$MATCH/i"
  # With --gaps, also keep the full unmatched list. The printed report samples
  # 20 counties so it stays readable on a phone; on a state nobody has fetched
  # before, the names it does not print are the evidence that decides the
  # filter, and they are gone once the terminal scrolls.
  dump=""
  [ -n "$gaps" ] && dump="--gaps-dump $STAGE_DIR/${state}_le_gaps.json"
  # shellcheck disable=SC2086
  soft "sheriff / primary LE" \
       "python3 $SP/seed_le_contacts.py --state $state --match '$MATCH' $gaps $dump $deadline" \
       python3 "$SP/seed_le_contacts.py" \
       --state "$state" --match "$MATCH" $gaps $dump $deadline
else
  banner "sheriff / primary LE - SKIPPED (--skip-le)"
fi

banner "building the pack (TIGERweb boundaries + ACS)"
# NOT soft: with no pack there is nothing to install.
python3 "$SP/build_county_pack.py" --state "$state" --out "$STAGE_DIR"

if [ "$install" -eq 1 ]; then
  banner "installing into ATAK"
  # Only this state's pack, so an --install does not reinstall every state you
  # have ever built into the same staging folder.
  shopt -s nullglob
  packs=("$STAGE_DIR/${state}_Counties__"*.kmz)
  shopt -u nullglob
  [ ${#packs[@]} -gt 0 ] || die "no ${state}_Counties__*.kmz in $STAGE_DIR - did step 3 run?"
  "$HERE/atak-install.sh" "${packs[@]}"
else
  printf '\n'
  say "Built into $STAGE_DIR"
  say "Install it with:"
  printf '    %s %s --install\n' "$0" "$state"
  say "or by hand:"
  printf '    %s/atak-install.sh %s\n' "$HERE" "$STAGE_DIR"
fi

if [ -n "$failed_steps" ]; then
  printf '\n'
  warn "Built, but these steps did not complete: ${failed_steps%, }"
  warn "Their fields read \"No data for\" rather than a guess. Re-run this"
  warn "command later to fill them in - everything already fetched is cached."
fi
