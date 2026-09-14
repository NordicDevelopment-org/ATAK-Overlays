#!/data/data/com.termux/files/usr/bin/bash
# atak-install.sh - copy KMZ/KML packs into ATAK and make ATAK actually see them.
#
#   ./atak-install.sh ~/atak-packs/MN_Counties_2024.kmz
#   ./atak-install.sh ~/atak-packs/*.kmz
#   ./atak-install.sh ~/atak-packs            # a whole folder
#
# THE FORCE STOP IS THE POINT. ATAK caches its overlay list in memory. Dropping
# a file into the folder while it is running usually does nothing visible - the
# new pack shows up only after the app is restarted from scratch. This script
# copies the files and then force-stops ATAK so the next launch re-reads the
# folder. Reopen ATAK yourself afterwards.
set -euo pipefail
cd "$(dirname "$0")" && . ./atak-env.sh

[ $# -ge 1 ] || die "usage: $0 <file.kmz|folder> [more...]"
[ -d "$ATAK_DIR" ] || die "ATAK overlays folder not found: $ATAK_DIR
Open ATAK once so it creates it, or set ATAK_DIR to your path."

# Collect the files to install: explicit files, or every KMZ/KML in a folder.
files=()
for arg in "$@"; do
  if [ -d "$arg" ]; then
    while IFS= read -r -d '' f; do files+=("$f"); done \
      < <(find "$arg" -maxdepth 1 -type f \( -name '*.kmz' -o -name '*.kml' \) -print0)
  elif [ -f "$arg" ]; then
    files+=("$arg")
  else
    warn "skipping (not found): $arg"
  fi
done
[ ${#files[@]} -gt 0 ] || die "nothing to install"

say "Installing ${#files[@]} file(s) into $ATAK_DIR"
for f in "${files[@]}"; do
  base="$(basename "$f")"
  # -f so a re-download replaces the old copy instead of failing
  cp -f "$f" "$ATAK_DIR/$base"
  printf '  %-46s %s\n' "$base" "$(du -h "$ATAK_DIR/$base" | cut -f1)"
done

say ""
say "Force-stopping ATAK so it re-reads the overlay folder"
# Needs no root. If Android refuses, the manual path is printed below.
if am force-stop "$ATAK_PKG" 2>/dev/null; then
  say "  stopped $ATAK_PKG"
else
  warn "  could not force-stop $ATAK_PKG automatically."
  warn "  Do it by hand - this step is REQUIRED or the new files will not appear:"
  warn "    Settings > Apps > ATAK > Force stop"
  warn "  (check your package name with:  pm list packages | grep -i atak)"
fi

say ""
say "Done. Now:"
say "  1. Open ATAK."
say "  2. Overlay Manager (the stacked-layers button)."
say "  3. Your pack is listed by filename - tap the eye to toggle it."
