"""CLI entry point for prokb.

Exposed as both `kb` and `prokb` via pyproject.toml [project.scripts].

Subcommands:
    init                  # create knowledgebase.yaml + knowledge/ in cwd
    update                # scan + (optional) translate + index
    query <text>          # semantic search
    status                # show index health
    mcp                   # run the MCP stdio server (called by .mcp.json)
    install-skills        # copy /knowledge-* skills into ~/.claude/skills
    install-mcp           # add or update the .mcp.json entry for this project
    doctor                # diagnose install + config + model cache
    diary <text>          # log a free-form note / session summary into the KB
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def _resolve_skills_src() -> Path:
    """Where the bundled skill templates live (inside the installed package)."""
    return Path(__file__).parent / "skills"


GITIGNORE_HEADER = "# kb (prokb) generated local index state, rebuilt by kb update"
GITIGNORE_LINES = [
    "knowledge/chroma_db/",
    "knowledge/manifest.json",
    "knowledge/.kb.lock",
]


def _ensure_gitignore(root: Path) -> list[str]:
    """Idempotently add the kb index-state lines to root/.gitignore.
    Returns the lines that were added."""
    gi = root / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    present = {ln.strip() for ln in existing.splitlines()}
    missing = [ln for ln in GITIGNORE_LINES if ln not in present]
    if not missing:
        return []
    block = []
    if existing and not existing.endswith("\n"):
        block.append("")
    if existing.strip():
        block.append("")
    if GITIGNORE_HEADER not in present:
        block.append(GITIGNORE_HEADER)
    block.extend(missing)
    gi.write_text(existing + "\n".join(block) + "\n")
    return missing


def cmd_init(args: argparse.Namespace) -> int:
    from .config import CONFIG_FILENAME, write_default_config

    dest = Path.cwd() / CONFIG_FILENAME
    if dest.exists() and not args.force:
        print(f"ERROR: {dest} already exists. Use --force to overwrite.",
              file=sys.stderr)
        return 1

    from .config import ConfigError

    preset = args.preset or "general"
    project_name = args.project_name or Path.cwd().name
    try:
        write_default_config(dest, project_name=project_name, preset=preset)
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Created {dest}")

    # Create knowledge/ directory structure
    (Path.cwd() / "knowledge").mkdir(exist_ok=True)
    print(f"Created {Path.cwd() / 'knowledge'}/")

    added = _ensure_gitignore(Path.cwd())
    if added:
        print(f"Added to .gitignore: {', '.join(added)}")

    # Offer to install skills if they're not yet in ~/.claude/skills
    skills_dst = Path.home() / ".claude" / "skills"
    has_query = (skills_dst / "knowledge-query" / "SKILL.md").exists()
    has_update = (skills_dst / "knowledge-update" / "SKILL.md").exists()
    if not (has_query and has_update):
        print()
        print("Tip: slash-command skills are not yet installed on this dev server.")
        print("     Run `kb install-skills` to install them (once per server).")
    return 0


def _scan_and_index(cfg, index_only: bool = False, batch=None) -> int:
    """Scan files → update manifest → index pending files. Shared by `kb update`
    and `kb diary --update`. Returns the number of files indexed this run.

    Holds the per-repo write lock for the whole scan+index (concurrent writers
    corrupt the HNSW index); raises LockHeldError if another writer is active."""
    from .lock import write_lock
    with write_lock(cfg):
        return _scan_and_index_locked(cfg, index_only, batch=batch)


def _scan_and_index_locked(cfg, index_only: bool = False, batch=None) -> int:
    from .indexer import run_incremental
    from .scanner import compute_pending, load_manifest, save_manifest

    # Step 1: scan files → update manifest
    if not index_only:
        pending, deleted, stats = compute_pending(cfg)
        manifest = load_manifest(cfg)

        # Apply new/changed files to manifest
        translation_enabled = cfg["translation"].get("enabled", False)

        for p in pending:
            # If translation is disabled, treat source-language files the same
            # as target-language — they go straight to pending_index.
            status = p["status"]
            if not translation_enabled and status == "pending_translation":
                status = "pending_index"
            manifest[p["relpath"]] = {
                "original_hash": p["hash"],
                "original_mtime": p["mtime"],
                "language": p["language"],
                "date": p["date"],
                "status": status,
                "size": p["size"],
                "source_mtime": p["mtime"],
            }

        # Mark deleted entries
        for d in deleted:
            if d["relpath"] in manifest:
                manifest[d["relpath"]]["status"] = "deleted"

        save_manifest(cfg, manifest)
        n_ready = sum(1 for e in manifest.values() if e.get("status") == "pending_index")
        n_need_trans = sum(1 for e in manifest.values() if e.get("status") == "pending_translation")
        print(f"Scanner: {len(pending)} new/changed, {len(deleted)} deleted, "
              f"{n_ready} ready to index, {n_need_trans} need translation",
              file=sys.stderr)

        if n_need_trans > 0 and translation_enabled:
            print(f"Tip: run the /knowledge-update skill (or equivalent AI agent) "
                  f"to translate the {n_need_trans} source-language files before "
                  f"they can be indexed.", file=sys.stderr)

    # Step 2: index all pending_index files
    return run_incremental(cfg, batch=batch)


def cmd_update(args: argparse.Namespace) -> int:
    from .config import load_config

    cfg = load_config()
    if args.batch < 1:
        print("ERROR: --batch must be positive", file=sys.stderr)
        return 2
    if args.status:
        return cmd_status(args)

    from .lock import LockHeldError
    try:
        _scan_and_index(cfg, index_only=args.index_only, batch=args.batch)
    except LockHeldError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    from .config import load_config
    from .query import query

    if args.top < 1:
        print("ERROR: --top must be positive", file=sys.stderr)
        return 2
    cfg = load_config()
    query(
        cfg,
        args.question,
        top_k=args.top,
        after=args.after,
        path_filter=args.path,
        full=args.full,
        as_json=args.json,
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from .config import load_config
    from .indexer import load_manifest, get_collection

    cfg = load_config()
    manifest = load_manifest(cfg)
    total = len(manifest)
    indexed = sum(1 for e in manifest.values() if e.get("status") == "indexed")
    pending_trans = sum(1 for e in manifest.values() if e.get("status") == "pending_translation")
    pending_idx = sum(1 for e in manifest.values() if e.get("status") == "pending_index")
    error_count = sum(1 for e in manifest.values() if e.get("status") == "error")
    total_chunks = sum(e.get("chunk_count", 0) for e in manifest.values())

    db_error = None
    try:
        coll = get_collection(cfg)
        chroma_n = coll.count()
    except Exception as e:
        chroma_n = -1
        db_error = type(e).__name__

    out = {
        "project_name": cfg["project_name"],
        "collection_name": cfg["collection_name"],
        "config_path": cfg["_config_path"],
        "manifest_entries": total,
        "indexed": indexed,
        "pending_translation": pending_trans,
        "pending_index": pending_idx,
        "errors": error_count,
        "manifest_chunks": total_chunks,
        "chromadb_chunks": chroma_n,
        "embedding_model": cfg["embedding"]["model"],
        "translation_enabled": cfg["translation"]["enabled"],
    }
    if db_error:
        out["error"] = f"Database unavailable: {db_error}"
    print(json.dumps(out, indent=2))
    return 1 if db_error else 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """Run the MCP stdio server. Called from .mcp.json."""
    from .mcp_server import main as mcp_main
    mcp_main()
    return 0


def cmd_install_skills(args: argparse.Namespace) -> int:
    src = _resolve_skills_src()
    dst = Path.home() / ".claude" / "skills"
    dst.mkdir(parents=True, exist_ok=True)

    installed = []
    for skill_dir in src.iterdir():
        if not skill_dir.is_dir():
            continue
        target = dst / skill_dir.name
        if target.exists() and not args.force:
            print(f"SKIP {target} (exists — use --force to overwrite)")
            continue
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill_dir, target)
        installed.append(target)
        print(f"OK   {target}")

    print(f"\nInstalled {len(installed)} skill(s) at {dst}")
    return 0


def cmd_install_mcp(args: argparse.Namespace) -> int:
    """Register this project's MCP server in .mcp.json (cwd)."""
    from .config import CONFIG_FILENAME

    mcp_json = Path.cwd() / ".mcp.json"
    data = {"mcpServers": {}}
    if mcp_json.exists():
        try:
            with open(mcp_json, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
                raise ValueError("Expected an object with an mcpServers object")
        except (OSError, ValueError) as exc:
            print(f"ERROR: cannot read valid .mcp.json ({type(exc).__name__}); file unchanged.",
                  file=sys.stderr)
            return 1
        data.setdefault("mcpServers", {})

    # Auto-derive project-knowledge as the MCP name
    yaml_path = Path.cwd() / CONFIG_FILENAME
    project_name = Path.cwd().name
    if yaml_path.exists():
        import yaml
        with open(yaml_path) as f:
            cfg = yaml.safe_load(f) or {}
        project_name = cfg.get("project_name", project_name)

    name = args.name or f"{project_name}-knowledge"

    data["mcpServers"][name] = {
        "type": "stdio",
        "command": "kb",
        "args": ["mcp"],
        "cwd": str(Path.cwd().resolve()),
    }
    with open(mcp_json, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote .mcp.json entry: {name}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run install/config diagnostics."""
    ok = True
    print("=== prokb doctor ===")
    # Config check
    try:
        from .config import load_config
        cfg = load_config()
        print(f"OK   config: {cfg['_config_path']}")
        print(f"     project_name:     {cfg['project_name']}")
        print(f"     collection_name:  {cfg['collection_name']}")
        print(f"     embedding.model:  {cfg['embedding']['model']}")
        print(f"     embedding.device: {cfg['embedding']['device']}")
        print(f"     translation.enabled: {cfg['translation']['enabled']}")
    except Exception as e:
        print(f"FAIL config: {e}")
        ok = False

    # Chromadb dep check
    try:
        import chromadb
        print(f"OK   chromadb version: {chromadb.__version__}")
    except Exception as e:
        print(f"FAIL chromadb: {e}")
        ok = False

    # Sentence transformers check
    try:
        import sentence_transformers
        print(f"OK   sentence-transformers: {sentence_transformers.__version__}")
    except Exception as e:
        print(f"FAIL sentence-transformers: {e}")
        ok = False

    # MCP server check
    try:
        import mcp
        print(f"OK   mcp: {mcp.__version__ if hasattr(mcp, '__version__') else 'installed'}")
    except Exception as e:
        print(f"FAIL mcp: {e}")
        ok = False

    # Skills check
    skills_dst = Path.home() / ".claude" / "skills"
    kq = skills_dst / "knowledge-query" / "SKILL.md"
    ku = skills_dst / "knowledge-update" / "SKILL.md"
    print(f"{'OK  ' if kq.exists() else 'WARN'} skill /knowledge-query:  {kq}")
    print(f"{'OK  ' if ku.exists() else 'WARN'} skill /knowledge-update: {ku}")

    # HuggingFace cache check (indirectly tells us if model is downloaded)
    hf_cache = Path.home() / ".cache" / "huggingface"
    if hf_cache.exists():
        n = sum(1 for _ in hf_cache.rglob("*") if _.is_file())
        print(f"OK   HF cache: {hf_cache} ({n} files)")
    else:
        print(f"WARN HF cache not found — model will download on first `kb update`")

    return 0 if ok else 1


def cmd_diary(args: argparse.Namespace) -> int:
    """Log a free-form note (e.g. session summary) as a knowledge-base entry."""
    from .config import load_config, project_root
    from datetime import datetime, timezone

    content_arg = getattr(args, "content", None)
    file_arg = getattr(args, "file", None)

    # Resolve the body BEFORE touching the filesystem, so a bad invocation
    # writes nothing and indexes nothing.
    if content_arg is not None and file_arg is not None:
        print("ERROR: give the note either as an argument or via --file, not both.",
              file=sys.stderr)
        return 2
    if file_arg is not None:
        try:
            body = Path(file_arg).read_text()
        except OSError as e:
            print(f"ERROR: cannot read --file {file_arg}: {e}", file=sys.stderr)
            return 2
    elif content_arg == "-":
        if sys.stdin.isatty():
            print("ERROR: `-` reads the note from stdin, but stdin is a terminal. "
                  "Pipe the text in (e.g. `... | kb diary -`) or use --file PATH.",
                  file=sys.stderr)
            return 2
        body = sys.stdin.read()
    elif content_arg is not None:
        body = content_arg
    else:
        print("ERROR: no note given. Pass the text, `-` (stdin) or --file PATH.",
              file=sys.stderr)
        return 2
    if not body.strip():
        print("ERROR: the diary note is empty (or whitespace only); nothing written.",
              file=sys.stderr)
        return 2

    cfg = load_config()
    root = project_root(cfg)
    diary_dir = root / "knowledge" / "diary"
    diary_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    title = args.title or "session-summary"
    safe_title = title.replace(" ", "_").replace("/", "_")
    fname = diary_dir / f"{stamp}_{safe_title}.md"
    header = f"""# Diary: {title}

- Date: {stamp} UTC
- Session: {args.session or 'ad-hoc'}
- Agent: {args.agent or 'human'}
- Tags: {args.tags or ''}

---

"""
    base = fname
    suffix = 1
    while True:
        try:
            with fname.open("x", encoding="utf-8") as stream:
                stream.write(header + body)
            break
        except FileExistsError:
            suffix += 1
            fname = base.with_name(f"{base.stem}-{suffix}{base.suffix}")
    print(f"Wrote {fname}")

    # --update indexes the new entry immediately (write + index in one call), so a
    # development summary lands in the searchable KB without a second command.
    if getattr(args, "update", False):
        from .lock import LockHeldError
        try:
            n = _scan_and_index(cfg)
        except LockHeldError as e:
            print(f"ERROR: {e}. The entry was written but NOT indexed; "
                  f"run `kb update` later.", file=sys.stderr)
            return 1
        print(f"Indexed ({n} file(s) processed).")
    else:
        print("Run `kb update` to index this entry into the knowledge base.")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="kb", description="Per-project knowledge base")
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    s_init = sub.add_parser("init", help="Create knowledgebase.yaml + knowledge/ in cwd")
    s_init.add_argument("--project-name", default=None)
    s_init.add_argument("--preset", choices=["general", "multilingual", "hu-translate"],
                        default="general")
    s_init.add_argument("--force", action="store_true")
    s_init.set_defaults(func=cmd_init)

    # update
    s_up = sub.add_parser("update", help="Scan + index (translation is skill's responsibility)")
    s_up.add_argument("--batch", type=int, default=20)
    s_up.add_argument("--index-only", action="store_true")
    s_up.add_argument("--status", action="store_true")
    s_up.set_defaults(func=cmd_update)

    # query
    s_q = sub.add_parser("query", help="Semantic search")
    s_q.add_argument("question")
    s_q.add_argument("--top", type=int, default=10)
    s_q.add_argument("--after", default=None)
    s_q.add_argument("--path", default=None)
    s_q.add_argument("--full", action="store_true")
    s_q.add_argument("--json", action="store_true")
    s_q.set_defaults(func=cmd_query)

    # status
    s_s = sub.add_parser("status", help="Index health JSON")
    s_s.set_defaults(func=cmd_status)

    # mcp
    s_m = sub.add_parser("mcp", help="Run MCP stdio server")
    s_m.set_defaults(func=cmd_mcp)

    # install-skills
    s_is = sub.add_parser("install-skills", help="Copy skills to ~/.claude/skills")
    s_is.add_argument("--force", action="store_true")
    s_is.set_defaults(func=cmd_install_skills)

    # install-mcp
    s_im = sub.add_parser("install-mcp", help="Add entry to .mcp.json in cwd")
    s_im.add_argument("--name", default=None)
    s_im.set_defaults(func=cmd_install_mcp)

    # doctor
    s_d = sub.add_parser("doctor", help="Diagnose install + config")
    s_d.set_defaults(func=cmd_doctor)

    # diary
    s_di = sub.add_parser("diary", help="Log a session summary / note")
    s_di.add_argument("content", nargs="?", default=None,
                      help="The summary/note content (markdown allowed); "
                           "`-` reads it from stdin")
    s_di.add_argument("--file", default=None, metavar="PATH",
                      help="Read the note body from PATH (instead of the positional text)")
    s_di.add_argument("--title", default=None)
    s_di.add_argument("--session", default=None)
    s_di.add_argument("--agent", default=None)
    s_di.add_argument("--tags", default=None)
    s_di.add_argument("--update", action="store_true",
                      help="Index the entry immediately (write + kb update in one call)")
    s_di.set_defaults(func=cmd_diary)

    args = p.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
