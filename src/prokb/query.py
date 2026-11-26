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
    from .lock import LockHeldError
    try:
        run_incremental(config)
    except LockHeldError as e:
        # A writer is already indexing; searching the current index is still fine.
        print(f"Skipping auto-reindex: {e}", file=sys.stderr)


def _exact_filtered_search(config, collection, question, top_k, path_filter, after):
    """Exact (brute-force cosine) search over the chunks passing the filters.

    Returns (ids, documents, metadatas, distances), best match first, at most
    top_k long. Distances are cosine distances (0 = identical), like chroma's.
    """
    import numpy as np
    from .indexer import get_embedding_function

    got = collection.get(include=["metadatas", "documents", "embeddings"])
    keep = [
        i for i, meta in enumerate(got["metadatas"])
        if (not path_filter or path_filter in (meta or {}).get("source_file", ""))
        and (not after or (meta or {}).get("date", "") >= after)
    ]
    if not keep:
        return [], [], [], []

    q = np.asarray(get_embedding_function(config)([question])[0], dtype=float)
    emb = np.asarray([got["embeddings"][i] for i in keep], dtype=float)
    denom = np.linalg.norm(emb, axis=1) * np.linalg.norm(q)
    denom[denom == 0] = 1.0
    dist = 1.0 - (emb @ q) / denom
    order = np.argsort(dist, kind="stable")[:top_k]
    return (
        [got["ids"][keep[j]] for j in order],
        [got["documents"][keep[j]] for j in order],
        [got["metadatas"][keep[j]] for j in order],
        [float(dist[j]) for j in order],
    )


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

    # No chroma `where` clause: chromadb >= 1.5 has no substring `$contains`
    # on metadata (silently returns nothing) and `$gte` only accepts numbers
    # (raises on date strings). With a filter we select the matching chunks
    # exactly in Python and rank them by brute-force cosine distance. We do NOT
    # use an ANN query (even with `ids=`): HNSW recall can silently miss
    # matches among near-identical chunks.
    total = collection.count()
    if path_filter or after:
        ids, documents, metadatas, distances = _exact_filtered_search(
            config, collection, question, top_k, path_filter, after)
    else:
        results = collection.query(
            query_texts=[question],
            n_results=min(top_k, total),
            include=["documents", "metadatas", "distances"],
        )
        if results and results["ids"] and results["ids"][0]:
            ids = results["ids"][0]
            documents = results["documents"][0]
            metadatas = results["metadatas"][0]
            distances = results["distances"][0]
        else:
            ids = documents = metadatas = distances = []

    if not ids:
        if as_json:
            print(json.dumps({"query": question, "results": [], "total_chunks": total}))
        else:
            print(f"No results for: \"{question}\"")
        return

    # Apply the path substring filter (and re-check `after`) in Python
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
