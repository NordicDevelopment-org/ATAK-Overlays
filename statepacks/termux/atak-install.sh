#!/data/data/com.termux/files/usr/bin/bash
# atak-install.sh - copy KMZ/KML packs into ATAK and make ATAK actually see them.
#
#   ./atak-install.sh ~/atak-packs/MN_Counties__Current_2026-09-16.kmz
#   ./atak-install.sh ~/atak-packs/*.kmz
#   ./atak-install.sh ~/atak-packs            # a whole folder
#   ./atak-install.sh --keep-old ~/atak-packs # keep previous editions too
#   ./atak-install.sh ~/atak-packs --keep-old # the flag works in either position
#
# Installing a pack RETIRES older editions of the same pack, so a rebuild does
# not leave the previous copy drawing underneath this one.
#
# THE FORCE STOP IS THE POINT. ATAK caches its overlay list in memory. Dropping
# a file into the folder while it is running usually does nothing visible - the
# new pack shows up only after the app is restarted from scratch. This script
# copies the files and then force-stops ATAK so the next launch re-reads the
# folder. Reopen ATAK yourself afterwards.
set -euo pipefail
# Resolve our own directory WITHOUT chdir'ing into it: the caller's relative
# paths ("./packs/MN.kmz") have to keep resolving against THEIR cwd, not ours.
HERE="$(cd -- "$(dirname -- "$0")" && pwd -P)"
. "$HERE/atak-env.sh"

# --keep-old anywhere in the argument list, not just as $1. `if [ "$1" =
# --keep-old ]` missed the completely natural `atak-install.sh <packs>
# --keep-old`, and the one flag that exists to PREVENT deletion silently did
# nothing when typed that way: it fell through to the file-collection loop
# below, printed "skipping (not found): --keep-old", and the retire step ran
# anyway. Verified by running the real script both ways.
keep_old=0
rest=()
for arg in "$@"; do
  if [ "$arg" = "--keep-old" ]; then keep_old=1; else rest+=("$arg"); fi
done
set -- "${rest[@]+"${rest[@]}"}"
[ $# -ge 1 ] || die "usage: $0 [--keep-old] <file.kmz|folder> [more...]"
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

# Two editions of the SAME pack in one invocation must not both reach the
# copy loop below. Each iteration of that loop retires every OTHER edition of
# its own family already in ATAK_DIR - so with two same-family files in this
# one batch, whichever is processed SECOND deletes the one processed FIRST,
# regardless of which is actually newer. Bash glob order is alphabetical, and
# an edition's trailing content-hash (added so a same-day rebuild gets a
# distinct filename) has no relationship to build time - so the file that
# survives was effectively a coin flip.
#
# Reproduced for real: two same-day editions in one folder, freshest built 5
# minutes ago, the other left over from the day before. Installing both in
# one call kept the DAY-OLD file and deleted the one just rebuilt.
#
# Fixed by settling each family to its single newest-by-mtime file BEFORE the
# copy loop runs, so at most one candidate per family ever reaches it. Source
# mtime is the right signal here - these are freshly built local files, not
# something copied or extracted, so "when this file was written" is exactly
# "how recently it was built". Older packs left in the same folder from a
# PRIOR run (make-state-pack.sh's staging directory is never cleared) are
# named and skipped, not silently dropped.
declare -A newest_path newest_time
skipped_older=()
for f in "${files[@]}"; do
  base="$(basename "$f")"
  case "$base" in
    *__*) fam="${base%%__*}" ;;
    *)    fam="" ;;                       # no version boundary; nothing to dedup
  esac
  if [ -z "$fam" ]; then
    continue
  fi
  mt=$(stat -c '%Y' -- "$f" 2>/dev/null || stat -f '%m' -- "$f" 2>/dev/null || echo 0)
  if [ -z "${newest_time[$fam]:-}" ] || [ "$mt" -gt "${newest_time[$fam]}" ]; then
    if [ -n "${newest_path[$fam]:-}" ]; then
      skipped_older+=("${newest_path[$fam]}")
    fi
    newest_time[$fam]=$mt
    newest_path[$fam]=$f
  else
    skipped_older+=("$f")
  fi
