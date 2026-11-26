"""Tests for the per-repo exclusive write lock (concurrent writers corrupt HNSW)."""
import argparse
import fcntl

import pytest

from prokb.cli import cmd_update
from prokb.config import CONFIG_FILENAME, load_config, write_default_config
from prokb.lock import LockHeldError, write_lock
from prokb.indexer import run_full, run_incremental


@pytest.fixture
def cfg(tmp_path):
    write_default_config(tmp_path / CONFIG_FILENAME, project_name="locktest")
    return load_config(tmp_path / CONFIG_FILENAME)


def _hold(cfg):
    from pathlib import Path
    p = Path(cfg["project_root"]) / "knowledge" / ".kb.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = open(p, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        fh.close()
        raise
    return fh


def test_write_lock_fails_fast_when_held_and_succeeds_after_release(cfg):
    fh = _hold(cfg)
    with pytest.raises(LockHeldError, match="another kb update is running"):
        with write_lock(cfg):
            pytest.fail("must not enter while the lock is held")
    fh.close()
    with write_lock(cfg):
        pass  # released -> acquirable


def test_write_lock_is_reentrant_in_process(cfg):
    with write_lock(cfg):
        with write_lock(cfg):
            pass
        # still held after the inner exit
        with pytest.raises(BlockingIOError):
            other = _hold(cfg)
            other.close()


@pytest.mark.parametrize("fn", [run_incremental, run_full])
def test_indexer_write_paths_take_the_lock(cfg, fn):
    fh = _hold(cfg)
    try:
        with pytest.raises(LockHeldError):
            fn(cfg)
    finally:
        fh.close()


def test_kb_update_fails_fast_then_works_after_release(cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(status=False, index_only=False, batch=20)
    fh = _hold(cfg)
    assert cmd_update(args) == 1
    assert "another kb update is running" in capsys.readouterr().err
    fh.close()
    # Empty project: scan finds nothing; run_incremental builds the collection,
    # which needs no model when there is nothing to embed.
    monkeypatch.setattr("prokb.indexer.get_embedding_function", lambda c: None)
    assert cmd_update(args) == 0


def test_lockfile_is_not_indexed(cfg, tmp_path):
    from prokb.scanner import scan_files
    (tmp_path / "knowledge").mkdir(exist_ok=True)
    (tmp_path / "knowledge" / ".kb.lock").write_text("")
    assert not any(".kb.lock" in rp for rp in scan_files(cfg))
