#!/usr/bin/env bash
# Build the whole Sonix library from the upstream sources: fetch (git) -> prep and renders -> engine/*.snx.
#   tools/build_library.sh
# Runs the Python steps in Docker (python:3.12-slim with numpy, scipy, soundfile), so the only things needed on the
# machine are git and Docker. Takes several minutes and about 2 GB of downloads the first time; the source clones
# in raw/ are reused afterwards.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

bash "$REPO/tools/fetch_sources.sh"

docker run --rm -v "$REPO":/r -w /r python:3.12-slim sh -euc '
  pip install -q --root-user-action=ignore --disable-pip-version-check numpy scipy soundfile
  python3 tools/build_demo.py
  python3 tools/build_batch2.py
  python3 tools/build_batch3.py
  python3 -I tools/pack_sonix.py
  python3 tools/sonix_params.py
  chown -R '"$(id -u):$(id -g)"' library engine src vst
'
echo "library built: $(du -sh "$REPO/engine" | cut -f1) in $REPO/engine"
