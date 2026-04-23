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


def cmd_init(args: argparse.Namespace) -> int:
    from .config import CONFIG_FILENAME, write_default_config

    dest = Path.cwd() / CONFIG_FILENAME
    if dest.exists() and not args.force:
        print(f"ERROR: {dest} already exists. Use --force to overwrite.",
              file=sys.stderr)
        return 1

    preset = args.preset or "general"
    project_name = args.project_name or Path.cwd().name
    write_default_config(dest, project_name=project_name, preset=preset)
    print(f"Created {dest}")

    # Create knowledge/ directory structure
    (Path.cwd() / "knowledge").mkdir(exist_ok=True)
    print(f"Created {Path.cwd() / 'knowledge'}/")

    # Offer to install skills if they're not yet in ~/.claude/skills
    skills_dst = Path.home() / ".claude" / "skills"
    has_query = (skills_dst / "knowledge-query" / "SKILL.md").exists()
    has_update = (skills_dst / "knowledge-update" / "SKILL.md").exists()
    if not (has_query and has_update):
        print()
        print("Tip: slash-command skills are not yet installed on this dev server.")
        print("     Run `kb install-skills` to install them (once per server).")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    from .config import load_config
    from .indexer import run_incremental
    from .scanner import compute_pending, load_manifest, save_manifest

    cfg = load_config()
    if args.status:
        return cmd_status(args)

    # Step 1: scan files → update manifest
    if not args.index_only:
        pending, deleted, stats = compute_pending(cfg)
        manifest = load_manifest(cfg)

        # Apply new/changed files to manifest
        translation_enabled = cfg["translation"].get("enabled", False)
        source_lang = cfg["translation"]["source_language"]

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
    run_incremental(cfg)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    from .config import load_config
    from .query import query

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

    try:
        coll = get_collection(cfg)
        chroma_n = coll.count()
    except Exception as e:
        chroma_n = -1

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
    print(json.dumps(out, indent=2))
    return 0


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
        with open(mcp_json) as f:
            data = json.load(f)
        if "mcpServers" not in data:
            data["mcpServers"] = {}

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
    fname.write_text(header + args.content)
    print(f"Wrote {fname}")
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
    s_di.add_argument("content", help="The summary/note content (markdown allowed)")
    s_di.add_argument("--title", default=None)
    s_di.add_argument("--session", default=None)
    s_di.add_argument("--agent", default=None)
    s_di.add_argument("--tags", default=None)
    s_di.set_defaults(func=cmd_diary)

    args = p.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
