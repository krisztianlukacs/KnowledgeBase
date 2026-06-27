#!/usr/bin/env bash
# Run the prokb test suite using the pipx-installed interpreter (which has all
# runtime deps + pytest injected). Pass KB_RUN_INTEGRATION=1 to also run the
# model-backed index+query roundtrip.
#
#   ./scripts/test.sh                      # fast, model-free unit suite
#   KB_RUN_INTEGRATION=1 ./scripts/test.sh # include the e2e roundtrip
set -euo pipefail
cd "$(dirname "$0")/.."
PY="$(dirname "$(readlink -f "$(command -v kb)")")/python"
exec "$PY" -m pytest "$@"
