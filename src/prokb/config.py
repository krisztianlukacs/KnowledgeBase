"""Central config loader for prokb.

Searches for `knowledgebase.yaml` starting at cwd (or a provided path), then
walks up the directory tree until it finds one. The project_root is the
directory where the config lives.

Schema (v1):

    version: 1
    project_name: "my-project"
    collection_name: "my_project_knowledge"

    scan:
      include: ["**/*.md", "src/**/*.py"]
      exclude_dirs: [".git", "venv", "node_modules", "__pycache__"]
      exclude_files: ["knowledgebase.yaml"]

    embedding:
      model: "all-mpnet-base-v2"   # HuggingFace sentence-transformers model
      device: "cpu"                # cpu / cuda / mps

    translation:
      enabled: false
      source_language: "hu"        # ISO 639-1
      target_language: "en"
      translations_dir: "knowledge/translations"

    chunking:
      max_tokens: 1500
      min_tokens: 50

    paths:
      chroma_db: "knowledge/chroma_db"
      manifest: "knowledge/manifest.json"
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILENAME = "knowledgebase.yaml"

# Sane defaults per config section. Merged on top of user config.
DEFAULTS: dict[str, Any] = {
    "version": 1,
    "project_name": None,                    # must be set
    "collection_name": None,                 # must be set
    "scan": {
        "include": ["**/*.md"],
        "exclude_dirs": [
            ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
            "dist", "build", ".next", "target", ".cache", ".idea", ".vscode",
            ".ipynb_checkpoints", ".mypy_cache", ".ruff_cache", ".pytest_cache",
            "knowledge",   # don't self-index the chroma_db / manifest / translations
            ".mempalace", "mlruns", "data",
        ],
        "exclude_files": [CONFIG_FILENAME, "manifest.json"],
    },
    "embedding": {
        "model": "all-mpnet-base-v2",
        "device": "cpu",
    },
    "translation": {
        "enabled": False,
        "source_language": "hu",
        "target_language": "en",
        "translations_dir": "knowledge/translations",
    },
    "chunking": {
        "max_tokens": 1500,
        "min_tokens": 50,
    },
    "paths": {
        "chroma_db": "knowledge/chroma_db",
        "manifest": "knowledge/manifest.json",
    },
}


class ConfigError(RuntimeError):
    pass


def find_config(start: Path | None = None) -> Path:
    """Walk up the dir tree from `start` until we find `knowledgebase.yaml`.

    Raises ConfigError if not found by the filesystem root.
    """
    start = (start or Path.cwd()).resolve()
    cur = start
    while True:
        candidate = cur / CONFIG_FILENAME
        if candidate.exists():
            return candidate
        if cur.parent == cur:
            raise ConfigError(
                f"No {CONFIG_FILENAME} found in {start} or any parent directory. "
                f"Run `kb init` first."
            )
        cur = cur.parent


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load the config and merge over DEFAULTS. Sets project_root from file location.

    If `path` is None, auto-discover starting at cwd.
    """
    cfg_path = path or find_config()
    with open(cfg_path) as f:
        user_cfg = yaml.safe_load(f) or {}

    cfg = _deep_merge(DEFAULTS, user_cfg)

    # Inject project_root (absolute path to the directory containing the yaml)
    cfg["project_root"] = str(cfg_path.parent.resolve())
    cfg["_config_path"] = str(cfg_path.resolve())

    # Back-compat: ensure required names exist
    if not cfg.get("project_name"):
        cfg["project_name"] = cfg_path.parent.name
    if not cfg.get("collection_name"):
        safe = cfg["project_name"].replace("-", "_").replace(".", "_")
        cfg["collection_name"] = f"{safe}_knowledge"

    return cfg


def project_root(cfg: dict) -> Path:
    return Path(cfg["project_root"])


def chroma_db_path(cfg: dict) -> Path:
    return project_root(cfg) / cfg["paths"]["chroma_db"]


def manifest_path(cfg: dict) -> Path:
    return project_root(cfg) / cfg["paths"]["manifest"]


def translations_dir(cfg: dict) -> Path:
    return project_root(cfg) / cfg["translation"]["translations_dir"]


def write_default_config(dest: Path, project_name: str | None = None,
                        preset: str = "general") -> None:
    """Write a starter knowledgebase.yaml.

    preset: 'general' (English-dominant), 'multilingual' (HU+EN via e5),
    'hu-translate' (Claude-assisted HU→EN translation before indexing).
    """
    project_name = project_name or dest.parent.name
    safe = project_name.replace("-", "_").replace(".", "_")

    if preset == "multilingual":
        embedding_model = "intfloat/multilingual-e5-large"
        translation_enabled = False
    elif preset == "hu-translate":
        embedding_model = "all-mpnet-base-v2"
        translation_enabled = True
    else:  # general
        embedding_model = "all-mpnet-base-v2"
        translation_enabled = False

    body = f"""# prokb — knowledgebase.yaml (generated by `kb init`)
# See: https://github.com/krisztianlukacs/KnowledgeBase

version: 1
project_name: {project_name}
collection_name: {safe}_knowledge

scan:
  include:
    - "**/*.md"
    # - "src/**/*.py"
    # - "tools/**/*.py"
  exclude_dirs:
    - .git
    - venv
    - .venv
    - node_modules
    - __pycache__
    - knowledge            # self-directory must be excluded
    - mlruns
    - data
  exclude_files:
    - knowledgebase.yaml
    - manifest.json

embedding:
  model: {embedding_model}
  # Available models:
  #   all-mpnet-base-v2            (420 MB, 768-dim, English-strong)
  #   all-MiniLM-L6-v2             (90 MB, 384-dim, faster)
  #   intfloat/multilingual-e5-large (2 GB, 1024-dim, 50+ langs incl. Hungarian)
  #   BAAI/bge-large-en-v1.5       (1.3 GB, 1024-dim, SOTA English)
  device: cpu                      # cpu / cuda / mps

translation:
  enabled: {str(translation_enabled).lower()}
  # Set enabled: true to pre-translate source files via Claude Code
  # before indexing (useful if using an English-only embedding model
  # and your source files are in another language).
  source_language: hu              # ISO 639-1
  target_language: en
  translations_dir: knowledge/translations

chunking:
  max_tokens: 1500
  min_tokens: 50

paths:
  chroma_db: knowledge/chroma_db
  manifest: knowledge/manifest.json
"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body)
