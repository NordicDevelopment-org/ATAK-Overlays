#!/data/data/com.termux/files/usr/bin/bash
# atak-list.sh - show what is currently installed in the ATAK overlays folder,
# newest first, with sizes. Run this before removing anything.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$0")" && pwd -P)"
. "$HERE/atak-env.sh"
[ -d "$ATAK_DIR" ] || die "ATAK overlays folder not found: $ATAK_DIR"

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
