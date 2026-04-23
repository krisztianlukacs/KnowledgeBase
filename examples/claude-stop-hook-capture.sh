#!/bin/bash
# Example Claude Code Stop hook: capture every session summary into the KB.
#
# Place this file somewhere stable, make it executable, and reference it
# from ~/.claude/settings.json or project-level .claude/settings.local.json:
#
#     "hooks": {
#       "Stop": [
#         { "type": "command", "command": "/path/to/claude-stop-hook-capture.sh" }
#       ]
#     }
#
# The hook fires at the end of every Claude Code interaction. We write a small
# diary entry (just the timestamp + working dir + a message placeholder) — the
# actual summary is written by the agent itself via `kb diary "..."` during the
# conversation when useful moments arise.
#
# Common pattern: add a memory-style instruction to the project CLAUDE.md such as:
#
#     ## KB diary convention
#     Every ~5 message exchanges, call:
#         kb diary "Short English summary of the last 5 exchanges, 1-3 bullets"
#
# Then this Stop hook just ensures the manifest stays fresh (triggers re-index).

set -euo pipefail

# Only run if cwd has a knowledgebase.yaml (i.e., prokb is initialized here)
if [ ! -f "$(pwd)/knowledgebase.yaml" ]; then
    exit 0
fi

# Re-scan the knowledge/diary/ directory if new entries were added
# (kb update will notice new files via mtime+SHA and queue them for indexing)
kb update --index-only 2>/dev/null || true

exit 0
