# KnowledgeBase (`prokb`) — Per-project semantic search with MCP

A lightweight, ChromaDB-backed knowledge base that every project in your dev
environment can share. Install **once** on the dev server, configure **once**
per project (one `knowledgebase.yaml` file), and get:

- Semantic search over your project's markdown + code (`kb query "..."`)
- Incremental indexing (mtime + SHA-256, only re-indexes changed files)
- Optional Claude-assisted translation to a chosen target language (for when
  multilingual embedding models aren't enough)
- MCP server exposing `knowledge_search`, `knowledge_status`,
  `knowledge_list_sources` to any Claude Code agent
- Globally shared embedding model cache (~420 MB — 2 GB depending on model)

## Install (once per dev server)

```bash
pipx install git+https://github.com/krisztianlukacs/KnowledgeBase.git
# Updates: pipx upgrade prokb
```

## Set up a project (30 seconds)

```bash
cd /path/to/your/project
kb init                 # creates knowledgebase.yaml + knowledge/ structure
kb update               # first index (downloads embedding model on first run)
kb query "how does feature X work"
```

## Claude Code integration

One-time per dev server:

```bash
kb install-skills       # adds /knowledge-query and /knowledge-update to ~/.claude/skills
```

Per-project `.mcp.json`:

```json
{
  "mcpServers": {
    "project-knowledge": {
      "type": "stdio",
      "command": "kb",
      "args": ["mcp"],
      "cwd": "/absolute/path/to/project"
    }
  }
}
```

## What stays in the project

```
<project>/
├── .mcp.json                  # one entry — run `kb mcp` as stdio server
├── knowledgebase.yaml         # project-specific config (committed)
└── knowledge/
    ├── chroma_db/             # vector store (gitignored)
    ├── manifest.json          # file state (gitignored)
    └── translations/          # Claude translations (optional commit)
```

No Python source lives in your project — the code is the global `prokb`
package. `pipx upgrade prokb` on the dev server updates every project at once.

## Configurable: embedding model + language

```yaml
embedding:
  model: all-mpnet-base-v2            # 420 MB, English-strong, general
  # multilingual-e5-large             # 2 GB, 50+ languages incl. Hungarian
  # all-MiniLM-L6-v2                  # 90 MB, fast, slight quality loss
  device: cpu                         # cpu / cuda / mps

translation:
  enabled: false                      # set true if source lang needs pre-translation
  source_language: hu                 # ISO 639-1 code
  target_language: en
  translations_dir: knowledge/translations
```

For projects where your files are mostly English, leave translation off. For
mixed-language projects (e.g., Hungarian docs + English code comments), pick
either:
- **Option A**: multilingual embedding model (fast, no extra step)
- **Option B**: Claude-assisted translation (slower, best quality)

## CLI commands

```
kb init                   # create knowledgebase.yaml + directories
kb update [--batch N]     # scan + translate + index
kb query "..." [--top N] [--after DATE] [--path PATTERN]
kb status                 # index state
kb mcp                    # run MCP stdio server (called by .mcp.json)
kb install-skills         # drop /knowledge-* skills into ~/.claude/skills
kb doctor                 # diagnose: model cache, chroma writable, MCP deps
```

## License

MIT.
