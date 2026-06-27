"""Tests for prokb.indexer.

Two layers:
  * Pure-logic tests for get_source_path (translation routing) — always run.
  * An opt-in end-to-end index+query roundtrip that needs the embedding model;
    skipped unless KB_RUN_INTEGRATION=1 so the default suite stays fast/offline.
"""
import os

import pytest

from prokb.config import DEFAULTS
from prokb.indexer import get_source_path


def _cfg(root, *, translation=False, source_lang="hu"):
    return {
        "project_root": str(root),
        "translation": {**DEFAULTS["translation"],
                        "enabled": translation, "source_language": source_lang},
        "paths": DEFAULTS["paths"],
    }


def test_get_source_path_uses_original_when_translation_off(tmp_path):
    (tmp_path / "a.md").write_text("hi")
    p = get_source_path(_cfg(tmp_path, translation=False), "a.md", {"language": "hu"})
    assert p == str(tmp_path / "a.md")


def test_get_source_path_uses_translation_when_enabled(tmp_path):
    tr = tmp_path / "knowledge" / "translations" / "a.md"
    tr.parent.mkdir(parents=True)
    tr.write_text("translated")
    p = get_source_path(_cfg(tmp_path, translation=True), "a.md", {"language": "hu"})
    assert p == str(tr)


def test_get_source_path_none_when_translation_missing(tmp_path):
    # Translation enabled + source-language file but no translated copy yet.
    p = get_source_path(_cfg(tmp_path, translation=True), "a.md", {"language": "hu"})
    assert p is None


def test_get_source_path_target_language_uses_original(tmp_path):
    # Even with translation enabled, an English (target) file uses the original.
    (tmp_path / "b.md").write_text("english")
    p = get_source_path(_cfg(tmp_path, translation=True), "b.md", {"language": "en"})
    assert p == str(tmp_path / "b.md")


# ---------------------------------------------------------------------------
# Opt-in integration test (needs sentence-transformers model + chromadb)
# ---------------------------------------------------------------------------

integration = pytest.mark.skipif(
    os.getenv("KB_RUN_INTEGRATION") != "1",
    reason="set KB_RUN_INTEGRATION=1 to run model-backed index+query roundtrip",
)


@integration
def test_index_and_query_roundtrip(tmp_path):
    import json
    from prokb.config import write_default_config, load_config, CONFIG_FILENAME
    from prokb.scanner import compute_pending, save_manifest, load_manifest
    from prokb.indexer import run_incremental, get_collection
    from prokb.query import query

    write_default_config(tmp_path / CONFIG_FILENAME, project_name="itest")
    cfg = load_config(tmp_path / CONFIG_FILENAME)

    (tmp_path / "knowledge" / "diary").mkdir(parents=True)
    (tmp_path / "knowledge" / "diary" / "2026-06-27_note.md").write_text(
        "# Diary\n\nThe fleet onboarding indexed 61 of 64 repositories successfully."
    )

    pending, deleted, _ = compute_pending(cfg)
    manifest = load_manifest(cfg)
    for p in pending:
        manifest[p["relpath"]] = {
            "original_hash": p["hash"], "original_mtime": p["mtime"],
            "language": "en", "date": p["date"], "status": "pending_index",
        }
    save_manifest(cfg, manifest)

    n = run_incremental(cfg)
    assert n == 1
    assert get_collection(cfg).count() >= 1

    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        query(cfg, "how many repositories were onboarded", top_k=3, as_json=True)
    out = json.loads(buf.getvalue())
    assert out["results"]
    assert "diary" in out["results"][0]["source_file"]
