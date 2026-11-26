"""Tests for prokb.config — discovery, default-merge, derived names, path helpers."""
import pytest
import yaml

from prokb.config import (
    CONFIG_FILENAME, ConfigError, find_config, load_config,
    chroma_db_path, manifest_path, translations_dir, project_root,
    write_default_config,
)


def _write_cfg(d, **over):
    body = {"version": 1, "project_name": "demo", "collection_name": "demo_knowledge"}
    body.update(over)
    (d / CONFIG_FILENAME).write_text(yaml.safe_dump(body))


def test_find_config_in_cwd(tmp_path):
    _write_cfg(tmp_path)
    assert find_config(tmp_path) == tmp_path / CONFIG_FILENAME


def test_find_config_walks_up(tmp_path):
    _write_cfg(tmp_path)
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_config(deep) == tmp_path / CONFIG_FILENAME


def test_find_config_raises_when_absent(tmp_path):
    with pytest.raises(ConfigError):
        find_config(tmp_path)


def test_load_config_merges_defaults(tmp_path):
    # User config only sets include; the rest must come from DEFAULTS.
    _write_cfg(tmp_path, scan={"include": ["docs/**/*.md"]})
    cfg = load_config(tmp_path / CONFIG_FILENAME)
    assert cfg["scan"]["include"] == ["docs/**/*.md"]
    # exclude_dirs falls back to the default list (which contains 'knowledge')
    assert "knowledge" in cfg["scan"]["exclude_dirs"]
    assert cfg["embedding"]["model"] == "all-mpnet-base-v2"
    assert cfg["chunking"]["max_tokens"] == 1500


def test_load_config_injects_project_root(tmp_path):
    _write_cfg(tmp_path)
    cfg = load_config(tmp_path / CONFIG_FILENAME)
    assert project_root(cfg) == tmp_path.resolve()
    assert cfg["_config_path"] == str((tmp_path / CONFIG_FILENAME).resolve())


def test_derived_names_when_missing(tmp_path):
    # No project_name/collection_name -> derive from directory name.
    proj = tmp_path / "my.cool-proj"
    proj.mkdir()
    (proj / CONFIG_FILENAME).write_text(yaml.safe_dump({"version": 1}))
    cfg = load_config(proj / CONFIG_FILENAME)
    assert cfg["project_name"] == "my.cool-proj"
    # dots and dashes are replaced with underscores for the collection name
    assert cfg["collection_name"] == "my_cool_proj_knowledge"


def test_path_helpers(tmp_path):
    _write_cfg(tmp_path)
    cfg = load_config(tmp_path / CONFIG_FILENAME)
    assert chroma_db_path(cfg) == tmp_path.resolve() / "knowledge" / "chroma_db"
    assert manifest_path(cfg) == tmp_path.resolve() / "knowledge" / "manifest.json"
    assert translations_dir(cfg) == tmp_path.resolve() / "knowledge" / "translations"


@pytest.mark.parametrize("preset,model,trans", [
    ("general", "all-mpnet-base-v2", False),
    ("multilingual", "intfloat/multilingual-e5-large", False),
    ("hu-translate", "all-mpnet-base-v2", True),
])
def test_write_default_config_presets(tmp_path, preset, model, trans):
    dest = tmp_path / CONFIG_FILENAME
    write_default_config(dest, project_name="p", preset=preset)
    cfg = load_config(dest)
    assert cfg["embedding"]["model"] == model
    assert cfg["translation"]["enabled"] is trans


def test_dot_directory_project_name_gives_valid_collection(tmp_path):
    from prokb.config import derive_collection_name
    assert derive_collection_name(".agents") == "agents_knowledge"
    assert derive_collection_name("my-proj") == "my_proj_knowledge"
    dest = tmp_path / CONFIG_FILENAME
    write_default_config(dest, project_name=".agents")
    assert yaml.safe_load(dest.read_text())["collection_name"] == "agents_knowledge"
    # Derived-at-load path (no collection_name in the file) agrees.
    proj = tmp_path / ".agents"
    proj.mkdir()
    (proj / CONFIG_FILENAME).write_text(yaml.safe_dump({"version": 1}))
    assert load_config(proj / CONFIG_FILENAME)["collection_name"] == "agents_knowledge"


