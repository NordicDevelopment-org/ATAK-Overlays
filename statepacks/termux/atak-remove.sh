#!/data/data/com.termux/files/usr/bin/bash
# atak-remove.sh - take overlay files back OUT of ATAK.
#
#   ./atak-remove.sh MN_Counties_2024.kmz     # one file
#   ./atak-remove.sh 'MN_*'                   # a pattern - QUOTE IT
#   ./atak-remove.sh --all                    # everything (asks twice)
#   ./atak-remove.sh --dry-run 'MN_*'         # show what would go, delete nothing
#
# Use it when a pack is out of date, wrong, or just clutter. It lists exactly
# what it will delete and waits for you to type yes. It force-stops ATAK
# afterwards for the same reason install does: the overlay list is cached, so a
# deleted file can linger in the UI until the app restarts.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$0")" && pwd -P)"
. "$HERE/atak-env.sh"
[ -d "$ATAK_DIR" ] || die "ATAK overlays folder not found: $ATAK_DIR"

# Parse flags in a loop, in ANY order. Checking only $1 meant
# "--all --dry-run" silently ignored --dry-run and really deleted - the one
# safety flag this script has, disappearing exactly when it matters most.
# An unrecognised -flag is an error, never a filename pattern.
dry=0
all=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1; shift ;;
    --all)     all=1; shift ;;
    --)        shift; break ;;
    -*)        die "unknown option: $1
usage: $0 [--dry-run] [--all] [filename|'pattern' ...]" ;;
    *)         break ;;
  esac
done
[ "$all" -eq 1 ] || [ $# -ge 1 ] \
  || die "usage: $0 [--dry-run] <filename|'pattern'|--all>"

# Build the match list. --all takes every overlay; otherwise each argument is a
# filename or a glob matched against the folder.
# The trailing slash matters: find in default -P mode will not descend a
# starting point that is itself a symlink to a directory, and ATAK_DIR very
# often is one (/sdcard -> /storage/emulated/0). Without it this script would
# report "nothing matched" while install happily wrote into the same folder.
files=()
if [ "$all" -eq 1 ]; then
  while IFS= read -r -d '' f; do files+=("$f"); done \
    < <(find "$ATAK_DIR/" -maxdepth 1 -type f \( -name '*.kmz' -o -name '*.kml' \) -print0)
else
  for pat in "$@"; do
    while IFS= read -r -d '' f; do files+=("$f"); done \
      < <(find "$ATAK_DIR/" -maxdepth 1 -type f -name "$pat" -print0)
  done
fi

if [ ${#files[@]} -eq 0 ]; then say "Nothing matched. Nothing removed."; exit 0; fi

say "These ${#files[@]} file(s) will be REMOVED from $ATAK_DIR:"
for f in "${files[@]}"; do
  printf '  %-46s %8s\n' "$(basename "$f")" "$(du -h "$f" | cut -f1)"
done

if [ "$dry" -eq 1 ]; then say ""; say "(dry run - nothing deleted)"; exit 0; fi

say ""
printf 'Type yes to delete: '
read -r ans
[ "$ans" = "yes" ] || die "Aborted. Nothing removed."

if [ "$all" -eq 1 ]; then
  printf 'This removes EVERY overlay. Type DELETE ALL to confirm: '
  read -r ans2
  [ "$ans2" = "DELETE ALL" ] || die "Aborted. Nothing removed."
fi

for f in "${files[@]}"; do rm -f -- "$f" && echo "  removed $(basename "$f")"; done

say ""
say "Force-stopping ATAK so the overlay list refreshes"
am force-stop "$ATAK_PKG" 2>/dev/null \
  || warn "  could not force-stop; do it by hand: Settings > Apps > ATAK > Force stop"
say "Done. Reopen ATAK."
