#!/usr/bin/env bash
# Sonix: one-step build, from a clone of this repo to a zip ready for the MPC.
#
#   bash ~/Sonix/install.sh
#
# What it does:
#   1. checks Docker is running and that mpc-vst-plugins is next to this folder (clones it if not)
#   2. builds the instruments into engine/ from their open sources (tools/build_library.sh; first time only), or
#      unpacks sonix-engine-v<ENGINE_VERSION>.zip if you have one in your Windows Downloads (or ~)
#   3. builds the plugin and packages dist/Sonix-<version>-mpc-armv7.zip
#   4. copies that zip to your Windows Downloads, ready to put on the MPC
# Safe to run again: each step skips what is already done.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VERSION="0.3.4"
ENGINE_VERSION="0.3.0"       # an optional prebuilt instruments zip
MPC_VST="${MPC_VST:-$HERE/../mpc-vst-plugins}"
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

INSTRUMENTS="$(sed -n 's/^    "\([a-z_]*\)",$/\1/p' "$HERE/src/instruments.h" | head -n "$(sed -n 's/#define NINST //p' "$HERE/src/instruments.h")")"
DL_DIRS=()
for d in /mnt/c/Users/*/Downloads; do [ -d "$d" ] && DL_DIRS+=("$d"); done
DL_DIRS+=("$HOME" "$HERE/..")

# ---- 1. tools
step "1/4  Checking tools"
command -v python3 >/dev/null || fail "python3 is missing:  sudo apt install python3"
command -v docker  >/dev/null || fail "Docker is not available in WSL. Start Docker Desktop (with WSL integration on) and run this again."
docker info >/dev/null 2>&1   || fail "Docker is not running. Start Docker Desktop, wait until it says running, then run this again."
echo "docker ok"
if [ ! -f "$MPC_VST/tools/build_port.sh" ]; then
  command -v git >/dev/null || fail "mpc-vst-plugins not found at $MPC_VST and git is missing (sudo apt install git)"
  echo "mpc-vst-plugins not found, cloning it to $MPC_VST"
  git clone --depth 1 https://github.com/sd88me/mpc-vst-plugins.git "$MPC_VST"
fi
echo "toolchain: $(cd "$MPC_VST" && pwd)"
export MPC_VST

# ---- 2. instruments
step "2/4  Instruments"
missing() { local m=""; for f in $INSTRUMENTS; do [ -f "$HERE/engine/$f.snx" ] || m="$m $f"; done; echo "$m"; }
mkdir -p "$HERE/engine"
if [ "$(cat "$HERE/engine/LIBRARY_VERSION" 2>/dev/null)" != "$ENGINE_VERSION" ]; then
  echo "the instruments in engine/ are from an older version: rebuilding them"
  rm -f "$HERE"/engine/*.snx "$HERE/engine/LIBRARY_VERSION"
fi
if [ -n "$(missing)" ]; then
  zip=""
  for d in "${DL_DIRS[@]}"; do
    [ -f "$d/sonix-engine-v$ENGINE_VERSION.zip" ] && { zip="$d/sonix-engine-v$ENGINE_VERSION.zip"; break; }
  done
  if [ -n "$zip" ]; then
    echo "unpacking $zip"
    python3 -m zipfile -e "$zip" "$HERE/engine/"
    [ -d "$HERE/engine/engine" ] && { mv "$HERE"/engine/engine/*.snx "$HERE/engine/"; rmdir "$HERE/engine/engine"; }
  else
    echo "building the instruments from their open sources (first time only: several minutes, ~2 GB of downloads)"
    command -v git >/dev/null || fail "git is missing:  sudo apt install git"
    bash "$HERE/tools/build_library.sh"
  fi
fi
m="$(missing)"
[ -z "$m" ] || fail "still missing in engine/:$m"
echo "all $(echo $INSTRUMENTS | wc -w) instrument files present ($(du -sh "$HERE/engine" | cut -f1))"

# ---- 3. build
step "3/4  Building Sonix (the first build can take a few minutes)"
rm -f "$HERE"/dist/Sonix-*-mpc-armv7.zip
bash "$HERE/make_release.sh" "$VERSION"
out="$HERE/dist/Sonix-$VERSION-mpc-armv7.zip"
[ -f "$out" ] || fail "build finished without $out"

# ---- 4. hand it over
step "4/4  Done"
copied=""
for d in /mnt/c/Users/*/Downloads; do
  [ -d "$d" ] && [ -w "$d" ] && cp "$out" "$d/" && { copied="$d/$(basename "$out")"; break; }
done
echo "MPC install zip: $out"
if [ -n "$copied" ]; then
  echo "also copied to Windows: $copied"
  echo "  (in Windows Explorer: Downloads\\$(basename "$out"))"
fi
echo
echo "Install it on the MPC the same way as your other plugins (see TESTING.md)."
