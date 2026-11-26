"""Smoke test: the MCP server module must import against the installed `mcp`
SDK and register its tools. Before 0.3.1 no test imported it, so an `mcp`
major upgrade (FastMCP renamed in 2.x) broke `kb mcp` without any test failing."""
import asyncio
import importlib
import sys

import yaml


def test_mcp_server_imports_and_registers_tools(tmp_path, monkeypatch):
    (tmp_path / "knowledgebase.yaml").write_text(yaml.safe_dump(
        {"version": 1, "project_name": "mcptest",
         "collection_name": "mcptest_knowledge"}))
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("prokb.mcp_server", None)
    mod = importlib.import_module("prokb.mcp_server")
    try:
        names = {t.name for t in asyncio.run(mod.mcp.list_tools())}
    finally:
        sys.modules.pop("prokb.mcp_server", None)
    assert {"knowledge_search", "knowledge_status",
            "knowledge_list_sources"} <= names
