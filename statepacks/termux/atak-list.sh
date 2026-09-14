#!/data/data/com.termux/files/usr/bin/bash
# atak-list.sh - show what is currently installed in the ATAK overlays folder.
# Run this before removing anything.
#
#   ./atak-list.sh              # what is there, what is inside it, what is wrong
#   ./atak-list.sh --quick      # names, sizes and dates only
#   ./atak-list.sh --json FILE  # also write the findings
#
# With python available it opens each file and reports the placemark count,
# whether the document says where it came from, and anything fighting with
# anything else - two editions of one pack, a pre-"__" file nothing can retire,
# a KMZ that parses but draws nothing. It only ever READS: removal lines are
# printed for you to run, never run for you.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$0")" && pwd -P)"
. "$HERE/atak-env.sh"
SP="$(cd -- "$HERE/.." && pwd -P)"
[ -d "$ATAK_DIR" ] || die "ATAK overlays folder not found: $ATAK_DIR"

# The deep inventory is python; the listing below is the fallback for a phone
# without it. python is already required to build a pack, so this is normally
# the path taken.
if command -v python3 >/dev/null && [ -f "$SP/atak_inventory.py" ]; then
  exec python3 "$SP/atak_inventory.py" --dir "$ATAK_DIR" "$@"
fi
[ $# -eq 0 ] || warn "python3 not found - ignoring $* and listing plainly"

say "Overlays in $ATAK_DIR"

# find -printf and date -r are GNU extensions. Termux normally ships GNU
# findutils/coreutils, but busybox and toybox builds do not have them - so try
# the sorted-by-time path and fall back to plain alphabetical if it fails.
# The trailing slash on ATAK_DIR makes find descend it even when it is a
# symlink (/sdcard -> /storage/emulated/0), which it usually is.
list_by_mtime() {
  find "$ATAK_DIR/" -maxdepth 1 -type f \( -name '*.kmz' -o -name '*.kml' \) \
       -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-
}
list_plain() {
  find "$ATAK_DIR/" -maxdepth 1 -type f \( -name '*.kmz' -o -name '*.kml' \) | sort
}
# `listing="$(list_by_mtime)"` would abort under `set -e` on a busybox find
# that has no -printf, so the fallback below could never run. Guard it.
if ! listing="$(list_by_mtime)" || [ -z "$listing" ]; then
  listing="$(list_plain || true)"
fi

n=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  n=$((n+1))
  when="$(date -r "$f" '+%Y-%m-%d %H:%M' 2>/dev/null || echo '        -')"
  printf '  %-46s %8s  %s\n' "$(basename "$f")" "$(du -h "$f" | cut -f1)" "$when"
done <<EOF
$listing
EOF
[ "$n" -gt 0 ] || say "  (empty)"
say ""
say "$n overlay file(s).  Total: $(du -sh "$ATAK_DIR" 2>/dev/null | cut -f1)"
