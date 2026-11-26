"""Reproduction tests for bug-hunt claims V1..V12 (each asserts the CORRECT behaviour,
so each is expected to FAIL on the current code)."""
import argparse
import datetime as _dt
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import chromadb
import pytest
from chromadb import Documents, EmbeddingFunction, Embeddings

from prokb.config import CONFIG_FILENAME, load_config, write_default_config

SRC = str(Path(__file__).resolve().parent.parent / "src")


class FakeEF(EmbeddingFunction):
    def __init__(self):
        pass

    def __call__(self, input: Documents) -> Embeddings:
        out = []
        for text in input:
            v = [0.0] * 16
            for w in text.lower().split():
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 16] += 1.0
            if not any(v):
                v[0] = 1.0
            out.append(v)
        return out

    @staticmethod
    def name() -> str:
        return "fake-ef"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return FakeEF()


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    write_default_config(tmp_path / CONFIG_FILENAME, project_name="bhtest")
    c = load_config(tmp_path / CONFIG_FILENAME)
    monkeypatch.setattr("prokb.indexer.get_embedding_function", lambda c: FakeEF())
    return c


def _chunks_for(cfg, relpath):
    from prokb.indexer import get_collection
    return get_collection(cfg).get(where={"source_file": relpath})["ids"]


# ---------------------------------------------------------------- V1
def test_v1_diary_same_title_same_second_keeps_both(tmp_path, monkeypatch):
    from prokb.cli import cmd_diary
    write_default_config(tmp_path / CONFIG_FILENAME, project_name="diarytest")
    monkeypatch.chdir(tmp_path)

    class Frozen(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(_dt, "datetime", Frozen)
    for body in ("first entry", "second entry"):
        ns = argparse.Namespace(content=body, title="dev-summary", session=None,
                                agent=None, tags=None, update=False, file=None)
        assert cmd_diary(ns) == 0
    texts = [p.read_text() for p in (tmp_path / "knowledge" / "diary").glob("*.md")]
    assert any("first entry" in t for t in texts), "first diary entry was overwritten"
    assert any("second entry" in t for t in texts)


# ---------------------------------------------------------------- V2
def test_v2_configured_chunk_limits_are_honoured(cfg, tmp_path):
    from prokb.indexer import index_file

    class Rec:
        def __init__(self):
            self.docs = []

        def get(self, **kw):
            return {"ids": []}

        def delete(self, **kw):
            pass

        def add(self, ids, documents, metadatas):
            self.docs.extend(documents)

    cfg["chunking"] = {"max_tokens": 50, "min_tokens": 1}
    paras = "\n\n".join(("word%d " % i) * 20 for i in range(10))  # ~100 chars each
    (tmp_path / "a.md").write_text("## Sec\n\n" + paras)
    rec = Rec()
    n, res = index_file(rec, cfg, "a.md", {"original_hash": "abcdef0123", "language": "en"})
    assert res == "ok"
    assert n > 1, "chunking.max_tokens=50 ignored: whole section is one chunk"


# ---------------------------------------------------------------- V3
def test_v3_german_source_language_gets_pending_translation(cfg, tmp_path):
    from prokb.scanner import compute_pending
    cfg["translation"] = {**cfg["translation"], "enabled": True,
                          "source_language": "de", "target_language": "en"}
    (tmp_path / "de.md").write_text(
        "# Anleitung\n\nDies ist ein ausführlicher deutscher Text über die Konfiguration "
        "des Systems. Der Benutzer muss die Datei zuerst öffnen und dann die Werte "
        "ändern, bevor der Dienst neu gestartet wird. Außerdem sollte man die "
        "Dokumentation lesen, weil nicht alle Einstellungen selbstverständlich sind.\n")
    pending, _, _ = compute_pending(cfg)
    entry = next(p for p in pending if p["relpath"] == "de.md")
    assert entry["language"] == "de" and entry["status"] == "pending_translation", entry


# ---------------------------------------------------------------- V4
def test_v4a_index_file_does_not_duplicate_when_old_chunk_delete_fails(cfg, tmp_path):
    from prokb.indexer import get_collection, index_file

    real = get_collection(cfg)

    class FailingDelete:
        def get(self, **kw):
            return real.get(**kw)

        def delete(self, **kw):
            raise RuntimeError("chroma delete failed")

        def add(self, **kw):
            return real.add(**kw)

    (tmp_path / "a.md").write_text("## S\n\n" + "alpha beta gamma delta " * 30)
    index_file(real, cfg, "a.md", {"original_hash": "11111111aaaa", "language": "en"})
    first = len(real.get(where={"source_file": "a.md"})["ids"])
    (tmp_path / "a.md").write_text("## S\n\n" + "epsilon zeta eta theta " * 30)
    try:
        index_file(FailingDelete(), cfg, "a.md",
                   {"original_hash": "22222222bbbb", "language": "en"})
    except Exception:
        pass  # failing loudly is acceptable; silently duplicating is not
    after = len(real.get(where={"source_file": "a.md"})["ids"])
    assert after <= first, f"old+new chunks coexist: {first} -> {after}"


def test_v4b_manifest_entry_kept_when_chunk_delete_fails(cfg, monkeypatch):
    from prokb import indexer

    class Coll:
        def get(self, **kw):
            return {"ids": ["x1", "x2"]}

        def delete(self, **kw):
            raise RuntimeError("chroma delete failed")

    monkeypatch.setattr(indexer, "get_collection", lambda c: Coll())
    indexer.save_manifest(cfg, {"gone.md": {"status": "deleted", "chunk_count": 2}})
    try:
        indexer._run_incremental(cfg)
    except Exception:
        pass
    assert "gone.md" in indexer.load_manifest(cfg), \
        "manifest entry dropped although its chunks could not be deleted (orphans)"


# ---------------------------------------------------------------- V5
def test_v5_emptied_file_old_chunks_removed(cfg, tmp_path):
    from prokb.cli import _scan_and_index
    f = tmp_path / "a.md"
    f.write_text("## S\n\n" + "alpha beta gamma delta unique content " * 20)
    _scan_and_index(cfg)
    assert _chunks_for(cfg, "a.md"), "precondition: file indexed"
    f.write_text("   \n")
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 100))
    _scan_and_index(cfg)
    assert _chunks_for(cfg, "a.md") == [], "stale content of emptied file still searchable"


