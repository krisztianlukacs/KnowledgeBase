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
import io
import threading
from contextlib import contextmanager
from pathlib import Path

from mcp.server.mcpserver import MCPServer  # mcp 2.x (FastMCP was renamed)

from . import __version__
from .indexer import get_collection, load_config, load_manifest
from .query import query as _raw_query


class _ThreadOutput:
    """sys.stdout proxy that routes writes to a per-thread buffer while one is
    set, else to the real stream. Installed once; never nested."""
    def __init__(self, fallback):
        self.fallback = fallback
        self.local = threading.local()

    def write(self, text):
        return getattr(self.local, "stream", self.fallback).write(text)

    def flush(self):
        return getattr(self.local, "stream", self.fallback).flush()

    def __getattr__(self, name):
        return getattr(self.fallback, name)


_output_lock = threading.Lock()


@contextmanager
def _capture_output():
    with _output_lock:
        if not isinstance(sys.stdout, _ThreadOutput):
            sys.stdout = _ThreadOutput(sys.stdout)
        proxy = sys.stdout
    previous = getattr(proxy.local, "stream", None)
    stream = io.StringIO()
    proxy.local.stream = stream
    try:
        yield stream
    finally:
        if previous is None:
            del proxy.local.stream
        else:
            proxy.local.stream = previous


# Load config once at startup (uses cwd-discovery)
_config = load_config()
_project_name = _config.get("project_name", "project")

mcp = MCPServer(
    f"{_project_name}-knowledge",
    version=__version__,
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
    """Semantic search over the project knowledge base.

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
    # Capture per thread: concurrent tool calls must not see each other's output,
    # and nothing may reach the real stdout (it carries the MCP stdio protocol).
    with _capture_output() as buf:
        _raw_query(_config, question, top_k=top_k, after=after,
                   path_filter=path_filter, full=full_text, as_json=True)

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

    return json.dumps({
        "files": results[:max(0, limit)],
        "total_matched": len(results),
        "truncated": len(results) > max(0, limit),
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