done
if [ ${#skipped_older[@]} -gt 0 ]; then
  say "${#skipped_older[@]} older edition(s) in this same batch will not be" \
      "installed at all (a newer edition of the same pack is also in this" \
      "batch):"
  for s in "${skipped_older[@]}"; do
    say "  $(basename "$s")"
  done
fi
# Files with no "__" pass through unchanged - one per file, nothing to settle.
settled=()
for f in "${files[@]}"; do
  base="$(basename "$f")"
  case "$base" in
    *__*) fam="${base%%__*}"
          [ "$f" = "${newest_path[$fam]}" ] && settled+=("$f") ;;
    *)    settled+=("$f") ;;
  esac
done
files=("${settled[@]}")

say "Installing ${#files[@]} file(s) into $ATAK_DIR"
copied=0
failed=0
retired=0
for f in "${files[@]}"; do
  base="$(basename "$f")"
  dest="$ATAK_DIR/$base"

  # Already the same file (re-running against the ATAK folder itself)? Skip,
  # otherwise the copy-through-temp below would delete it.
  if [ "$f" -ef "$dest" ] 2>/dev/null; then
    printf '  %-46s %s\n' "$base" "(already installed, skipped)"
    continue
  fi

  # Copy to a temp name in the SAME folder, then rename. A plain "cp over the
  # destination" truncates the live file first, so a copy interrupted by a full
  # card or a yanked SD leaves a corrupt overlay where a working one used to
  # be. Same-filesystem rename is atomic: the old file survives until the new
  # one is complete.
  tmp="$ATAK_DIR/.${base}.part.$$"
  if cp -f -- "$f" "$tmp" 2>/dev/null && mv -f -- "$tmp" "$dest"; then
    copied=$((copied+1))
    printf '  %-46s %s\n' "$base" "$(du -h "$dest" | cut -f1)"

    # Retire older editions of the SAME pack. "__" separates a pack's identity
    # from its version: MN_Counties__Current_2026-09-16 is a new edition of
    # MN_Counties, and leaving the previous one behind draws every county twice
    # and lists the pack twice in Overlay Manager.
    #
    # A filename with no "__" carries no version, so nothing is retired for it -
    # that is what keeps MN_Water from being read as an edition of MN_Energy.
    case "$base" in
      *__*)
        if [ "$keep_old" -eq 0 ]; then
          family="${base%%__*}"
          while IFS= read -r -d '' old; do
            [ "$(basename "$old")" != "$base" ] || continue
            rm -f -- "$old" && printf '  %-46s %s\n' \
              "$(basename "$old")" "(superseded, removed)"
            retired=$((retired+1))
          done < <(find "$ATAK_DIR/" -maxdepth 1 -type f \
                        \( -name "${family}__*.kmz" -o -name "${family}__*.kml" \) \
                        -print0)

          # A pack built before the "__" convention has only one underscore, so
          # it matches neither the retire glob above nor its own - nothing ever
          # retires it, and ATAK draws the state twice with no sign why. That
          # happened for real: MN_Counties_Current_2026_09_14.kmz sat alongside
          # MN_Counties__Current_2026_09_14.kmz for a day. Deleting a file on a
          # guess is not this script's call, so it says so and leaves it.
          while IFS= read -r -d '' anc; do
            ab="$(basename "$anc")"
            [ "$ab" != "$base" ] || continue
            case "$ab" in
              *__*) continue ;;
            esac
            warn "  $ab"
            warn "      looks like a pre-'__' edition of ${family}. It carries no"
            warn "      version, so nothing will ever retire it and ATAK will draw"
            warn "      this pack twice. Remove it when you have checked it:"
            warn "        $HERE/atak-remove.sh '$ab'"
          done < <(find "$ATAK_DIR/" -maxdepth 1 -type f \
                        \( -name "${family}_*.kmz" -o -name "${family}_*.kml" \) \
                        -print0)
        fi
        ;;
    esac
  else
    rm -f -- "$tmp" 2>/dev/null || true
    failed=$((failed+1))
    warn "  FAILED: $base (existing copy left untouched)"
  fi
done

if [ "$copied" -eq 0 ]; then
  die "nothing was copied ($failed failure(s)); ATAK not restarted"
fi

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
[ "$retired" -eq 0 ] || say "$retired older edition(s) removed (use --keep-old to keep them)"
[ "$failed" -eq 0 ] || warn "$failed file(s) failed to copy; $copied succeeded"
say "Done. Now:"
say "  1. Open ATAK."
say "  2. Overlay Manager (the stacked-layers button)."
say "  3. Your pack is listed by filename - tap the eye to toggle it."
