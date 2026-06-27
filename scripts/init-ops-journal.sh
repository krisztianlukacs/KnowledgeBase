#!/usr/bin/env bash
# Create the fleet-wide ops-journal KB project: a per-project knowledge base whose
# sole job is to hold daily ops/work reports, semantically searchable across time.
# Idempotent — safe to re-run.
set -euo pipefail

JOURNAL="${OPS_JOURNAL_DIR:-/home/lukacsk/Development/ops-journal}"

echo "=== ops-journal at $JOURNAL ==="
mkdir -p "$JOURNAL"
cd "$JOURNAL"

[ -d .git ] || git init -q
if [ ! -f knowledgebase.yaml ]; then
  kb init --project-name ops-journal
fi

# Track the chroma_db index + manifest in git (override kb init's default .gitignore,
# per the fleet convention — the index must travel with the repo).
if [ -f .gitignore ]; then
  sed -i '/^# kb (prokb)/d; /knowledge\/chroma_db\//d; /knowledge\/manifest.json/d' .gitignore
fi

mkdir -p reports
echo "ops-journal ready."
