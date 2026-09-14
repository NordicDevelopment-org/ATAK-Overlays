#!/data/data/com.termux/files/usr/bin/bash
# make-state-pack.sh - one command for a whole state's county pack.
#
#   ./make-state-pack.sh MN                # fetch everything, build into ~/atak-packs
#   ./make-state-pack.sh MN --install      # ...and install it, force-stopping ATAK
#   ./make-state-pack.sh MN --skip-le      # skip the OpenStreetMap sheriff step
#   ./make-state-pack.sh MN --gaps         # print the per-county gap report too
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
# rather than starting over. A step that fails stops the run: a pack built on
# half-fetched data would look complete.
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

state=""; install=0; skip_le=0; skip_seats=0; gaps=""
while [ $# -gt 0 ]; do
  case "$1" in
    --install)     install=1 ;;
    --skip-le)     skip_le=1 ;;
    --skip-seats)  skip_seats=1 ;;
    --gaps)        gaps="--gaps" ;;
    -h|--help)     sed -n '2,30p' "$0"; exit 0 ;;
    -*)            die "unknown option: $1" ;;
    *)             [ -z "$state" ] || die "one state at a time, got '$state' and '$1'"
                   state="$1" ;;
  esac
  shift
done
[ -n "$state" ] || die "usage: $0 <STATE> [--install] [--skip-le] [--skip-seats] [--gaps]
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

if [ "$skip_seats" -eq 0 ]; then
  banner "county seats for $state (Wikidata)"
  python3 "$SP/fetch_county_seats.py" --state "$state"
else
  banner "county seats - SKIPPED (--skip-seats)"
fi

if [ "$skip_le" -eq 0 ]; then
  banner "sheriff / primary LE for $state (OpenStreetMap)"
  say "      filter: /$MATCH/i"
  python3 "$SP/seed_le_contacts.py" --state "$state" --match "$MATCH" $gaps
else
  banner "sheriff / primary LE - SKIPPED (--skip-le)"
fi

banner "building the pack (TIGERweb boundaries + ACS)"
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
