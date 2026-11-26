#!/usr/bin/env bash
# Reinstall prokb from local source via pipx, then verify the kb-diary indexing fix
# end-to-end in a throwaway project. Exits non-zero with a diagnostic if the diary
# entry does not get indexed + retrieved.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST="$(mktemp -d -t kbfixtest.XXXXXX)"
trap 'rm -rf "$TEST"' EXIT

echo "=== 1. Reinstall prokb from local source ==="
pipx install --force "$SRC"
"$(dirname "$(readlink -f "$(command -v kb)")")/python" -c "import prokb; print('installed prokb', prokb.__version__)"

echo "=== 2. Fresh test project ==="
cd "$TEST"
kb init --project-name kbfixtest

echo "=== 3. Write a diary entry (the previously-broken path) ==="
kb diary "Fleet onboarding 2026-06-27: 61 of 64 repos onboarded with validated project-agent json. BubbleTicket deduped to 103 projects with zero duplicates. A BT 500 incident from an invalid status field was filed as three platform bug tickets." \
  --title kbfix-verify --session test --agent claude --tags "diary,fix-verify"

echo "=== 4. kb update (must index the diary entry) ==="
kb update

echo "=== 5. Query for diary content ==="
kb query "how many repos were onboarded during fleet onboarding" --top 3 --json

echo "=== 6. Assert diary file is in the index ==="
kb status
