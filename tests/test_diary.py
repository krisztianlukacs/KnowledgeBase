"""Tests for `kb diary` — file write path (model-free) and --update indexing (opt-in)."""
import argparse
import os
from pathlib import Path

import pytest

from prokb.cli import cmd_diary
from prokb.config import write_default_config, CONFIG_FILENAME


def _args(content, **over):
    ns = argparse.Namespace(content=content, title=None, session=None,
                            agent=None, tags=None, update=False)
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
