#!/data/data/com.termux/files/usr/bin/bash
# atak-env.sh - shared settings for the ATAK helper scripts.
# Sourced by the others; you normally do not run this directly.
#
# ATAK reads overlays from this folder. On most builds it is:
#     /storage/emulated/0/atak/overlays
# Some older builds and some forks use /sdcard/atak/overlays (same place,
# different name for it). Override with:  export ATAK_DIR=/your/path
ATAK_DIR="${ATAK_DIR:-/storage/emulated/0/atak/overlays}"

# Where packs are downloaded/built before being copied in.
STAGE_DIR="${STAGE_DIR:-$HOME/atak-packs}"

# The Android package name ATAK-CIV installs under. WinTAK/other forks differ;
# find yours with:   pm list packages | grep -i atak
ATAK_PKG="${ATAK_PKG:-com.atakmap.app.civ}"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
