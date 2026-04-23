#!/usr/bin/env python3
"""Knowledge Base Indexer — chunk, embed, store in ChromaDB.

Reads English translations (or original English files), chunks them,
generates embeddings with sentence-transformers, and stores in ChromaDB.

Usage:
    python knowledge/indexer.py --incremental   # Process pending files only
    python knowledge/indexer.py --full           # Rebuild entire index
    python knowledge/indexer.py --status         # Show index stats
"""

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

from .chunker import chunk_markdown
from .config import (
    load_config as _load_config, manifest_path as _manifest_path,
    chroma_db_path as _chroma_db_path, translations_dir as _translations_dir,
    project_root,
)


def load_config():
    return _load_config()


def load_manifest(config):
    mp = _manifest_path(config)
    if mp.exists():
        with open(mp) as f:
            return json.load(f)
    return {}


def save_manifest(config, manifest):
    mp = _manifest_path(config)
    mp.parent.mkdir(parents=True, exist_ok=True)
    with open(mp, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def get_chroma_client(config):
    import chromadb
    db_path = str(_chroma_db_path(config))
    Path(db_path).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=db_path)


def get_embedding_function(config):
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    model_name = config["embedding"]["model"]
    device = config["embedding"].get("device", "cpu")
    return SentenceTransformerEmbeddingFunction(
        model_name=model_name, device=device
    )


