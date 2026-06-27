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


def test_write_default_config_excludes_knowledge(tmp_path):
    dest = tmp_path / CONFIG_FILENAME
    write_default_config(dest, project_name="p")
    raw = yaml.safe_load(dest.read_text())
    assert "knowledge" in raw["scan"]["exclude_dirs"]
    assert "knowledgebase.yaml" in raw["scan"]["exclude_files"]
