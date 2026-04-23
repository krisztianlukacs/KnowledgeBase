---
name: knowledge-query
description: Semantic search over the current project's knowledge base via the `kb` CLI. Interprets natural-language questions and presents chunk-level results with source and relevance.
user-invocable: true
---

# /knowledge-query — Knowledge Base Search

Semantic search across all files indexed in the current project's knowledge base,
powered by `prokb` (ChromaDB + sentence-transformers).

## Usage

```
/knowledge-query ISL flip success rate
/knowledge-query endgame strategy --after 2026-03-25
/knowledge-query bilateral build --path measurements/early-in-bot
/knowledge-query P3 AHC pricing --top 15 --full
```

## What it does

- Translates your question into a vector embedding
- Ranks all indexed chunks by cosine similarity
- Returns the top N hits with source file, section, date, and content preview
- Works regardless of exact keyword match (handles paraphrases, synonyms)

## Execution Steps

### 1. Parse the args

Everything before `--` flags is the search query:

| Flag | Description |
|---|---|
| `--top N` | Number of results (default 10) |
| `--after YYYY-MM-DD` | Only results dated on/after this |
| `--path <substring>` | Only results whose source path contains this |
| `--full` | Show full chunk content, not just preview |

### 2. Run the query

```bash
kb query "THE QUESTION" --json --top N [--after DATE] [--path PATH] [--full]
```

The CLI auto-detects the project root (looks for `knowledgebase.yaml` in
cwd or parents). No need to pass project paths.

### 3. Present the results

Parse the JSON output, then:

1. Briefly summarize what the user asked
2. Group the top results into thematic clusters if applicable
3. For each result, cite: `<source file>` (date), `<section>`, key content
4. If results are weak (all scores < 0.3), suggest reformulating the question
5. Offer a follow-up: suggest running `kb query` with a tighter `--path` filter
   if many hits come from one area

### 4. Recommend follow-up

After presenting, ask: *"Want me to dive deeper into any of these? Or refine
the query?"* — this keeps the conversation productive.

## Output format

Keep it concise. User wants the answer, not a wall of JSON. Summarize,
cite sources, offer next steps.

## See also

- `/knowledge-update` to refresh the index (translation + re-embedding)
- `kb status` to see index health without running a search
