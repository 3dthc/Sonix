#!/usr/bin/env bash
# Sonix: one-step build, from a clone of this repo and the instruments zip to a zip ready for the MPC.
#
#   bash ~/Sonix/install.sh
#
# What it does:
#   1. finds sonix-engine-v<ENGINE_VERSION>.zip in your Windows Downloads (or ~) and unpacks the instruments into engine/
#   2. checks Docker is running and that mpc-vst-plugins is next to this folder (clones it if not)
#   3. builds the plugin and packages dist/Sonix-<version>-mpc-armv7.zip
#   4. copies that zip to your Windows Downloads, ready to put on the MPC
# Safe to run again: each step skips what is already done.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VERSION="0.1.0"
ENGINE_VERSION="0.1.0"       # the instruments zip (a GitHub release asset of this repo)
MPC_VST="${MPC_VST:-$HERE/../mpc-vst-plugins}"
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

INSTRUMENTS="$(sed -n 's/^    "\([a-z_]*\)",$/\1/p' "$HERE/src/instruments.h" | head -n "$(sed -n 's/#define NINST //p' "$HERE/src/instruments.h")")"
DL_DIRS=()
for d in /mnt/c/Users/*/Downloads; do [ -d "$d" ] && DL_DIRS+=("$d"); done
DL_DIRS+=("$HOME" "$HERE/..")

# ---- 1. instruments
step "1/4  Instruments"
missing() { local m=""; for f in $INSTRUMENTS; do [ -f "$HERE/engine/$f.snx" ] || m="$m $f"; done; echo "$m"; }
mkdir -p "$HERE/engine"
if [ -n "$(missing)" ]; then
  zip=""
  for d in "${DL_DIRS[@]}"; do
    [ -f "$d/sonix-engine-v$ENGINE_VERSION.zip" ] && { zip="$d/sonix-engine-v$ENGINE_VERSION.zip"; break; }
  done
  [ -n "$zip" ] || fail "sonix-engine-v$ENGINE_VERSION.zip not found in: ${DL_DIRS[*]}
       Download it from the repo's Releases page on GitHub (v$ENGINE_VERSION) and run this again."
  echo "unpacking $zip"
  python3 -m zipfile -e "$zip" "$HERE/engine/"
  [ -d "$HERE/engine/engine" ] && { mv "$HERE"/engine/engine/*.snx "$HERE/engine/"; rmdir "$HERE/engine/engine"; }
fi
m="$(missing)"
[ -z "$m" ] || fail "still missing in engine/:$m"
echo "all $(echo $INSTRUMENTS | wc -w) instrument files present ($(du -sh "$HERE/engine" | cut -f1))"

# ---- 2. tools
step "2/4  Checking tools"
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
