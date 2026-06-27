"""Regression test: kb diary entries (knowledge/diary/) must be scanned/indexed.

Bug (fixed in 0.1.1): the default scan config excludes the whole `knowledge/`
directory (it holds chroma_db / manifest / translations), which also swallowed
`knowledge/diary/*.md` — so `kb diary` entries were never indexed. The scanner
now whitelists knowledge/diary/ explicitly.

These tests exercise scanner.scan_files directly with a hand-built config, so
they need no embedding model / chromadb — fast + deterministic.
"""
from pathlib import Path

from prokb.config import DEFAULTS
from prokb.scanner import scan_files


def _make_config(root: Path) -> dict:
    return {
        "project_root": str(root),
        "scan": DEFAULTS["scan"],  # the real default exclude list (includes "knowledge")
    }


def _write(p: Path, text: str = "content") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_diary_entries_are_scanned(tmp_path):
    _write(tmp_path / "knowledge" / "diary" / "2026-06-27_000000_session.md",
           "# Diary\n\nFleet onboarding summary.")
    found = scan_files(_make_config(tmp_path))
    assert "knowledge/diary/2026-06-27_000000_session.md" in found


def test_chroma_db_and_translations_still_excluded(tmp_path):
    # Artifact dirs under knowledge/ must stay excluded even though diary is allowed.
    _write(tmp_path / "knowledge" / "chroma_db" / "stray.md")
    _write(tmp_path / "knowledge" / "translations" / "copy.md")
    found = scan_files(_make_config(tmp_path))
    assert all(not rp.startswith("knowledge/chroma_db/") for rp in found)
    assert all(not rp.startswith("knowledge/translations/") for rp in found)


def test_root_markdown_still_scanned(tmp_path):
    _write(tmp_path / "report-2026-06-27.md")
    found = scan_files(_make_config(tmp_path))
    assert "report-2026-06-27.md" in found
