---
name: knowledge-update
description: Update the project knowledge base — scan for changed files, translate (optional, via Claude), re-embed and store in ChromaDB.
user-invocable: true
---

# /knowledge-update — Knowledge Base Update

Orchestrates the three-stage pipeline:

1. **Scan** (Python): detect new/changed/deleted files via mtime + SHA-256
2. **Translate** (Claude Code): translate source-language files to target language
   (only if `translation.enabled: true` in `knowledgebase.yaml`)
3. **Index** (Python): chunk translated text, generate embeddings, store in ChromaDB

## Usage

```
/knowledge-update                  # Translate + index up to 20 pending files
/knowledge-update --batch 50       # Larger batch size
/knowledge-update --index-only     # Skip translation, index only files already translated
/knowledge-update --status         # Show current index health
```

## When to run

- After a burst of writing (new measurements, strategy docs, reports)
- On a schedule (nightly cron is a common pattern — see the project README)
- Before running `/knowledge-query` against fresh content

## Execution Steps

### 1. Parse args

- `--batch N` → max files to translate per run (default 20)
- `--index-only` → skip translation step
- `--status` → only report, do not modify

### 2. Check the current state

```bash
kb status
```

Parse the JSON. Note `pending_translation` and `pending_index` counts.

### 3. Translate (if translation is enabled)

If the project's config has `translation.enabled: true`:

1. Run `kb query "::list-pending-translations" --json` (internal pattern)
   or read `knowledge/manifest.json` to identify files with status `pending_translation`

2. For each pending file (up to --batch N):
   - Read the original source-language file (Read tool)
   - Translate it to target language, preserving markdown structure
   - Write the translated file to the configured `translations_dir`
     (relative path mirrored)
   - Update manifest entry: `status: pending_index`

3. When batch is complete, proceed to indexing.

**Translation quality guidelines**:
- Preserve all markdown structure (headers, lists, code blocks, tables)
- Translate comments inside code blocks but NOT variable/function names
- Keep file paths, URLs, dates, numbers, and commit hashes verbatim
- If the source language is already the target language, just copy the file

### 4. Index

```bash
kb update --index-only   # or just: kb update (auto-runs index after translation)
```

This chunks each ready file, generates embeddings, and stores in ChromaDB.

### 5. Report the result

Summarize:
- How many translated
- How many indexed
- Remaining pending (if batch was capped)
- Any errors encountered

## Alternative translators

If your Claude tokens are exhausted but you have credits with another AI agent
(Gemini, GPT, local LLM), you can substitute this step externally. The manifest
format is simple JSON; any agent can:

1. Read `knowledge/manifest.json`
2. Pick files with `status: pending_translation`
3. Translate them, save to `translations_dir`
4. Update manifest entry to `status: pending_index`
5. Run `kb update --index-only` to finalize

See the project's `cron/knowledge-translate-cron.sh` example for an overnight
auto-translation pattern.

## See also

- `/knowledge-query` to search the knowledge base
- `kb doctor` to diagnose install / config issues
- `kb status` to check index health
