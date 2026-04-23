#!/usr/bin/env python3
"""Knowledge Base MCP Server — exposes semantic search over project docs.

Wraps the existing knowledge/query.py and knowledge/indexer.py modules
as an MCP stdio server, so external AI agents can search the PolyFastWay
knowledge base via the MCP protocol.

Usage (standalone test):
    python knowledge/mcp_server.py

Register in .mcp.json:
    "polyfastway-knowledge": {
        "type": "stdio",
        "command": "/path/to/.venv/bin/python",
        "args": ["-m", "knowledge.mcp_server"],
        "cwd": "/path/to/PolyFastWay"
    }
"""

import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .indexer import get_collection, load_config, load_manifest
from .query import query as _raw_query

# Load config once at startup (uses cwd-discovery)
_config = load_config()
_project_name = _config.get("project_name", "project")

mcp = FastMCP(
    f"{_project_name}-knowledge",
    instructions=(
        f"Semantic search over the {_project_name} project knowledge base. "
        "Indexed markdown and code files with optional translation. Use the "
        "search tool for any project-related question."
    ),
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def knowledge_search(
    question: str,
    top_k: int = 10,
    after: str | None = None,
    path_filter: str | None = None,
    full_text: bool = False,
) -> str:
    """Semantic search over the PolyFastWay knowledge base.

    Args:
        question: Natural language query (English works best, Hungarian OK).
        top_k: Max number of results (default 10).
        after: Only return results dated on or after this date (YYYY-MM-DD).
        path_filter: Only return results whose source file path contains this substring
                     (e.g. "measurements/early-in-bot", "docs/delta-bot", "aegis").
        full_text: If True, include the full chunk text (not just preview).

    Returns:
        JSON with query results including score, source file, section, date, and content.
    """
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        _raw_query(
            _config,
            question,
            top_k=top_k,
            after=after,
            path_filter=path_filter,
            full=full_text,
            as_json=True,
        )

    output = buf.getvalue().strip()
    if not output:
        return json.dumps({"query": question, "results": [], "error": "No output from query"})
    return output


@mcp.tool()
def knowledge_status() -> str:
    """Show knowledge base index status: total files, indexed count, pending translations, chunk counts.

    Returns:
        JSON with index statistics.
    """
    manifest = load_manifest(_config)
    total = len(manifest)
    indexed = sum(1 for e in manifest.values() if e.get("status") == "indexed")
    pending_trans = sum(1 for e in manifest.values() if e.get("status") == "pending_translation")
    pending_idx = sum(1 for e in manifest.values() if e.get("status") == "pending_index")
    error_count = sum(1 for e in manifest.values() if e.get("status") == "error")
    hu = sum(1 for e in manifest.values() if e.get("language") == "hu")
    en = sum(1 for e in manifest.values() if e.get("language") == "en")
    total_chunks = sum(e.get("chunk_count", 0) for e in manifest.values())

    try:
        collection = get_collection(_config)
        chroma_count = collection.count()
    except Exception:
        chroma_count = -1

    return json.dumps({
        "manifest_entries": total,
        "indexed": indexed,
        "pending_translation": pending_trans,
        "pending_index": pending_idx,
        "errors": error_count,
        "hungarian_files": hu,
        "english_files": en,
        "manifest_chunks": total_chunks,
        "chromadb_chunks": chroma_count,
    })


@mcp.tool()
def knowledge_list_sources(
    path_filter: str | None = None,
    status_filter: str | None = None,
    limit: int = 50,
) -> str:
    """List indexed source files in the knowledge base.

    Args:
        path_filter: Only show files whose path contains this substring.
        status_filter: Filter by status: "indexed", "pending_translation", "pending_index", "error".
        limit: Max number of files to return (default 50).

    Returns:
        JSON list of source files with their status, date, language, and chunk count.
    """
    manifest = load_manifest(_config)

    results = []
    for relpath, entry in sorted(manifest.items()):
        if path_filter and path_filter not in relpath:
            continue
        if status_filter and entry.get("status") != status_filter:
            continue

        results.append({
            "path": relpath,
            "status": entry.get("status", "unknown"),
            "language": entry.get("language", ""),
            "date": entry.get("date", ""),
            "chunk_count": entry.get("chunk_count", 0),
        })

        if len(results) >= limit:
            break

    return json.dumps({
        "files": results,
        "total_matched": len(results),
        "truncated": len(results) >= limit,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
