"""Tests for the CLI interface."""

from click.testing import CliRunner
import pytest
from probe.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestCLI:
    def test_help(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_index_command_exists(self, runner):
        result = runner.invoke(main, ["index", "--help"])
        assert result.exit_code == 0

    def test_search_command_exists(self, runner):
        result = runner.invoke(main, ["search", "--help"])
        assert result.exit_code == 0

    def test_status_command_exists(self, runner):
        result = runner.invoke(main, ["status", "--help"])
        assert result.exit_code == 0

    def test_mcp_command_exists(self, runner):
        result = runner.invoke(main, ["mcp", "--help"])
        assert result.exit_code == 0

    def test_search_calls_refresh_when_gate_allows(self, runner, monkeypatch, tmp_path):
        """When PROBE_REFRESH_TTL=0, every search invocation triggers refresh_changed."""
        import os
        from unittest.mock import MagicMock, patch

        os.environ["PROBE_REFRESH_TTL"] = "0"
        os.environ["ZEROENTROPY_API_KEY"] = "test"
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".probe").mkdir()

        # Mock the pieces so we don't hit disk/API, just observe refresh is called.
        with patch("probe.cli._build_providers") as mock_build, \
             patch("probe.indexer.pipeline.IndexPipeline.refresh_changed") as mock_refresh, \
             patch("probe.search.engine.ContextEngine.search") as mock_search, \
             patch("probe.store.database.ProbeDB.get_stats",
                   return_value={"total_files": 1, "total_chunks": 1, "file_types": {}, "last_indexed": None}):
            mock_build.return_value = (MagicMock(), None)
            mock_refresh.return_value = {"added": 0, "changed": 0, "removed": 0, "elapsed_ms": 10}
            mock_search.return_value = MagicMock(results=[], total_tokens=0, sources_searched=0, query="x")

            result = runner.invoke(main, ["search", "x"])
            assert mock_refresh.called, f"refresh_changed was not called. Output: {result.output}"
