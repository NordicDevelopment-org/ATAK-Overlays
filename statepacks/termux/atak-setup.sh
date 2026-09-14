#!/data/data/com.termux/files/usr/bin/bash
# atak-setup.sh - one-time Termux setup. Run this FIRST, once.
#
# Installs every dependency the other scripts need and wires up storage access.
# Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")" && . ./atak-env.sh

say "[1/4] Updating package lists"
pkg update -y && pkg upgrade -y

say "[2/4] Installing dependencies"
#   python  - runs the builders (standard library only, no pip packages needed)
#   git     - to clone/update this repo
#   curl    - to download prebuilt packs
#   termux-api (optional) - nicer notifications; not required
pkg install -y python git curl

say "[3/4] Granting Termux access to shared storage"
# Creates ~/storage/* symlinks. Android will show a permission prompt - ALLOW it.
# If nothing happens, grant it by hand:
#   Settings > Apps > Termux > Permissions > Files and media > Allow all
termux-setup-storage || warn "termux-setup-storage failed; grant storage permission by hand"
sleep 2

say "[4/4] Checking the ATAK overlays folder"
if [ -d "$ATAK_DIR" ]; then
  say "    found: $ATAK_DIR"
else
  warn "    NOT found: $ATAK_DIR"
  warn "    Open ATAK once so it creates its folders, then re-run this script."
  warn "    If your build uses a different path, set it:  export ATAK_DIR=/your/path"
fi

mkdir -p "$STAGE_DIR"
say ""
say "Setup done."
say "  ATAK overlays : $ATAK_DIR"
say "  Staging folder: $STAGE_DIR"
python3 --version
