#!/usr/bin/env bash
# Clone the upstream sample sets Sonix is built from into raw/, fetching only the folders it uses.
#   tools/fetch_sources.sh
set -euo pipefail
RAW="$(cd "$(dirname "$0")/.." && pwd)/raw"
mkdir -p "$RAW"

fetch() {  # fetch <owner/repo> <path> [path ...]
  local repo=$1; shift
  local dir="$RAW/$(basename "$repo")"
  if [ ! -d "$dir/.git" ]; then
    GIT_LFS_SKIP_SMUDGE=1 git clone -q --filter=blob:none --no-checkout "https://github.com/$repo.git" "$dir"
    git -C "$dir" config gc.auto 0
  fi
  git -C "$dir" checkout -q HEAD -- "$@"
}

fetch sgossner/VSCO-2-CE LICENSE \
  "Strings/Violin Section/susVib" "Strings/Viola Section/susvib" "Strings/Cello Section/susvib" \
  "Brass/Trumpet/sus" "Brass/F Horn/sus" "Brass/Tenor Trombone/sus" \
  "Strings/Solo Violin/Arco Vib" "Strings/Violin Section/Pizz" "Strings/Viola Section/pizz" \
  "Strings/Cello Section/pizzT" "Strings/Violin Section/Trem" "Strings/Viola Section/trem" \
  "Strings/Cello Section/trem" "Strings/Violin Section/Spic" "Strings/Viola Section/spic" \
  "Strings/Cello Section/spic" "Strings/Solo Contrabass/Pizz" "Woodwinds/Flute/susvib"

fetch sgossner/VCSL LICENSE \
  "Idiophones/Struck Idiophones/Vibraphone" "Idiophones/Struck Idiophones/Glockenspiel" \
  "Idiophones/Struck Idiophones/Tubular Bells 2" "Idiophones/Struck Idiophones/Marimba" \
  "Chordophones/Composite Chordophones/Concert Harp" "Idiophones/Plucked Idiophones/Kalimba, Tanzania" \
  "Chordophones/Zithers/Harpsichord, Flemish" "Aerophones/Edge-blown Aerophones/Pipe Organ" \
  "Electrophones/TX81Z/Clavisynth" "Aerophones/Reed Aerophones/Tenor Saxophone/Vibrato"

# Salamander: 3 of the 16 velocity layers
dir="$RAW/SalamanderGrandPiano"
if [ ! -d "$dir/.git" ]; then
  GIT_LFS_SKIP_SMUDGE=1 git clone -q --filter=blob:none --no-checkout https://github.com/sfzinstruments/SalamanderGrandPiano.git "$dir"
  git -C "$dir" config gc.auto 0
fi
git -C "$dir" ls-tree -r --name-only HEAD Samples | grep -E 'v(6|11|16)\.flac$' > "$dir/.sonix-files"
git -C "$dir" checkout -q HEAD --pathspec-from-file="$dir/.sonix-files"
git -C "$dir" checkout -q HEAD -- LICENSE README.md

fetch facebookincubator/Meta-open-sound LICENSE README.md \
  systems/instruments/meta-piano systems/instruments/meta-nylon-guitar systems/instruments/meta-steel-guitar

echo "sources ready in $RAW"
