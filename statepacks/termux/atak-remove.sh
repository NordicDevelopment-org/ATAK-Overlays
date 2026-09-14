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
cd "$(dirname "$0")" && . ./atak-env.sh
[ -d "$ATAK_DIR" ] || die "ATAK overlays folder not found: $ATAK_DIR"

dry=0
if [ "${1:-}" = "--dry-run" ]; then dry=1; shift; fi
[ $# -ge 1 ] || die "usage: $0 [--dry-run] <filename|'pattern'|--all>"

# Build the match list. --all takes every overlay; otherwise each argument is a
# filename or a glob matched against the folder.
files=()
if [ "${1:-}" = "--all" ]; then
  while IFS= read -r -d '' f; do files+=("$f"); done \
    < <(find "$ATAK_DIR" -maxdepth 1 -type f \( -name '*.kmz' -o -name '*.kml' \) -print0)
else
  for pat in "$@"; do
    while IFS= read -r -d '' f; do files+=("$f"); done \
      < <(find "$ATAK_DIR" -maxdepth 1 -type f -name "$pat" -print0)
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

if [ "${1:-}" = "--all" ]; then
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
