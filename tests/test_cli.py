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
        assert "0.2.3" in result.output

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
        from unittest.mock import MagicMock, patch

        monkeypatch.setenv("PROBE_REFRESH_TTL", "0")
        monkeypatch.setenv("ZEROENTROPY_API_KEY", "test")
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
            if "add-json" in cmd:
                captured["cmd"] = cmd
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        # Hit enter at the confirm prompt → default yes
        result = runner.invoke(main, ["install"], input="\n")
        assert result.exit_code == 0
        assert "Use $ZEROENTROPY_API_KEY from environment?" in result.output
        # Verify the env key ended up in the JSON config passed to add-json
        import json as _json
        json_arg = captured["cmd"][-1]
        config = _json.loads(json_arg)
        assert config["env"]["ZEROENTROPY_API_KEY"] == "env-key-xyz"
        assert config["type"] == "stdio"
        # Command name in argv is "add-json", not "add"
        assert "add-json" in captured["cmd"]

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
            if "add-json" in cmd:
                captured["cmd"] = cmd
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(main, ["install", "--no-embed-key"])
        assert result.exit_code == 0
        import json as _json
        json_arg = captured["cmd"][-1]
        config = _json.loads(json_arg)
        # No env key should be present when --no-embed-key is used
        assert "env" not in config or not config.get("env")
        # Structural: the command in the JSON config is the probe binary
        assert config["command"] == "/fake/probe"
        assert config["args"] == ["mcp"]
        # Command name in argv is "add-json"
        assert "add-json" in captured["cmd"]

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
        # We should have called get, remove, add-json
        assert any("remove" in cmd for cmd in seen)
        assert any("add-json" in cmd for cmd in seen)

    def test_uninstall_calls_claude_mcp_remove(self, runner, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name == "claude" else None,
        )
        seen = []

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 0
            r.stdout = b""
            r.stderr = b""
            seen.append(cmd)
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(main, ["uninstall"])
        assert result.exit_code == 0
        assert any("remove" in cmd for cmd in seen)

    def test_uninstall_purge_deletes_dot_probe(self, runner, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name == "claude" else None,
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})(),
        )
        monkeypatch.chdir(tmp_path)
        probe_dir = tmp_path / ".probe"
        probe_dir.mkdir()
        (probe_dir / "probe.db").write_text("dummy")

        result = runner.invoke(main, ["uninstall", "--purge"])
        assert result.exit_code == 0
        assert not probe_dir.exists()

    def test_install_enables_probe_in_disabled_projects(self, runner, monkeypatch, tmp_path):
        """After install, probe should be removed from disabledMcpServers in all projects."""
        import json as _json

        # Seed a fake ~/.claude.json with probe disabled in two projects
        home = tmp_path / "home"
        home.mkdir()
        fake_claude_json = home / ".claude.json"
        fake_claude_json.write_text(_json.dumps({
            "projects": {
                "/project/a": {
                    "mcpServers": {},
                    "disabledMcpServers": ["probe", "other-server"],
                    "enabledMcpjsonServers": [],
                    "hasTrustDialogAccepted": True,
                },
                "/project/b": {
                    "mcpServers": {},
                    "disabledMcpServers": ["probe"],
                    "hasTrustDialogAccepted": True,
                },
                "/project/c": {
                    "mcpServers": {},
                    "disabledMcpServers": ["other-server"],  # probe not disabled; leave alone
                },
                "/project/d": {
                    "mcpServers": {},
                    # no disabledMcpServers key at all — leave alone
                },
            },
            "someTopLevelKey": "unchanged",
        }, indent=2))

        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("claude", "probe") else None,
        )

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1 if "get" in cmd else 0  # get → not installed; add-json → success
            r.stdout = b""
            r.stderr = b""
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(main, ["install", "--api-key", "sk-test"])
        assert result.exit_code == 0, f"install failed: {result.output}"

        # Verify the JSON was updated
        data = _json.loads(fake_claude_json.read_text())
        # probe removed from /project/a's list but other-server retained
        assert data["projects"]["/project/a"]["disabledMcpServers"] == ["other-server"]
        # probe removed from /project/b's list (now empty)
        assert data["projects"]["/project/b"]["disabledMcpServers"] == []
        # /project/c untouched (probe wasn't in it)
        assert data["projects"]["/project/c"]["disabledMcpServers"] == ["other-server"]
        # /project/d untouched (no disabledMcpServers key at all)
        assert "disabledMcpServers" not in data["projects"]["/project/d"]
        # Top-level unchanged
        assert data["someTopLevelKey"] == "unchanged"

    def test_install_handles_missing_claude_json(self, runner, monkeypatch, tmp_path):
        """If ~/.claude.json doesn't exist, install still succeeds silently."""
        home = tmp_path / "home"
        home.mkdir()
        # DO NOT create ~/.claude.json

        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("claude", "probe") else None,
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: type("R", (), {"returncode": 1 if "get" in a[0] else 0,
                                            "stdout": b"", "stderr": b""})(),
        )

        result = runner.invoke(main, ["install", "--api-key", "sk-test"])
        assert result.exit_code == 0
        # ~/.claude.json should not have been created
        assert not (home / ".claude.json").exists()

    def test_install_handles_malformed_claude_json(self, runner, monkeypatch, tmp_path):
        """If ~/.claude.json is corrupt, print a warning but don't fail install."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude.json").write_text("{ this is not valid json")

        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("claude", "probe") else None,
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: type("R", (), {"returncode": 1 if "get" in a[0] else 0,
                                            "stdout": b"", "stderr": b""})(),
        )

        result = runner.invoke(main, ["install", "--api-key", "sk-test"])
        assert result.exit_code == 0  # still succeeds
        # Warning should be printed
        assert "Warning" in result.output or "warning" in result.output
