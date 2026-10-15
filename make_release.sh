#!/usr/bin/env bash
# One command from source to an installable MPC zip:
#   ./make_release.sh [version]        (default: the version below)
# Builds the plugin (vst/build.sh, Docker), then packages it with mpc-vst-plugins' release tool into
# dist/Sonix-<version>-mpc-armv7.zip, with the instruments (engine/*.snx) next to the .so.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
MPC_VST="${MPC_VST:-$HERE/../mpc-vst-plugins}"
VERSION="${1:-0.3.4}"
B="$HERE/vst/build"

bash "$HERE/vst/build.sh"
[ -f "$B/sonix.so" ] || { echo "build failed: no $B/sonix.so" >&2; exit 1; }

python3 "$MPC_VST/tools/release.py" \
  --so "$B/sonix.so" \
  --skin "$B/skin/3dthc - VST - Sonix" \
  --entry "$B/pluginlist-entry.xml" \
  --version "$VERSION" \
  --repo 3dthc/Sonix --id sonix \
  --extra "$HERE/engine:engine" \
  --about "A ROMpler with 128 presets and 45 instruments: pianos, keys, guitars, strings, brass, choirs, pads, synths, leads, plucks, bass and bells, from CC0 / CC BY recordings and original renders. Four layers, filter, LFO, chorus, delay, reverb, eleven colorways." \
  -o "$HERE/dist"

echo
echo "Release zip:"
ls -la "$HERE/dist/"*.zip