def get_collection(config):
    client = get_chroma_client(config)
    ef = get_embedding_function(config)
    return client.get_or_create_collection(
        name=config["collection_name"],
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


def get_source_path(config, relpath, manifest_entry):
    """Get the path to the file to embed (translation or original).

    If translation is enabled and the file is in the source_language, we
    look for its translated version in translations_dir. Otherwise use
    the original file as-is.
    """
    root = project_root(config)
    source_lang = config["translation"]["source_language"]

    if (config["translation"].get("enabled", False)
            and manifest_entry.get("language") == source_lang):
        trans_dir = _translations_dir(config)
        tr_path = trans_dir / relpath
        if tr_path.exists():
            return str(tr_path)
        return None
    else:
        # Native-language file — use original
        return str(root / relpath)


def index_file(collection, config, relpath, manifest_entry):
    """Index a single file: chunk → embed → store."""
    root = project_root(config)
    file_to_embed = get_source_path(config, relpath, manifest_entry)

    if file_to_embed is None:
        return 0, "no_translation"

    try:
        with open(file_to_embed, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except FileNotFoundError:
        return 0, "file_not_found"

    if not text.strip():
        return 0, "empty_file"

    # Remove translation header comment if present
    text = text.lstrip()
    if text.startswith("<!--"):
        end = text.find("-->")
        if end != -1:
            text = text[end + 3:].lstrip()

    date = manifest_entry.get("date", "")
    max_tokens = config.get("max_chunk_tokens", 1500)
    min_tokens = config.get("min_chunk_tokens", 50)

    chunks = chunk_markdown(
        text,
        source_file=relpath,
        date=date,
        max_tokens=max_tokens,
        min_tokens=min_tokens,
    )

    if not chunks:
        return 0, "no_chunks"

    # Build chunk IDs using hash of relpath + content hash (avoids collisions
    # when different files have the same content hash)
    import hashlib
    path_hash = hashlib.md5(relpath.encode()).hexdigest()[:8]
    content_hash = manifest_entry.get("original_hash", "unknown")[:8]
    hash_prefix = f"{path_hash}_{content_hash}"

    # Delete old chunks for this file (if any)
    try:
        existing = collection.get(where={"source_file": relpath})
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        pass  # Collection might be empty or field doesn't exist yet

    # Prepare batch
    ids = []
    documents = []
    metadatas = []

    for chunk in chunks:
        chunk_id = f"{hash_prefix}_{chunk['index']:03d}"
        ids.append(chunk_id)
        documents.append(chunk["text"])
        metadatas.append({
            "source_file": relpath,
            "section": chunk["section"],
            "date": date or "",
            "language": manifest_entry.get("language", "en"),
            "chunk_index": chunk["index"],
            "content_preview": chunk["content"][:300],
        })

    # Add to ChromaDB (batched)
    batch_size = 100
    for i in range(0, len(ids), batch_size):
        collection.add(
            ids=ids[i:i + batch_size],
            documents=documents[i:i + batch_size],
            metadatas=metadatas[i:i + batch_size],
        )

    return len(chunks), "ok"


def delete_file_chunks(collection, relpath):
    """Remove all chunks for a file from ChromaDB."""
    try:
        existing = collection.get(where={"source_file": relpath})
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
            return len(existing["ids"])
    except Exception:
        pass
    return 0


def run_incremental(config):
    """Process only pending_index files."""
    manifest = load_manifest(config)
    collection = get_collection(config)

    indexed = 0
    skipped = 0
    errors = 0
    deleted = 0

    for relpath, entry in sorted(manifest.items()):
        status = entry.get("status", "")

        if status == "pending_index":
            count, result = index_file(collection, config, relpath, entry)
            if result == "ok":
                entry["status"] = "indexed"
                entry["indexed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                entry["chunk_count"] = count
                indexed += 1
                print(f"  Indexed: {relpath} ({count} chunks)")
            elif result == "no_translation":
                skipped += 1
                # Keep as pending — translation not yet done
            else:
                errors += 1
                entry["status"] = "error"
                entry["error"] = result
                print(f"  Error: {relpath} — {result}", file=sys.stderr)

        elif status == "deleted":
            n = delete_file_chunks(collection, relpath)
            deleted += 1
            # Remove from manifest
            # (handled below to avoid dict mutation during iteration)

    # Clean up deleted entries
    to_delete = [rp for rp, e in manifest.items() if e.get("status") == "deleted"]
    for rp in to_delete:
        # Also remove translation if exists
        if config["translation"].get("enabled"):
            tr_path = _translations_dir(config) / rp
            if tr_path.exists():
                tr_path.unlink()
        del manifest[rp]

    save_manifest(config, manifest)

    print(f"\nIncremental index complete: {indexed} indexed, {skipped} skipped (need translation), "
          f"{errors} errors, {deleted} deleted")
    return indexed


def run_full(config):
    """Full rebuild: re-chunk and re-embed everything."""
    manifest = load_manifest(config)
    root = project_root(config)

    # Clear existing collection
    client = get_chroma_client(config)
    try:
        client.delete_collection(config["collection_name"])
    except Exception:
        pass
    collection = get_collection(config)

    indexed = 0
    skipped = 0
    errors = 0

    for relpath, entry in sorted(manifest.items()):
        # Skip files without content
        original = root / relpath
        if not original.exists():
            continue

        count, result = index_file(collection, config, relpath, entry)
        if result == "ok":
            entry["status"] = "indexed"
            entry["indexed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            entry["chunk_count"] = count
            indexed += 1
            if indexed % 50 == 0:
                print(f"  Progress: {indexed} files indexed...")
        elif result == "no_translation":
            skipped += 1
        else:
            errors += 1
            entry["status"] = "error"
            entry["error"] = result

    save_manifest(config, manifest)
    print(f"\nFull rebuild complete: {indexed} indexed, {skipped} skipped, {errors} errors")
    return indexed


def show_status(config):
    manifest = load_manifest(config)
    total = len(manifest)
    indexed = sum(1 for e in manifest.values() if e.get("status") == "indexed")
    pending_trans = sum(1 for e in manifest.values() if e.get("status") == "pending_translation")
    pending_idx = sum(1 for e in manifest.values() if e.get("status") == "pending_index")
    error = sum(1 for e in manifest.values() if e.get("status") == "error")
    hu = sum(1 for e in manifest.values() if e.get("language") == "hu")
    en = sum(1 for e in manifest.values() if e.get("language") == "en")

    total_chunks = sum(e.get("chunk_count", 0) for e in manifest.values())

    try:
        collection = get_collection(config)
        chroma_count = collection.count()
    except Exception:
        chroma_count = "N/A"

    print(f"Knowledge Base Index Status:")
    print(f"  Manifest entries:      {total}")
    print(f"  Indexed:               {indexed}")
    print(f"  Pending translation:   {pending_trans}")
    print(f"  Pending index:         {pending_idx}")
    print(f"  Errors:                {error}")
    print(f"  Hungarian files:       {hu}")
    print(f"  English files:         {en}")
    print(f"  Total chunks:          {total_chunks}")
    print(f"  ChromaDB chunks:       {chroma_count}")


def main():
    parser = argparse.ArgumentParser(description="Knowledge Base Indexer")
    parser.add_argument("--incremental", action="store_true", help="Process pending files only")
    parser.add_argument("--full", action="store_true", help="Full rebuild")
    parser.add_argument("--status", action="store_true", help="Show index stats")
    args = parser.parse_args()

    config = load_config()

    if args.status:
        show_status(config)
    elif args.full:
        print("Starting full rebuild...")
        run_full(config)
    elif args.incremental:
        print("Starting incremental index...")
        run_incremental(config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
