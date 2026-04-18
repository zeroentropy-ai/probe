"""Tests for MCP server tool definitions."""

import json
from unittest.mock import MagicMock, patch

import numpy as np

from probe.mcp.server import create_mcp_server


class TestMCPServer:
    def test_server_has_tools(self):
        server = create_mcp_server()
        assert server is not None

    def test_server_name(self):
        server = create_mcp_server()
        assert server.name == "probe"


def _fake_embed():
    return MagicMock(
        dimensions=4,
        embed=MagicMock(return_value=np.zeros((1, 4), dtype=np.float32)),
    )


def test_probe_search_returns_refreshed_field(tmp_path, monkeypatch):
    """probe_search JSON response should always include a refreshed field."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".probe").mkdir()
    monkeypatch.setenv("ZEROENTROPY_API_KEY", "test")
    monkeypatch.setenv("PROBE_REFRESH_TTL", "0")

    server = create_mcp_server()
    # Find the registered probe_search tool
    tool = server._tool_manager._tools["probe_search"]  # FastMCP internal

    fake_response = MagicMock()
    fake_response.query = "x"
    fake_response.results = []
    fake_response.total_tokens = 0
    fake_response.sources_searched = 0

    with (
        patch("probe.search.engine.ContextEngine.search", return_value=fake_response),
        patch(
            "probe.indexer.pipeline.IndexPipeline.refresh_changed",
            return_value={"added": 0, "changed": 2, "removed": 0, "elapsed_ms": 50},
        ),
        patch(
            "probe.mcp.server._build_providers",
            return_value=(_fake_embed(), None),
        ),
    ):
        result_json = tool.fn(query="x")

    data = json.loads(result_json)
    assert "refreshed" in data
    assert data["refreshed"] == {"added": 0, "changed": 2, "removed": 0, "elapsed_ms": 50}


def test_probe_search_refresh_error_does_not_block_search(tmp_path, monkeypatch):
    """A failing refresh surfaces an error field but search still runs."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".probe").mkdir()
    monkeypatch.setenv("ZEROENTROPY_API_KEY", "test")
    monkeypatch.setenv("PROBE_REFRESH_TTL", "0")

    server = create_mcp_server()
    tool = server._tool_manager._tools["probe_search"]

    fake_response = MagicMock()
    fake_response.query = "x"
    fake_response.results = []
    fake_response.total_tokens = 0
    fake_response.sources_searched = 0

    with (
        patch("probe.search.engine.ContextEngine.search", return_value=fake_response),
        patch(
            "probe.indexer.pipeline.IndexPipeline.refresh_changed",
            side_effect=RuntimeError("rate limited"),
        ),
        patch(
            "probe.mcp.server._build_providers",
            return_value=(_fake_embed(), None),
        ),
    ):
        result_json = tool.fn(query="x")

    data = json.loads(result_json)
    assert "refreshed" in data
    assert "error" in data["refreshed"]
    assert "rate limited" in data["refreshed"]["error"]
    assert data["refreshed"]["elapsed_ms"] >= 0
    # Search still ran
    assert data["query"] == "x"


def test_probe_search_gate_persists_across_calls(tmp_path, monkeypatch):
    """The RefreshGate lives on _ServerState so the debounce window actually works.

    With PROBE_REFRESH_TTL=60s, the first call should trigger refresh, and a
    second call within the window should NOT trigger refresh again.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".probe").mkdir()
    monkeypatch.setenv("ZEROENTROPY_API_KEY", "test")
    monkeypatch.setenv("PROBE_REFRESH_TTL", "60")

    server = create_mcp_server()
    tool = server._tool_manager._tools["probe_search"]

    fake_response = MagicMock()
    fake_response.query = "x"
    fake_response.results = []
    fake_response.total_tokens = 0
    fake_response.sources_searched = 0

    with (
        patch("probe.search.engine.ContextEngine.search", return_value=fake_response),
        patch(
            "probe.indexer.pipeline.IndexPipeline.refresh_changed",
            return_value={"added": 0, "changed": 0, "removed": 0, "elapsed_ms": 10},
        ) as mock_refresh,
        patch(
            "probe.mcp.server._build_providers",
            return_value=(_fake_embed(), None),
        ),
    ):
        tool.fn(query="first")
        tool.fn(query="second")

    # With TTL=60s and back-to-back calls, gate should block the second refresh.
    assert mock_refresh.call_count == 1, (
        f"Expected gate to debounce second call; got {mock_refresh.call_count} refresh calls"
    )