# ---------------------------------------------------------------- V6
def test_v6_manifest_survives_ascii_locale_with_non_ascii_filename(tmp_path):
    code = (
        "import sys\n"
        "from prokb.scanner import save_manifest\n"
        "cfg={'project_root': sys.argv[1], 'paths': {'manifest': 'knowledge/manifest.json'}}\n"
        "save_manifest(cfg, {'\\u00e1rv\\u00edzt\\u0171r\\u0151.md': {'status': 'indexed'}})\n"
    )
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("LC_") and k not in ("LANG", "PYTHONUTF8", "PYTHONIOENCODING")}
    env.update(LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", PYTHONPATH=SRC)
    r = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=env,
                       capture_output=True, text=True)
    mf = tmp_path / "knowledge" / "manifest.json"
    ok = r.returncode == 0
    if ok:
        json.loads(mf.read_text(encoding="utf-8"))
    assert ok, f"save_manifest crashed in C locale (manifest left at {mf.stat().st_size if mf.exists() else None} bytes): {r.stderr.strip().splitlines()[-1]}"


# ---------------------------------------------------------------- V7
def test_v7_init_manifest_respects_write_lock(cfg, tmp_path, monkeypatch):
    from prokb import scanner
    from prokb.lock import LockHeldError
    (tmp_path / "a.md").write_text("# hello world\n\nsome English content here for the test.\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["scanner", "--init-manifest"])
    (tmp_path / "knowledge").mkdir(exist_ok=True)
    with open(tmp_path / "knowledge" / ".kb.lock", "a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            scanner.main()
        except (LockHeldError, SystemExit):
            pass
    assert not (tmp_path / "knowledge" / "manifest.json").exists(), \
        "--init-manifest wrote the manifest while another process held the write lock"


# ---------------------------------------------------------------- V8
def test_v8_full_rebuild_does_not_keep_stale_indexed_state(cfg, tmp_path):
    from prokb import indexer
    (tmp_path / "keep.md").write_text("## S\n\n" + "alpha beta gamma delta " * 30)
    indexer.save_manifest(cfg, {
        "keep.md": {"status": "pending_index", "original_hash": "aaaaaaaa11", "language": "en"},
        "missing.md": {"status": "indexed", "chunk_count": 3, "original_hash": "bbbbbbbb22",
                       "language": "en"},
    })
    indexer._run_full(cfg)
    e = indexer.load_manifest(cfg)["missing.md"]
    assert not (e["status"] == "indexed" and e.get("chunk_count", 0) > 0), \
        f"manifest claims 3 indexed chunks for a file with none in the (rebuilt) collection: {e}"


# ---------------------------------------------------------------- V9
def test_v9_symlink_outside_project_not_indexed(tmp_path):
    from prokb.scanner import scan_files
    proj = tmp_path / "proj"
    proj.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("top secret outside content")
    (proj / "link.md").symlink_to(outside / "secret.md")
    (proj / "real.md").write_text("real")
    write_default_config(proj / CONFIG_FILENAME, project_name="symtest")
    c = load_config(proj / CONFIG_FILENAME)
    assert "link.md" not in scan_files(c), "symlink target outside project_root is scanned"


# ---------------------------------------------------------------- V12
def test_v12_content_change_with_preserved_mtime_is_detected(cfg, tmp_path):
    from prokb.scanner import compute_pending
    f = tmp_path / "a.md"
    f.write_text("# one\n\noriginal content that is fairly long and english.\n")
    pending, _, _ = compute_pending(cfg)
    p = next(x for x in pending if x["relpath"] == "a.md")
    from prokb.scanner import save_manifest
    save_manifest(cfg, {"a.md": {"original_hash": p["hash"], "original_mtime": p["mtime"],
                                 "size": p["size"], "status": "indexed", "language": "en"}})
    st = f.stat()
    f.write_text("# one\n\nCOMPLETELY different and notably longer replacement content now.\n")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))  # cp -p / rsync -a style
    pending, _, _ = compute_pending(cfg)
    assert any(x["relpath"] == "a.md" and x["action"] == "changed" for x in pending), \
        "changed content (different size/hash) not detected because mtime matched"
