"""Tests for prokb.scanner — include/exclude, date extraction, manifest diffing."""
from prokb.config import DEFAULTS
from prokb.scanner import (
    scan_files, extract_date, compute_pending, save_manifest, load_manifest,
)


def _cfg(root, **scan_over):
    scan = dict(DEFAULTS["scan"])
    scan.update(scan_over)
    return {
        "project_root": str(root),
        "scan": scan,
        "paths": DEFAULTS["paths"],
        "translation": dict(DEFAULTS["translation"]),
    }


def _w(p, text="hello world this is content"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# ---- scan_files ----------------------------------------------------------

def test_scan_includes_root_markdown(tmp_path):
    _w(tmp_path / "readme.md")
    _w(tmp_path / "docs" / "guide.md")
    found = scan_files(_cfg(tmp_path))
    assert "readme.md" in found
    assert "docs/guide.md" in found


def test_scan_excludes_configured_dirs(tmp_path):
    _w(tmp_path / "node_modules" / "pkg.md")
    _w(tmp_path / ".git" / "x.md")
    _w(tmp_path / "venv" / "y.md")
    found = scan_files(_cfg(tmp_path))
    assert found == {} or all(
        not any(part in {"node_modules", ".git", "venv"} for part in rp.split("/"))
        for rp in found
    )


def test_scan_excludes_exclude_files(tmp_path):
    _w(tmp_path / "keep.md")
    _w(tmp_path / "manifest.json", "{}")  # in default exclude_files
    found = scan_files(_cfg(tmp_path))
    assert "keep.md" in found
    assert "manifest.json" not in found


def test_scan_respects_include_pattern(tmp_path):
    _w(tmp_path / "a.md")
    _w(tmp_path / "src" / "code.py", "print(1)")
    # Only **/*.md by default -> python file not picked up
    found_default = scan_files(_cfg(tmp_path))
    assert "src/code.py" not in found_default
    # Add a python include pattern -> now it is picked up
    found_py = scan_files(_cfg(tmp_path, include=["**/*.md", "src/**/*.py"]))
    assert "src/code.py" in found_py


# ---- extract_date --------------------------------------------------------

def test_extract_date_from_path():
    assert extract_date("/x/2026-06-27/file.md", "2026-06-27/file.md") == "2026-06-27"


def test_extract_date_from_filename_compact():
    assert extract_date("/x/20260627_summary.md", "20260627_summary.md") == "2026-06-27"


def test_extract_date_none_when_absent():
    assert extract_date("/x/notes.md", "notes.md") is None


# ---- compute_pending -----------------------------------------------------

def test_compute_pending_detects_new_files(tmp_path):
    _w(tmp_path / "a.md")
    _w(tmp_path / "b.md")
    pending, deleted, stats = compute_pending(_cfg(tmp_path))
    assert stats["new"] == 2
    assert stats["total_files"] == 2
    assert {p["relpath"] for p in pending} == {"a.md", "b.md"}


def test_compute_pending_unchanged_after_index(tmp_path):
    cfg = _cfg(tmp_path)
    f = tmp_path / "a.md"
    _w(f)
    pending, _, _ = compute_pending(cfg)
    entry = pending[0]
    # Simulate a manifest where the file is already indexed.
    save_manifest(cfg, {"a.md": {
        "original_hash": entry["hash"],
        "original_mtime": entry["mtime"],
        "status": "indexed",
    }})
    pending2, deleted2, stats2 = compute_pending(cfg)
    assert stats2["unchanged"] == 1
    assert pending2 == []


def test_compute_pending_detects_change(tmp_path):
    cfg = _cfg(tmp_path)
    f = tmp_path / "a.md"
    _w(f, "original content here")
    pending, _, _ = compute_pending(cfg)
    save_manifest(cfg, {"a.md": {
        "original_hash": pending[0]["hash"],
        "original_mtime": pending[0]["mtime"] - 100,  # force mtime mismatch
        "status": "indexed",
    }})
    f.write_text("completely different content now")
    pending2, _, stats2 = compute_pending(cfg)
    assert stats2["changed"] == 1
    assert pending2[0]["action"] == "changed"


def test_compute_pending_detects_deletion(tmp_path):
    cfg = _cfg(tmp_path)
    save_manifest(cfg, {"gone.md": {
        "original_hash": "abc", "original_mtime": 1.0, "status": "indexed",
    }})
    pending, deleted, stats = compute_pending(cfg)
    assert stats["deleted"] == 1
    assert deleted[0]["relpath"] == "gone.md"


def test_manifest_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    data = {"a.md": {"status": "indexed", "chunk_count": 3}}
    save_manifest(cfg, data)
    assert load_manifest(cfg) == data
