#!/bin/bash
# Example cron script: nightly auto-translation for the knowledge base
#
# Drops the Claude CLI (or any AI agent CLI) against `/knowledge-update`
# between 02:00 and 08:00 UTC. Stops early if no files are pending or the
# wall clock reaches the cutoff.
#
# Crontab example:
#   0 2 * * * /path/to/project/scripts/knowledge-translate-cron.sh \
#             >> /path/to/project/logs/knowledge-translate.log 2>&1

set -euo pipefail

# ----- Configure these for your project -----
PROJECT_DIR="${PROJECT_DIR:-$(pwd)}"
AGENT_BIN="${AGENT_BIN:-/home/$(whoami)/.local/bin/claude}"     # Claude CLI
STOP_HOUR="${STOP_HOUR:-8}"        # Stop at 08:00 UTC
BATCH_SIZE="${BATCH_SIZE:-20}"     # Files per batch
MAX_BATCHES="${MAX_BATCHES:-15}"   # Safety cap
# --------------------------------------------

LOG_PREFIX="[kb-translate]"
cd "$PROJECT_DIR"

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $LOG_PREFIX $*"
}

check_time() {
    local hour
    hour=$(date -u '+%H' | sed 's/^0//')
    if [ "$hour" -ge "$STOP_HOUR" ]; then
        log "STOP — past ${STOP_HOUR}:00 UTC cutoff."
        return 1
    fi
    return 0
}

check_pending() {
    kb status 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('pending_translation', 0))
" || echo "0"
}

log "=== session start ==="

batch=0
while [ "$batch" -lt "$MAX_BATCHES" ]; do
    check_time || break

    pending=$(check_pending)
    log "pending translations: $pending"

    if [ "$pending" -eq 0 ]; then
        log "DONE — no more files pending."
        break
    fi

    batch=$((batch + 1))
    log "batch $batch/$MAX_BATCHES (up to $BATCH_SIZE files)..."

    # Run the AI agent against the /knowledge-update skill.
    # Substitute AGENT_BIN with a different AI's CLI if Claude credits are out:
    #   - Gemini:  GEMINI_API_KEY=... gemini -p "/knowledge-update --batch $BATCH_SIZE"
    #   - GPT-CLI: chatgpt -p "/knowledge-update --batch $BATCH_SIZE"
    "$AGENT_BIN" -p "/knowledge-update --batch $BATCH_SIZE" \
        --allowedTools "Read,Write,Edit,Bash,Glob,Grep,Agent" \
        --max-turns 50 \
        --output-format text \
        2>&1 | while IFS= read -r line; do
            echo "$(date -u '+%H:%M:%S') | $line"
        done

    log "batch $batch done."
    sleep 10
done

log "=== session end ($batch batches) ==="
