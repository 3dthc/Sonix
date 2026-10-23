#!/usr/bin/env bash
# The GitHub Actions build (.github/workflows/release.yml): the packed instruments (engine/*.snx, about 130 MB) are built
# by tools/build_library.sh and not committed, so CI takes them from an earlier release zip with the same library version
# (engine/LIBRARY_VERSION), then builds the plugin and stamps the skin's version (as make_release.sh does).
#   VERSION=0.3.10 MPC_VST=../mpc-vst-plugins bash tools/ci_build.sh
# SONIX_ENGINE_FROM: the release tag whose zip holds the instruments (default below; bump it when the library changes).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
FROM="${SONIX_ENGINE_FROM:-v0.3.9}"
WANT="$(cat "$REPO/engine/LIBRARY_VERSION")"
if ! ls "$REPO"/engine/*.snx >/dev/null 2>&1; then
  TMP="$(mktemp -d)"
  ver="${FROM#v}"
  curl -fsSL -o "$TMP/sonix.zip" "https://github.com/3dthc/Sonix/releases/download/$FROM/Sonix-$ver-mpc-armv7.zip"
  unzip -q "$TMP/sonix.zip" '*/engine/*' -d "$TMP/x"
  src="$(dirname "$(find "$TMP/x" -name LIBRARY_VERSION | head -1)")"
  got="$(cat "$src/LIBRARY_VERSION")"
  [ "$got" = "$WANT" ] || { echo "release $FROM has library $got, this source needs $WANT: set SONIX_ENGINE_FROM" >&2; exit 1; }
  cp "$src"/*.snx "$REPO/engine/"
  rm -rf "$TMP"
  echo "instruments: $(ls "$REPO"/engine/*.snx | wc -l) .snx files from $FROM (library $got)"
fi
bash "$REPO/vst/build.sh"
B="$REPO/vst/build"
SKIN_VER="$(echo "${VERSION:?set VERSION}" | awk -F. '{printf "%d.%d.%d.0", $1, $2, $3}')"
sed -i "s|<version>[^<]*</version>|<version>$SKIN_VER</version>|" "$B/skin/3dthc - VST - Sonix/version.xml"
grep -q "<version>$SKIN_VER</version>" "$B/skin/3dthc - VST - Sonix/version.xml" || { echo "could not stamp the skin version" >&2; exit 1; }
