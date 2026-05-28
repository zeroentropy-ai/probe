"""Tests for MCP server tool definitions."""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from probe.config import ProbeConfig
from probe.mcp.server import _build_providers, create_mcp_server
from probe.models import SearchResult


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
    fake_response.results = [
        SearchResult(
            score=0.9,
            file="src/auth.py",
            file_type="code",
            content="def login():\n    return True",
            char_range=(0, 28),
            line_start=12,
            line_end=13,
        )
    ]
    fake_response.total_tokens = 5
    fake_response.sources_searched = 1

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
    assert data["results"][0]["line_start"] == 12
    assert data["results"][0]["line_end"] == 13


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
    # Pin monotonic clock so the gate's TTL check is deterministic regardless
    # of how long the machine has been up (fresh CI runners start near zero).
    now = [1_000_000.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])

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


def test_mcp_embedding_provider_requires_api_key(monkeypatch):
    """MCP should fail fast with setup guidance instead of surfacing SDK connection errors."""
    monkeypatch.delenv("ZEROENTROPY_API_KEY", raising=False)

    with pytest.raises(ValueError, match="ZEROENTROPY_API_KEY not set"):
        _build_providers(ProbeConfig())


def test_probe_read_supports_line_windows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".probe").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "line 1\nline 2\nline 3\nline 4\nline 5\n"
    )

    server = create_mcp_server()
    tool = server._tool_manager._tools["probe_read"]

    result = tool.fn(file_path="src/auth.py", line_start=2, line_end=4)

    assert result == "line 2\nline 3\nline 4"
