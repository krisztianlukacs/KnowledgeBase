"""Tests for `kb diary` — file write path (model-free) and --update indexing (opt-in)."""
import argparse
import io
import os
from pathlib import Path

import pytest

from prokb.cli import cmd_diary
from prokb.config import write_default_config, CONFIG_FILENAME


def _args(content, **over):
    ns = argparse.Namespace(content=content, title=None, session=None,
                            agent=None, tags=None, update=False, file=None)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _init(tmp_path):
    write_default_config(tmp_path / CONFIG_FILENAME, project_name="diarytest")
    (tmp_path / "knowledge").mkdir(exist_ok=True)


def test_diary_writes_entry_under_knowledge_diary(tmp_path, monkeypatch, capsys):
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    rc = cmd_diary(_args("a development summary line", title="dev-summary",
                         tags="dev-summary"))
    assert rc == 0
    diary = list((tmp_path / "knowledge" / "diary").glob("*.md"))
    assert len(diary) == 1
    body = diary[0].read_text()
    assert "a development summary line" in body
    assert "dev-summary" in body  # title + tags land in the header
    # Without --update it only writes; it tells the user to run kb update.
    assert "Run `kb update`" in capsys.readouterr().out


class _FakeStdin(io.StringIO):
    def __init__(self, text, tty=False):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


def _entries(tmp_path):
    d = tmp_path / "knowledge" / "diary"
    return list(d.glob("*.md")) if d.exists() else []


def test_diary_dash_reads_stdin(tmp_path, monkeypatch):
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", _FakeStdin("body from stdin, multi\nline"))
    assert cmd_diary(_args("-")) == 0
    (entry,) = _entries(tmp_path)
    text = entry.read_text()
    assert "body from stdin, multi\nline" in text
    assert not text.rstrip().endswith("---\n\n-")  # not the literal dash


@pytest.mark.parametrize("stdin_text", ["", "   \n\t\n"])
def test_diary_dash_empty_stdin_rejected(tmp_path, monkeypatch, capsys, stdin_text):
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", _FakeStdin(stdin_text))
    assert cmd_diary(_args("-")) != 0
    assert "empty" in capsys.readouterr().err
    assert _entries(tmp_path) == []


def test_diary_dash_tty_rejected(tmp_path, monkeypatch, capsys):
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", _FakeStdin("ignored", tty=True))
    assert cmd_diary(_args("-")) != 0
    assert "terminal" in capsys.readouterr().err
    assert _entries(tmp_path) == []


def test_diary_file_option(tmp_path, monkeypatch):
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "note-src.txt"
    src.write_text("note body from a file")
    assert cmd_diary(_args(None, file=str(src))) == 0
    (entry,) = _entries(tmp_path)
    assert "note body from a file" in entry.read_text()


def test_diary_file_and_content_mutually_exclusive(tmp_path, monkeypatch, capsys):
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "n.txt"
    src.write_text("x")
    assert cmd_diary(_args("text", file=str(src))) != 0
    assert _entries(tmp_path) == []


def test_diary_empty_file_and_missing_file_rejected(tmp_path, monkeypatch):
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n")
    assert cmd_diary(_args(None, file=str(empty))) != 0
    assert cmd_diary(_args(None, file=str(tmp_path / "nope.txt"))) != 0
    assert _entries(tmp_path) == []


@pytest.mark.parametrize("content", ["", "   \n  ", None])
def test_diary_whitespace_positional_rejected(tmp_path, monkeypatch, content):
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cmd_diary(_args(content)) != 0
    assert _entries(tmp_path) == []


def test_diary_update_fails_fast_when_lock_held(tmp_path, monkeypatch, capsys):
    import fcntl
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    with open(tmp_path / "knowledge" / ".kb.lock", "a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc = cmd_diary(_args("a note", update=True))
    assert rc != 0
    assert "another kb update is running" in capsys.readouterr().err


integration = pytest.mark.skipif(
    os.getenv("KB_RUN_INTEGRATION") != "1",
    reason="set KB_RUN_INTEGRATION=1 to run the model-backed --update path",
)


@integration
def test_diary_update_indexes_immediately(tmp_path, monkeypatch):
    from prokb.config import load_config
    from prokb.indexer import get_collection
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    rc = cmd_diary(_args(
        "Fixed the scanner so diary entries index; added the update flag.",
        title="dev-summary", update=True))
    assert rc == 0
    cfg = load_config(tmp_path / CONFIG_FILENAME)
    assert get_collection(cfg).count() >= 1
