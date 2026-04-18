"""Tests for the CLI interface."""

import pytest
from click.testing import CliRunner

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
                   return_value={"total_files": 1, "total_chunks": 1, "file_types": {},
                                 "last_indexed": None}):
            mock_build.return_value = (MagicMock(), None)
            mock_refresh.return_value = {"added": 0, "changed": 0, "removed": 0, "elapsed_ms": 10}
            mock_search.return_value = MagicMock(results=[], total_tokens=0,
                                                  sources_searched=0, query="x")

            result = runner.invoke(main, ["search", "x"])
            assert mock_refresh.called, f"refresh_changed was not called. Output: {result.output}"

    def test_install_exits_when_claude_not_on_path(self, runner, monkeypatch):
        def mock_which(name):
            return None if name == "claude" else "/usr/bin/" + name
        monkeypatch.setattr("shutil.which", mock_which)
        result = runner.invoke(main, ["install"])
        assert result.exit_code == 1
        assert "Claude Code CLI not found" in result.output

    def test_install_command_exists(self, runner):
        result = runner.invoke(main, ["install", "--help"])
        assert result.exit_code == 0

    def test_install_uses_api_key_flag(self, runner, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("claude", "probe") else None,
        )
        # Stub claude-mcp-get to "not installed" (exit 1) and claude-mcp-add to success.
        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1 if "get" in cmd else 0
            r.stdout = b""
            r.stderr = b""
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(main, ["install", "--api-key", "sk-test-123"])
        # No key prompt appears in output (non-interactive via flag)
        assert "Enter your ZeroEntropy API key" not in result.output

    def test_install_uses_env_key_by_default(self, runner, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("claude", "probe") else None,
        )
        monkeypatch.setenv("ZEROENTROPY_API_KEY", "env-key-xyz")
        captured = {}
        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1 if "get" in cmd else 0
            r.stdout = b""
            r.stderr = b""
            if "add" in cmd:
                captured["cmd"] = cmd
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        # Hit enter at the confirm prompt → default yes
        result = runner.invoke(main, ["install"], input="\n")
        assert result.exit_code == 0
        assert "Use $ZEROENTROPY_API_KEY from environment?" in result.output
        # Verify the env key ended up in the add args
        assert "ZEROENTROPY_API_KEY=env-key-xyz" in " ".join(captured["cmd"])

    def test_install_rejects_empty_key_after_retries(self, runner, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("claude", "probe") else None,
        )
        monkeypatch.delenv("ZEROENTROPY_API_KEY", raising=False)
        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1
            r.stdout = b""
            r.stderr = b""
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        # Give empty input 3 times (4 newlines = 4 empty tries, hit the cap)
        result = runner.invoke(main, ["install"], input="\n\n\n\n")
        assert result.exit_code == 1
        assert "API key required" in result.output

    def test_install_no_embed_key_omits_env(self, runner, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("claude", "probe") else None,
        )
        captured = {}

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1 if "get" in cmd else 0
            r.stdout = b""
            r.stderr = b""
            if "add" in cmd:
                captured["cmd"] = cmd
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(main, ["install", "--no-embed-key"])
        assert result.exit_code == 0
        joined = " ".join(captured["cmd"])
        assert "ZEROENTROPY_API_KEY=" not in joined
        assert "-e" not in captured["cmd"]
        # Verify structural integrity: the `--` separator must precede the probe argv.
        assert "--" in captured["cmd"]
        dash_idx = captured["cmd"].index("--")
        assert dash_idx < captured["cmd"].index("/fake/probe")

    def test_install_already_registered_cancels_without_force(self, runner, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("claude", "probe") else None,
        )

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            # "get" succeeds → already registered
            r.returncode = 0
            r.stdout = b""
            r.stderr = b""
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        # Hit enter → default "no" for the reinstall confirm
        result = runner.invoke(main, ["install"], input="\n")
        assert result.exit_code == 0
        assert "already registered" in result.output
        assert "No changes made" in result.output

    def test_install_force_skips_confirmation(self, runner, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("claude", "probe") else None,
        )
        monkeypatch.setenv("ZEROENTROPY_API_KEY", "k")
        seen = []

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 0  # "get" says installed; "remove" and "add" both succeed
            r.stdout = b""
            r.stderr = b""
            seen.append(cmd[:3])
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        # No stdin — would fail if confirm prompted
        result = runner.invoke(main, ["install", "--force"], input="\n")
        assert result.exit_code == 0
        # We should have called get, remove, add
        assert any("remove" in cmd for cmd in seen)
        assert any("add" in cmd for cmd in seen)
