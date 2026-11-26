"""Regression tests for `kb query --path` (chromadb 1.5 `$contains` no-op).

Uses a real chromadb collection with a cheap deterministic embedding function,
so no model download is needed.
"""
import hashlib
import io
import json
from contextlib import redirect_stdout

import chromadb
import pytest
from chromadb import Documents, EmbeddingFunction, Embeddings

from prokb.config import CONFIG_FILENAME, load_config, write_default_config


class FakeEF(EmbeddingFunction):
    """Deterministic 16-dim bag-of-words hash embedding."""

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
    write_default_config(tmp_path / CONFIG_FILENAME, project_name="qtest")
    cfg = load_config(tmp_path / CONFIG_FILENAME)
    monkeypatch.setattr("prokb.indexer.get_embedding_function", lambda c: FakeEF())
    return cfg


def _fill(cfg, rows):
    from prokb.indexer import get_collection
    coll = get_collection(cfg)
    coll.add(
        ids=[f"id{i}" for i in range(len(rows))],
        documents=[r[1] for r in rows],
        metadatas=[{"source_file": r[0], "section": "s", "date": "2026-01-01",
                    "language": "en", "chunk_index": 0,
                    "content_preview": r[1][:50]} for r in rows],
    )


def _run(cfg, question, **kw):
    from prokb.query import query
    buf = io.StringIO()
    with redirect_stdout(buf):
        query(cfg, question, as_json=True, **kw)
    return json.loads(buf.getvalue())


def test_path_filter_returns_substring_match(cfg):
    _fill(cfg, [
        ("knowledge/diary/2026-01-01_note.md", "fleet onboarding finished today"),
        ("docs/guide.md", "fleet onboarding guide for newcomers"),
    ])
    out = _run(cfg, "fleet onboarding", top_k=5, path_filter="knowledge/diary")
    assert [r["source_file"] for r in out["results"]] == [
        "knowledge/diary/2026-01-01_note.md"]


def test_path_filter_finds_match_beyond_top_k(cfg):
    # 30 closer non-matching chunks + 1 distant diary chunk: a naive
    # n_results=top_k query would never see the diary chunk.
    rows = [(f"docs/d{i}.md", "alpha beta gamma delta") for i in range(30)]
    rows.append(("knowledge/diary/x.md", "completely unrelated zebra words here"))
    _fill(cfg, rows)
    out = _run(cfg, "alpha beta gamma delta", top_k=3, path_filter="knowledge/diary")
    assert [r["source_file"] for r in out["results"]] == ["knowledge/diary/x.md"]


def test_path_filter_finds_match_ranked_beyond_100(cfg):
    # Distinct vectors (identical duplicates make HNSW drop an outlier, which
    # would be a test-data artefact, not the behaviour under test).
    rows = [(f"docs/d{i}.md", f"alpha beta gamma delta tok{i}") for i in range(150)]
    rows.append(("knowledge/diary/far.md", "completely unrelated zebra words here"))
    _fill(cfg, rows)
    out = _run(cfg, "alpha beta gamma delta", top_k=3, path_filter="knowledge/diary")
    assert [r["source_file"] for r in out["results"]] == ["knowledge/diary/far.md"]


def test_path_filter_exact_with_identical_chunks(cfg):
    # 150 identical texts/vectors + one distinct diary chunk. HNSW approximate
    # search can drop the outlier; the pre-filtered query must not.
    rows = [(f"docs/d{i}.md", "alpha beta gamma delta") for i in range(150)]
    rows.append(("knowledge/diary/far.md", "completely unrelated zebra words here"))
    _fill(cfg, rows)
    out = _run(cfg, "alpha beta gamma delta", top_k=3, path_filter="knowledge/diary")
    assert [r["source_file"] for r in out["results"]] == ["knowledge/diary/far.md"]


def test_no_match_filter_keeps_json_shape(cfg):
    _fill(cfg, [("docs/a.md", "alpha beta")])
    out = _run(cfg, "alpha", top_k=3, path_filter="nope/")
    assert out["results"] == [] and out["total_chunks"] == 1


def test_after_filter_returns_only_later_chunks_without_error(cfg):
    _fill(cfg, [
        ("docs/old.md", "alpha beta old"),
        ("docs/new.md", "alpha beta new"),
    ])
    # give the chunks different dates
    from prokb.indexer import get_collection
    coll = get_collection(cfg)
    coll.update(ids=["id0"], metadatas=[{"source_file": "docs/old.md", "section": "s",
                "date": "2026-01-01", "language": "en", "chunk_index": 0,
                "content_preview": "old"}])
    coll.update(ids=["id1"], metadatas=[{"source_file": "docs/new.md", "section": "s",
                "date": "2026-07-01", "language": "en", "chunk_index": 0,
                "content_preview": "new"}])
    out = _run(cfg, "alpha beta", top_k=5, after="2026-06-01")
    assert [r["source_file"] for r in out["results"]] == ["docs/new.md"]


def test_top_k_truncates_after_filtering(cfg):
    rows = [(f"knowledge/diary/{i}.md", f"alpha beta note{i}") for i in range(8)]
    rows += [(f"docs/d{i}.md", "alpha beta") for i in range(8)]
    _fill(cfg, rows)
    out = _run(cfg, "alpha beta", top_k=4, path_filter="knowledge/diary")
    assert len(out["results"]) == 4
    assert all("knowledge/diary" in r["source_file"] for r in out["results"])
