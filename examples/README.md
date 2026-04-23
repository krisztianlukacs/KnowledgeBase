# Examples

Ready-to-adapt scripts for common prokb integrations.

## `knowledge-translate-cron.sh`

A nightly cron script that runs your AI agent against the `/knowledge-update`
skill between fixed hours (default 02:00-08:00 UTC) to translate any files
queued as `pending_translation`. Stops automatically when the queue drains or
the wall-clock cutoff hits.

**Why nightly**: AI API tokens (Claude / Gemini / GPT) have daily budgets. Use
them for bulk translation while you sleep — during the day, save credits for
development work.

**Why multi-agent**: if Claude tokens are exhausted but Gemini credits remain,
the same `/knowledge-update` skill works with any AI CLI that supports
`--allowedTools` and file access. Swap `AGENT_BIN` to:

- Claude: `/home/$USER/.local/bin/claude`
- Gemini: `/home/$USER/.local/bin/gemini` (if installed)
- Any OpenAI-compatible CLI with file I/O support
- A custom orchestrator script that picks whichever has budget that day

### Install

```bash
cp knowledge-translate-cron.sh <your-project>/scripts/
chmod +x <your-project>/scripts/knowledge-translate-cron.sh

# Edit the PROJECT_DIR + AGENT_BIN variables at the top
# Then install to crontab:
crontab -e
# Add:
0 2 * * * /path/to/your-project/scripts/knowledge-translate-cron.sh \
          >> /path/to/your-project/logs/kb-translate.log 2>&1
```

## `claude-stop-hook-capture.sh`

A Claude Code Stop hook that triggers after every message exchange. Together
with a convention in your project's `CLAUDE.md` telling the agent to call
`kb diary "..."` every ~5 messages, this captures conversation summaries
automatically into the knowledge base (indexed and searchable later).

### Install

In your `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "/absolute/path/to/claude-stop-hook-capture.sh"
      }
    ]
  }
}
```

And in the project's `CLAUDE.md`:

```
## Diary convention

Every ~5 message exchanges, capture a short English summary into the KB:

    kb diary "Three bullets on the last 5 exchanges" \
       --title "session-<short-topic>" \
       --agent claude --tags "session,exploration"

Use the title to help future searches (`/knowledge-query session-<topic>`).
```

## Multi-agent translation orchestrator (sketch)

If you want to use whichever AI agent has budget available, a simple daily
orchestrator can round-robin across them:

```bash
#!/bin/bash
# Picks the first AI agent with remaining budget today and runs the cron.
if has_claude_credits; then
    AGENT_BIN=/home/$USER/.local/bin/claude exec ./knowledge-translate-cron.sh
elif has_gemini_credits; then
    AGENT_BIN=/home/$USER/.local/bin/gemini exec ./knowledge-translate-cron.sh
elif has_gpt_credits; then
    AGENT_BIN=/home/$USER/.local/bin/chatgpt exec ./knowledge-translate-cron.sh
else
    echo "No budget for any agent today." >&2
fi
```

`has_*_credits` is a project-specific helper that queries your API quotas
(e.g., a `gh api` call or local counter). Implementation depends on the agent
providers.

## See also

- Top-level `README.md` for install + basic usage
- `src/prokb/skills/knowledge-update/SKILL.md` for the translation pipeline
  contract that any AI agent must follow
