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
