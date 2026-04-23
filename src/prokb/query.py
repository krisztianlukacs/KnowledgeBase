#!/usr/bin/env python3
"""Knowledge Base Query — semantic search over indexed markdown files.

Usage:
    python knowledge/query.py "ISL flip success rate"
    python knowledge/query.py "endgame strategy" --top 15
    python knowledge/query.py "bilateral build" --after 2026-03-25
    python knowledge/query.py "hedge cascade" --path measurements/early-in-bot
    python knowledge/query.py "P3 AHC pricing" --full
    python knowledge/query.py "question" --json
    python knowledge/query.py "question" --no-reindex
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from .config import (
    load_config as _load_config, manifest_path as _manifest_path,
)


def load_config():
    return _load_config()


def auto_reindex(config):
    """Run incremental indexing for any pending_index files (fast, no translation)."""
    mp = _manifest_path(config)
    if not mp.exists():
        return

    with open(mp) as f:
        manifest = json.load(f)

    pending = sum(1 for e in manifest.values() if e.get("status") == "pending_index")
    if pending == 0:
        return

    print(f"Auto-indexing {pending} pending files...", file=sys.stderr)
    from .indexer import run_incremental
    run_incremental(config)


def query(config, question, top_k=10, after=None, path_filter=None, full=False, as_json=False):
    """Run semantic search and return results."""
    from .indexer import get_collection

    collection = get_collection(config)

    if collection.count() == 0:
        if as_json:
            print(json.dumps({"error": "Index is empty. Run /knowledge-update first.", "results": []}))
        else:
            print("Index is empty. Run /knowledge-update or `python knowledge/indexer.py --full` first.")
        return

    # Build where filter
    where_filter = None
    where_clauses = []

    if after:
        where_clauses.append({"date": {"$gte": after}})
    if path_filter:
        where_clauses.append({"source_file": {"$contains": path_filter}})

    if len(where_clauses) == 1:
        where_filter = where_clauses[0]
    elif len(where_clauses) > 1:
        where_filter = {"$and": where_clauses}

    # Query
    try:
        results = collection.query(
            query_texts=[question],
            n_results=min(top_k, collection.count()),
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        if "where" in str(e).lower() or "filter" in str(e).lower():
            # Retry without filter
            results = collection.query(
                query_texts=[question],
                n_results=min(top_k, collection.count()),
                include=["documents", "metadatas", "distances"],
            )
        else:
            raise

    if not results or not results["ids"] or not results["ids"][0]:
        if as_json:
            print(json.dumps({"query": question, "results": [], "total_chunks": collection.count()}))
        else:
            print(f"No results for: \"{question}\"")
        return

    ids = results["ids"][0]
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    # Apply path filter manually if ChromaDB $contains didn't work
    filtered = []
    for i in range(len(ids)):
        meta = metadatas[i]
        if path_filter and path_filter not in meta.get("source_file", ""):
            continue
        if after and meta.get("date", "") < after:
            continue

        # ChromaDB cosine distance: 0 = identical, 2 = opposite
        # Convert to similarity score: 1 - (distance/2)
        score = 1 - (distances[i] / 2)

        filtered.append({
            "id": ids[i],
            "score": round(score, 4),
            "source_file": meta.get("source_file", ""),
            "section": meta.get("section", ""),
            "date": meta.get("date", ""),
            "language": meta.get("language", ""),
            "content_preview": meta.get("content_preview", ""),
            "full_text": documents[i] if full else None,
        })

    # Sort by score descending
    filtered.sort(key=lambda x: x["score"], reverse=True)
    filtered = filtered[:top_k]

    # Count pending translations
    mp = _manifest_path(config)
    pending_trans = 0
    if mp.exists():
        with open(mp) as f:
            manifest = json.load(f)
        pending_trans = sum(1 for e in manifest.values() if e.get("status") == "pending_translation")

    if as_json:
        output = {
            "query": question,
            "results": filtered,
            "total_chunks": collection.count(),
            "pending_translation": pending_trans,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        # Human-readable output
        print(f"Query: \"{question}\"")
        stale_note = f" ({pending_trans} pending translation)" if pending_trans else ""
        print(f"Index: {collection.count()} chunks{stale_note}")
        if after:
            print(f"Filter: after {after}")
        if path_filter:
            print(f"Filter: path contains \"{path_filter}\"")
        print()

        if not filtered:
            print("No matching results.")
            return

        for i, r in enumerate(filtered):
            print(f"[{i + 1}] Score: {r['score']:.3f} | {r['date'] or 'no date'} | {r['source_file']}")
            print(f"    Section: {r['section']}")

            if full and r["full_text"]:
                # Show full text
                print(f"    ---")
                for line in r["full_text"].split("\n"):
                    print(f"    {line}")
                print(f"    ---")
            else:
                # Show preview
                preview = r["content_preview"].replace("\n", " ").strip()
                if len(preview) > 200:
                    preview = preview[:200] + "..."
                print(f"    > {preview}")
            print()


def main():
    parser = argparse.ArgumentParser(description="Knowledge Base Query")
    parser.add_argument("question", nargs="?", help="Search query")
    parser.add_argument("--top", type=int, default=None, help="Number of results (default: from config)")
    parser.add_argument("--after", type=str, default=None, help="Filter by date (YYYY-MM-DD)")
    parser.add_argument("--path", type=str, default=None, help="Filter by source file path substring")
    parser.add_argument("--full", action="store_true", help="Show full chunk content")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--no-reindex", action="store_true", help="Skip auto-reindex")
    args = parser.parse_args()

    if not args.question:
        parser.print_help()
        sys.exit(1)

    config = load_config()
    top_k = args.top or config.get("default_top_k", 10)

    if not args.no_reindex:
        auto_reindex(config)

    query(config, args.question, top_k=top_k, after=args.after,
          path_filter=args.path, full=args.full, as_json=args.json)


if __name__ == "__main__":
    main()
