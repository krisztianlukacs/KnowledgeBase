#!/usr/bin/env python3
"""Knowledge Base Scanner — file change detection + manifest management.

Scans all .md files in the project, compares mtime + SHA-256 against manifest.json,
detects language, and reports which files need translation or indexing.

Usage:
    python knowledge/scanner.py --pending          # JSON list of files needing work
    python knowledge/scanner.py --pending --human   # Human-readable pending list
    python knowledge/scanner.py --status            # Summary stats
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import yaml

try:
    from langdetect import detect, LangDetectException  # type: ignore
    _HAS_LANGDETECT = True
except ImportError:
    _HAS_LANGDETECT = False

from .config import (
    load_config as _load_config, manifest_path as _manifest_path,
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


def file_hash(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_language(filepath, source_lang: str = "hu", target_lang: str = "en"):
    """Detect if file is in the source or target language.

    Returns the source_lang (e.g. 'hu') if detected as needing translation,
    otherwise target_lang (e.g. 'en').

    Uses langdetect + language-specific heuristics when available.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(5000)
    except Exception:
        return target_lang

    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"[#*|>_\-=]", " ", text)
    text = re.sub(r"\S+/\S+", "", text)
    text = re.sub(r"\$[\d.,]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) < 30:
        return target_lang

    # Primary: langdetect (supports 50+ languages)
    if _HAS_LANGDETECT:
        try:
            detected = detect(text)
            if detected == source_lang:
                return source_lang
        except LangDetectException:
            pass

    # Fallback heuristic for common source languages (Hungarian bundled here
    # as legacy; other languages rely on langdetect alone).
    if source_lang == "hu":
        markers = [
            r"\b(és|vagy|hogy|nem|van|egy|volt|lett|csak|már|még|mint|után|alatt)\b",
            r"\b(pedig|tehát|viszont|mert|mivel|ezért|ugyanis|továbbá)\b",
            r"\b(amikor|ahol|amelyik|ahogy|amíg|ameddig)\b",
            r"\b(kötelező|kritikus|fontos|szükséges|lehetséges)\b",
        ]
        hu_count = sum(len(re.findall(p, text, re.IGNORECASE)) for p in markers)
        if hu_count >= 5:
            return "hu"

    return target_lang


def extract_date(filepath, relpath):
    """Extract date from file path or name. Returns YYYY-MM-DD or None."""
    # Try path patterns like 2026-03-31
    match = re.search(r"(\d{4}-\d{2}-\d{2})", str(relpath))
    if match:
        return match.group(1)
    # Try filename patterns
    match = re.search(r"(\d{4})(\d{2})(\d{2})", Path(filepath).stem)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None


def scan_files(config):
    """Scan files matching include patterns, return dict of relpath → file info."""
    root = Path(config["project_root"])
    scan = config.get("scan", {})
    include_patterns = scan.get("include", ["**/*.md"])
    exclude_dirs = set(scan.get("exclude_dirs", []))
    exclude_files = set(scan.get("exclude_files", []))
    files = {}

    for pattern in include_patterns:
        for fpath in root.glob(pattern):
            if not fpath.is_file():
                continue
            relpath = str(fpath.relative_to(root))

            parts = Path(relpath).parts
            if any(excl in parts for excl in exclude_dirs):
                continue
            if relpath in exclude_files:
                continue

            stat = fpath.stat()
            files[relpath] = {
                "abspath": str(fpath),
                "mtime": stat.st_mtime,
                "size": stat.st_size,
            }

    return files


def compute_pending(config):
    """Compare current files against manifest, return pending actions."""
    manifest = load_manifest(config)
    current_files = scan_files(config)
    root = Path(config["project_root"])

    pending = []
    unchanged = 0
    new_files = 0
    changed_files = 0
    deleted_files = 0

    # Check current files against manifest
    for relpath, info in sorted(current_files.items()):
        entry = manifest.get(relpath)

        if entry is None:
            # New file
            h = file_hash(info["abspath"])
            lang = detect_language(info["abspath"])
            date = extract_date(info["abspath"], relpath)
            new_files += 1
            status = "pending_translation" if lang == "hu" else "pending_index"
            pending.append({
                "relpath": relpath,
                "action": "new",
                "language": lang,
                "date": date,
                "status": status,
                "hash": h,
                "mtime": info["mtime"],
                "size": info["size"],
            })
            continue

        # Existing file — check if changed
        if abs(info["mtime"] - entry.get("original_mtime", 0)) < 0.001:
            unchanged += 1
            continue

        # mtime changed — check hash
        h = file_hash(info["abspath"])
        if h == entry.get("original_hash"):
            # Content unchanged, just update mtime in manifest
            unchanged += 1
            continue

        # Content changed
        lang = detect_language(info["abspath"])
        date = extract_date(info["abspath"], relpath)
        changed_files += 1
        status = "pending_translation" if lang == "hu" else "pending_index"
        pending.append({
            "relpath": relpath,
            "action": "changed",
            "language": lang,
            "date": date,
            "status": status,
            "hash": h,
            "mtime": info["mtime"],
            "size": info["size"],
        })

    # Check for deleted files
    deleted = []
    for relpath in manifest:
        if relpath not in current_files:
            deleted_files += 1
            deleted.append({"relpath": relpath, "action": "deleted"})

    stats = {
        "total_files": len(current_files),
        "unchanged": unchanged,
        "new": new_files,
        "changed": changed_files,
        "deleted": deleted_files,
        "pending_translation": sum(1 for p in pending if p["status"] == "pending_translation"),
        "pending_index": sum(1 for p in pending if p["status"] == "pending_index"),
        "manifest_entries": len(manifest),
        "indexed": sum(1 for e in manifest.values() if e.get("status") == "indexed"),
    }

    return pending, deleted, stats


def update_manifest_entry(config, relpath, info):
    """Update a single manifest entry (called after translation or indexing)."""
    manifest = load_manifest(config)
    if relpath in manifest:
        manifest[relpath].update(info)
    else:
        manifest[relpath] = info
    save_manifest(config, manifest)


def remove_manifest_entry(config, relpath):
    """Remove a manifest entry (for deleted files)."""
    manifest = load_manifest(config)
    if relpath in manifest:
        del manifest[relpath]
    save_manifest(config, manifest)


def main():
    parser = argparse.ArgumentParser(description="Knowledge Base Scanner")
    parser.add_argument("--pending", action="store_true", help="List files needing work")
    parser.add_argument("--status", action="store_true", help="Show index status summary")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--init-manifest", action="store_true",
                        help="Scan all files and create initial manifest (marks everything as pending)")
    args = parser.parse_args()

    config = load_config()

    if args.status:
        _, _, stats = compute_pending(config)
        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print(f"Knowledge Base Status:")
            print(f"  Total .md files:       {stats['total_files']}")
            print(f"  Indexed:               {stats['indexed']}")
            print(f"  Pending translation:   {stats['pending_translation']}")
            print(f"  Pending index:         {stats['pending_index']}")
            print(f"  New (untracked):       {stats['new']}")
            print(f"  Changed:               {stats['changed']}")
            print(f"  Deleted:               {stats['deleted']}")
            print(f"  Unchanged:             {stats['unchanged']}")
        return

    if args.pending or args.init_manifest:
        pending, deleted, stats = compute_pending(config)

        if args.init_manifest:
            # Write all pending entries to manifest as pending
            manifest = load_manifest(config)
            for p in pending:
                manifest[p["relpath"]] = {
                    "original_hash": p["hash"],
                    "original_mtime": p["mtime"],
                    "language": p["language"],
                    "date": p["date"],
                    "status": p["status"],
                    "size": p["size"],
                }
            save_manifest(config, manifest)
            print(f"Manifest initialized: {len(pending)} files tracked")
            return

        if args.json:
            output = {
                "pending": pending,
                "deleted": deleted,
                "stats": stats,
            }
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            # Human-readable
            if not pending and not deleted:
                print(f"Nothing pending. {stats['indexed']}/{stats['total_files']} files indexed.")
                return

            if pending:
                hu_pending = [p for p in pending if p["status"] == "pending_translation"]
                en_pending = [p for p in pending if p["status"] == "pending_index"]

                if hu_pending:
                    print(f"\n--- Pending translation ({len(hu_pending)} Hungarian files) ---")
                    for p in hu_pending:
                        tag = "NEW" if p["action"] == "new" else "CHANGED"
                        print(f"  [{tag}] {p['relpath']}")

                if en_pending:
                    print(f"\n--- Pending index ({len(en_pending)} English files) ---")
                    for p in en_pending:
                        tag = "NEW" if p["action"] == "new" else "CHANGED"
                        print(f"  [{tag}] {p['relpath']}")

            if deleted:
                print(f"\n--- Deleted ({len(deleted)} files) ---")
                for d in deleted:
                    print(f"  [DEL] {d['relpath']}")

            print(f"\nSummary: {stats['pending_translation']} need translation, "
                  f"{stats['pending_index']} need indexing, "
                  f"{stats['deleted']} deleted")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
