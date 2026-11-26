"""Independent bug-hunt reproductions; assert intended behaviour, not current bugs.

Not executed by the delegated author. No embedding downloads or network calls.
"""
import copy
import importlib.util
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from prokb import chunker, cli, config, indexer, query, scanner
from prokb.lock import LockHeldError, write_lock


@pytest.fixture
def cfg(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    result = copy.deepcopy(config.DEFAULTS)
    result.update(project_root=str(root), project_name="repro",
                  collection_name="repro_knowledge",
                  _config_path=str(root / "knowledgebase.yaml"))
    return result


@pytest.fixture
def collection(monkeypatch):
    coll = Mock()
    coll.get.return_value = {"ids": []}
    coll.count.return_value = 1
    coll.query.return_value = {
        "ids": [["one"]], "documents": [["body"]],
        "metadatas": [[{"source_file": "a.md"}]], "distances": [[0.0]],
    }
    monkeypatch.setattr(indexer, "get_collection", lambda cfg: coll)
    # Fail closed if a test unexpectedly reaches model/client construction.
    monkeypatch.setattr(indexer, "get_embedding_function",
                        Mock(side_effect=AssertionError("unexpected embedding call")))
    monkeypatch.setattr(indexer, "get_chroma_client",
                        Mock(side_effect=AssertionError("unexpected client call")))
    return coll


@pytest.fixture
def server(cfg, monkeypatch):
    # Execute a fresh module with an explicitly supplied tmp config, avoiding
    # the server's import-time config discovery and cross-test module state.
    monkeypatch.setattr(indexer, "load_config", lambda: cfg)
    spec = importlib.util.spec_from_file_location(
        "prokb._bughunt_mcp", Path(indexer.__file__).with_name("mcp_server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tracked(cfg, name="a.md", status="indexed", mtime_offset=0):
    path = Path(cfg["project_root"]) / name
    path.write_text("A short English document.")
    entry = {"status": status, "language": "hu" if status == "pending_translation" else "en",
             "original_hash": scanner.file_hash(path),
             "original_mtime": path.stat().st_mtime + mtime_offset}
    scanner.save_manifest(cfg, {name: entry})
    return path


def test_v10_include_escapes_root(cfg, tmp_path):
    (tmp_path / "outside.md").write_text("outside project")
    cfg["scan"]["include"] = ["../**/*.md"]
    try:
        found = scanner.scan_files(cfg)
    except (config.ConfigError, ValueError):
        return  # Explicitly rejecting the escaping pattern is also safe.
    root = Path(cfg["project_root"]).resolve()
    assert all(Path(info["abspath"]).resolve().is_relative_to(root)
               for info in found.values())


def test_v11_deleted_translation_cannot_escape(cfg, collection):
    cfg["translation"]["enabled"] = True
    translations = config.translations_dir(cfg)
    translations.mkdir(parents=True)
    victim = translations.parent / "outside.md"
    victim.write_text("must survive")
    scanner.save_manifest(cfg, {"../outside.md": {"status": "deleted"}})
    try:
        indexer.run_incremental(cfg)
    except (config.ConfigError, ValueError):
        pass
    assert victim.exists(), "Deleting a translation must stay within translations_dir"


def test_v13_disabled_translation_resumes_pending(cfg, collection):
    tracked(cfg, status="pending_translation")
    assert cli._scan_and_index(cfg) == 1
    assert scanner.load_manifest(cfg)["a.md"]["status"] == "indexed"


def test_v14_long_paragraph_respects_max_tokens():
    chunks = chunker.chunk_markdown("word " * 100, max_tokens=20, min_tokens=0)
    assert chunks
    assert all(chunker.estimate_tokens(c["content"]) <= 20 for c in chunks)


def test_v15_split_preserves_code_fences():
    text = "```python\n" + "x = 1\n" * 10 + "\n\n" + "y = 2\n" * 10 + "```"
    chunks = chunker.chunk_markdown(text, max_tokens=20, min_tokens=0)
    assert all(c["content"].count("```") % 2 == 0 for c in chunks)


def test_v16_multisegment_exclude(cfg):
    path = Path(cfg["project_root"]) / "docs/archive/a.md"
    path.parent.mkdir(parents=True)
    path.write_text("archived")
    cfg["scan"]["exclude_dirs"] = ["docs/archive"]
    assert "docs/archive/a.md" not in scanner.scan_files(cfg)


def test_v17_project_name_yaml_roundtrip(tmp_path):
    dest = tmp_path / "knowledgebase.yaml"
    config.write_default_config(dest, project_name="api # old")
    assert config.load_config(dest)["project_name"] == "api # old"


def test_v18_empty_section_uses_defaults(tmp_path):
    dest = tmp_path / "knowledgebase.yaml"
    dest.write_text("project_name: example\nscan:\n  # include: [\"**/*.md\"]\n")
    loaded = config.load_config(dest)
    assert loaded["scan"] == config.DEFAULTS["scan"]


def test_v19_concurrent_search_output_is_isolated(server, monkeypatch):
    a_entered, b_entered, a_printed = Event(), Event(), Event()

    def raw(cfg, question, **kwargs):
        if question == "a":
            a_entered.set()
            assert b_entered.wait(5)
            print(json.dumps({"query": "a"}))
            a_printed.set()
        else:
            b_entered.set()
            assert a_printed.wait(5)
            # Wait until A has exited its redirect before printing B.
            assert a_done.wait(5)
            print(json.dumps({"query": "b"}))

    a_done = Event()
    monkeypatch.setattr(server, "_raw_query", raw)

    def run_a():
        try:
            return server.knowledge_search("a")
        finally:
            a_done.set()

    original_stdout = sys.stdout
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(run_a)
            assert a_entered.wait(5)
            b = pool.submit(server.knowledge_search, "b")
            outputs = [a.result(timeout=10), b.result(timeout=10)]
    finally:
        sys.stdout = original_stdout  # Bad nested redirects must not poison pytest.
    assert [json.loads(value) for value in outputs] == [{"query": "a"}, {"query": "b"}]


def test_v20_reject_invalid_date(tmp_path):
    path = tmp_path / "report-12345678.md"
    assert scanner.extract_date(path, path.name) is None


def test_v21_update_batch_limits_indexing(cfg, collection, monkeypatch):
    root = Path(cfg["project_root"])
    for name in ("a.md", "b.md"):
        (root / name).write_text("English content")
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    monkeypatch.setattr(scanner, "detect_language", lambda path: "en")
    args = SimpleNamespace(status=False, index_only=False, batch=1)
    assert cli.cmd_update(args) == 0
    manifest = scanner.load_manifest(cfg)
    assert sum(e["status"] == "indexed" for e in manifest.values()) == 1


def test_v22_lock_excludes_other_thread(cfg):
    def attempt():
        try:
            with write_lock(cfg):
                return "entered"
        except LockHeldError:
            return "blocked"

    with write_lock(cfg):
        with ThreadPoolExecutor(max_workers=1) as pool:
            outcome = pool.submit(attempt).result(timeout=5)
    assert outcome == "blocked"


def test_v24_unchanged_hash_refreshes_mtime(cfg, collection, monkeypatch):
    tracked(cfg, mtime_offset=-10)
    real_hash = scanner.file_hash
    spy = Mock(wraps=real_hash)
    monkeypatch.setattr(scanner, "file_hash", spy)
    cli._scan_and_index(cfg)
    assert spy.call_count == 1
    spy.reset_mock()
    cli._scan_and_index(cfg)
    assert spy.call_count == 0, "An unchanged touched file must not be rehashed every update"


def test_v25_empty_header_preserves_body():
    sections = chunker.split_by_headers("##\nFirst body line\nSecond body line")
    assert "First body line" in "\n".join(s["content"] for s in sections)


def test_v26_literal_placeholder_preserved():
    original = "Literal __CODEBLOCK_0__\n\n```python\nx = 1\n```"
    protected, blocks = chunker.extract_code_blocks(original)
    assert chunker.restore_code_blocks(protected, blocks) == original


def test_v27_null_metadata_query(cfg, collection, capsys):
    collection.query.return_value["metadatas"] = [[None]]
    query.query(cfg, "body", as_json=True)
    result = json.loads(capsys.readouterr().out)
    assert len(result["results"]) == 1
    assert result["results"][0]["source_file"] == ""


def test_v28_source_count_before_limit(server, monkeypatch):
    monkeypatch.setattr(server, "load_manifest", lambda cfg: {
        "a.md": {"status": "indexed"}, "b.md": {"status": "indexed"}})
    result = json.loads(server.knowledge_list_sources(limit=1))
    assert len(result["files"]) == 1
    assert result["total_matched"] == 2


def test_v29_original_comment_is_indexed(cfg, collection):
    (Path(cfg["project_root"]) / "a.md").write_text("<!-- original design rationale -->\n\nBody")
    count, status = indexer.index_file(collection, cfg, "a.md", {"language": "en"})
    assert status == "ok" and count > 0
    documents = collection.add.call_args.kwargs["documents"]
    assert any("original design rationale" in doc for doc in documents)


def test_v30_directory_error_preserves_progress(cfg, collection):
    root = Path(cfg["project_root"])
    (root / "a.md").write_text("Valid source content")
    (root / "b.md").mkdir()
    scanner.save_manifest(cfg, {name: {"status": "pending_index", "language": "en"}
                                for name in ("a.md", "b.md")})
    try:
        indexer.run_incremental(cfg)
    except OSError:
        pass  # Even an abort must preserve completed indexing status.
    assert scanner.load_manifest(cfg)["a.md"]["status"] == "indexed"


def test_v31_zero_top_not_silently_defaulted(cfg, monkeypatch):
    monkeypatch.setattr(query, "load_config", lambda: cfg)
    called = Mock()
    monkeypatch.setattr(query, "query", called)
    monkeypatch.setattr(sys, "argv", ["query", "body", "--top", "0", "--no-reindex"])
    try:
        query.main()
    except SystemExit as exc:
        assert exc.code != 0  # Explicit validation failure is acceptable.
        return
    assert called.call_args.kwargs["top_k"] == 0


def test_v32_status_reports_database_failure(cfg, monkeypatch, capsys):
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    monkeypatch.setattr(indexer, "get_collection", Mock(side_effect=RuntimeError("db unavailable")))
    exit_code = cli.cmd_status(SimpleNamespace())
    result = json.loads(capsys.readouterr().out)
    assert exit_code != 0 or result.get("error"), "DB failure needs a failure status or explicit error"


def test_v34_collection_name_rejects_newline():
    with pytest.raises(config.ConfigError):
        config.validate_collection_name("abc_knowledge\n")


def test_v35_config_discovery_skips_directory(tmp_path):
    parent_config = tmp_path / "knowledgebase.yaml"
    parent_config.write_text("project_name: parent\n")
    child = tmp_path / "child"
    child.mkdir()
    (child / "knowledgebase.yaml").mkdir()
    assert config.find_config(child) == parent_config


def test_v36_malformed_mcp_has_controlled_error(tmp_path, monkeypatch):
    # Never open or create a credential/config file: substitute a synthetic
    # empty stream only for this path and reject every other attempted open.
    monkeypatch.chdir(tmp_path)
    target = tmp_path / ".mcp.json"
    exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: True if path == target else exists(path))

    def fake_open(path, *args, **kwargs):
        assert Path(path) == target
        return io.StringIO("")

    monkeypatch.setattr("builtins.open", fake_open)
    assert cli.cmd_install_mcp(SimpleNamespace(name="test")) != 0


def test_v37_empty_query_json_keeps_schema(cfg, collection, capsys):
    collection.count.return_value = 0
    query.query(cfg, "body", as_json=True)
    result = json.loads(capsys.readouterr().out)
    assert {"query", "results", "total_chunks", "pending_translation"} <= result.keys()