def _init_ns():
    import argparse
    return argparse.Namespace(force=True, preset="general", project_name="gi")


def test_init_creates_gitignore_with_three_lines(tmp_path, monkeypatch, capsys):
    from prokb.cli import cmd_init, GITIGNORE_HEADER
    monkeypatch.chdir(tmp_path)
    assert cmd_init(_init_ns()) == 0
    lines = (tmp_path / ".gitignore").read_text().splitlines()
    assert GITIGNORE_HEADER in lines
    for ln in ("knowledge/chroma_db/", "knowledge/manifest.json", "knowledge/.kb.lock"):
        assert lines.count(ln) == 1
    assert "Added to .gitignore" in capsys.readouterr().out


def test_init_gitignore_idempotent_and_preserves_content(tmp_path, monkeypatch):
    from prokb.cli import cmd_init
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/\nknowledge/manifest.json")
    assert cmd_init(_init_ns()) == 0
    first = (tmp_path / ".gitignore").read_text()
    assert first.startswith("node_modules/\nknowledge/manifest.json\n")
    assert first.splitlines().count("knowledge/manifest.json") == 1
    assert first.splitlines().count("knowledge/chroma_db/") == 1
    assert cmd_init(_init_ns()) == 0
    assert (tmp_path / ".gitignore").read_text() == first


def test_derive_collection_name_rejects_symbol_only_name():
    from prokb.config import derive_collection_name
    with pytest.raises(ConfigError, match="no letters or digits"):
        derive_collection_name("...")


def test_kb_init_reports_unusable_project_name(tmp_path, monkeypatch, capsys):
    import argparse
    from prokb.cli import cmd_init
    monkeypatch.chdir(tmp_path)
    rc = cmd_init(argparse.Namespace(force=False, preset="general", project_name="--"))
    assert rc == 1
    assert "collection name" in capsys.readouterr().err
    assert not (tmp_path / CONFIG_FILENAME).exists()


@pytest.mark.parametrize("bad", ["_agents_knowledge", "ab", "x" * 513, "has space",
                                 "trailing_", "ünï"])
def test_invalid_explicit_collection_name_gives_clear_error(tmp_path, bad):
    _write_cfg(tmp_path, collection_name=bad)
    with pytest.raises(ConfigError, match="Invalid collection_name"):
        load_config(tmp_path / CONFIG_FILENAME)


def test_valid_explicit_collection_name_kept(tmp_path):
    _write_cfg(tmp_path, collection_name="My.Coll-1")
    assert load_config(tmp_path / CONFIG_FILENAME)["collection_name"] == "My.Coll-1"


def test_template_and_defaults_exclude_claude_dir(tmp_path):
    dest = tmp_path / CONFIG_FILENAME
    write_default_config(dest, project_name="p")
    raw = yaml.safe_load(dest.read_text())
    for d in (".claude", ".hypothesis", ".pytest_cache"):
        assert d in raw["scan"]["exclude_dirs"]
    from prokb.config import DEFAULTS
    for d in (".claude", ".hypothesis", ".pytest_cache"):
        assert d in DEFAULTS["scan"]["exclude_dirs"]


def test_write_default_config_excludes_knowledge(tmp_path):
    dest = tmp_path / CONFIG_FILENAME
    write_default_config(dest, project_name="p")
    raw = yaml.safe_load(dest.read_text())
    assert "knowledge" in raw["scan"]["exclude_dirs"]
    assert "knowledgebase.yaml" in raw["scan"]["exclude_files"]


def test_template_exclude_dirs_match_defaults(tmp_path):
    """The generated YAML must exclude exactly what DEFAULTS excludes (no drift:
    a shorter template let build/ and dist/ copies get indexed)."""
    from prokb.config import DEFAULTS
    dest = tmp_path / "knowledgebase.yaml"
    write_default_config(dest, project_name="p")
    cfg = yaml.safe_load(dest.read_text())
    assert cfg["scan"]["exclude_dirs"] == DEFAULTS["scan"]["exclude_dirs"]
    for d in ("build", "dist", ".claude", "knowledge"):
        assert d in cfg["scan"]["exclude_dirs"]
