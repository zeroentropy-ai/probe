"""Tests for the CLI interface."""

import json

import pytest
from click.testing import CliRunner

import probe
from probe.cli import _print_json, main
from probe.models import ContextResponse, SearchResult
from probe.store.database import ProbeDB

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


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
        assert probe.__version__ in result.output

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

    def test_doctor_command_exists(self, runner):
        result = runner.invoke(main, ["doctor", "--help"])
        assert result.exit_code == 0

    def test_smoke_command_exists(self, runner):
        result = runner.invoke(main, ["smoke", "--help"])
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

    def test_status_json_outputs_parseable_report(self, runner, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        probe_dir = tmp_path / ".probe"
        probe_dir.mkdir()
        db = ProbeDB(probe_dir / "probe.db")
        db.initialize()
        file_id = db.add_file("README.md", "abc", "markdown")
        db.add_chunk(
            file_id=file_id,
            chunk_index=0,
            content="# Readme",
            file_type="markdown",
            char_start=0,
            char_end=8,
            token_count=3,
            line_start=1,
            line_end=1,
        )
        db.commit()
        db.close()

        result = runner.invoke(main, ["status", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_files"] == 1
        assert data["total_chunks"] == 1
        assert data["providers"]["embedding"] == "zeroentropy/zembed-1"

    def test_search_json_outputs_line_ranges(self, runner, monkeypatch, tmp_path):
        from unittest.mock import MagicMock, patch

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PROBE_REFRESH_TTL", "-1")
        monkeypatch.setenv("ZEROENTROPY_API_KEY", "test")
        (tmp_path / ".probe").mkdir()

        response = ContextResponse(
            query="auth",
            results=[
                SearchResult(
                    score=0.95,
                    file="src/auth.py",
                    file_type="code",
                    content="def login():\n    return True",
                    char_range=(0, 28),
                    line_start=7,
                    line_end=8,
                )
            ],
            total_tokens=5,
            sources_searched=1,
        )

        with patch("probe.cli._build_providers", return_value=(MagicMock(), None)), \
             patch("probe.search.engine.ContextEngine.search", return_value=response):
            result = runner.invoke(main, ["search", "auth", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["results"][0]["file"] == "src/auth.py"
        assert data["results"][0]["line_start"] == 7
        assert data["results"][0]["line_end"] == 8

    def test_print_json_does_not_wrap_long_values(self, capsys):
        _print_json({"fix": "x" * 240})

        data = json.loads(capsys.readouterr().out)
        assert data["fix"] == "x" * 240

    def test_smoke_json_is_parseable_when_api_key_missing(self, runner, monkeypatch):
        monkeypatch.delenv("ZEROENTROPY_API_KEY", raising=False)

        result = runner.invoke(main, ["smoke", "--json"])

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "FAIL"
        assert "ZEROENTROPY_API_KEY" in data["error"]

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
        assert "--client" in result.output

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

    def test_install_codex_uses_api_key_flag(self, runner, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("codex", "probe") else None,
        )
        captured = {}

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1 if cmd[:3] == ["/fake/codex", "mcp", "get"] else 0
            r.stdout = b""
            r.stderr = b""
            if cmd[:3] == ["/fake/codex", "mcp", "add"]:
                captured["cmd"] = cmd
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(main, ["install", "--client", "codex", "--api-key", "ze-test"])

        assert result.exit_code == 0, result.output
        assert captured["cmd"] == [
            "/fake/codex", "mcp", "add", "probe",
            "--env", "ZEROENTROPY_API_KEY=ze-test",
            "--", "/fake/probe", "mcp",
        ]
        assert "Codex" in result.output

    def test_install_codex_no_embed_key_omits_env(self, runner, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("codex", "probe") else None,
        )
        captured = {}

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1 if cmd[:3] == ["/fake/codex", "mcp", "get"] else 0
            r.stdout = b""
            r.stderr = b""
            if cmd[:3] == ["/fake/codex", "mcp", "add"]:
                captured["cmd"] = cmd
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(main, ["install", "--client", "codex", "--no-embed-key"])

        assert result.exit_code == 0, result.output
        assert captured["cmd"] == [
            "/fake/codex", "mcp", "add", "probe", "--", "/fake/probe", "mcp",
        ]

    def test_install_codex_can_preapprove_tools_and_zeroentropy_network(
        self, runner, monkeypatch, tmp_path,
    ):
        codex_home = tmp_path / "codex-home"
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("codex", "probe") else None,
        )

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1 if cmd[:3] == ["/fake/codex", "mcp", "get"] else 0
            r.stdout = b""
            r.stderr = b""
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(
            main,
            [
                "install",
                "--client", "codex",
                "--api-key", "ze-test",
                "--approve-tools",
                "--allow-zeroentropy-network",
            ],
        )

        assert result.exit_code == 0, result.output
        config = tomllib.loads((codex_home / "config.toml").read_text())
        assert config["sandbox_workspace_write"]["network_access"] is True
        assert config["features"]["network_proxy"]["enabled"] is True
        domains = config["features"]["network_proxy"]["domains"]
        assert domains["api.zeroentropy.dev"] == "allow"
        assert domains["pypi.org"] == "allow"
        assert domains["files.pythonhosted.org"] == "allow"
        direct = config["mcp_servers"]["probe"]
        assert direct["default_tools_approval_mode"] == "approve"
        for tool in ("probe_search", "probe_index", "probe_status", "probe_read"):
            assert direct["tools"][tool]["approval_mode"] == "approve"
        plugin = config["plugins"]["probe@zeroentropy"]["mcp_servers"]["probe"]
        assert plugin["default_tools_approval_mode"] == "approve"
        for tool in ("probe_search", "probe_index", "probe_status", "probe_read"):
            assert plugin["tools"][tool]["approval_mode"] == "approve"
        assert "Codex auto-review can use probe" in result.output

    def test_install_codex_accepts_custom_home_and_bin(
        self, runner, monkeypatch, tmp_path,
    ):
        codex_home = tmp_path / "custom-codex"
        custom_codex = tmp_path / "bin" / "codex-custom"
        custom_codex.parent.mkdir()
        custom_codex.write_text("#!/bin/sh\n")
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/probe" if name == "probe" else None,
        )
        seen = []

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1 if cmd[:3] == [str(custom_codex), "mcp", "get"] else 0
            r.stdout = b""
            r.stderr = b""
            seen.append((cmd, kw.get("env")))
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(
            main,
            [
                "install",
                "--client", "codex",
                "--codex-bin", str(custom_codex),
                "--codex-home", str(codex_home),
                "--api-key", "ze-test",
                "--approve-tools",
                "--allow-zeroentropy-network",
            ],
        )

        assert result.exit_code == 0, result.output
        add_cmd, add_env = next(
            call for call in seen if call[0][:3] == [str(custom_codex), "mcp", "add"]
        )
        assert add_cmd[0] == str(custom_codex)
        assert add_env["CODEX_HOME"] == str(codex_home)
        assert (codex_home / "config.toml").exists()
        config = tomllib.loads((codex_home / "config.toml").read_text())
        assert config["mcp_servers"]["probe"]["default_tools_approval_mode"] == "approve"

    def test_install_codex_plugin_installs_marketplace_plugin(
        self, runner, monkeypatch,
    ):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("codex", "probe") else None,
        )
        seen = []

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1 if cmd[:3] == ["/fake/codex", "mcp", "get"] else 0
            r.stdout = b""
            r.stderr = b""
            seen.append(cmd)
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(
            main,
            ["install", "--client", "codex", "--plugin", "--api-key", "ze-test"],
        )

        assert result.exit_code == 0, result.output
        assert [
            "/fake/codex", "plugin", "marketplace", "add",
            "https://github.com/zeroentropy-ai/probe.git",
            "--sparse", ".agents/plugins",
            "--sparse", "plugins/probe-codex",
        ] in seen
        assert ["/fake/codex", "plugin", "add", "probe@zeroentropy"] in seen

    def test_install_claude_plugin_installs_marketplace_plugin(
        self, runner, monkeypatch,
    ):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("claude", "probe") else None,
        )
        seen = []

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1 if cmd[:3] == ["/fake/claude", "mcp", "get"] else 0
            r.stdout = b""
            r.stderr = b""
            seen.append(cmd)
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(
            main,
            ["install", "--client", "claude", "--plugin", "--api-key", "ze-test"],
        )

        assert result.exit_code == 0, result.output
        assert [
            "/fake/claude", "plugin", "marketplace", "add",
            "https://github.com/zeroentropy-ai/probe.git",
            "--sparse", ".claude-plugin", "plugins",
        ] in seen
        assert [
            "/fake/claude", "plugin", "install",
            "--config", "zeroentropy_api_key=ze-test",
            "probe@zeroentropy",
        ] in seen

    def test_install_claude_plugin_requires_api_key(self, runner):
        result = runner.invoke(
            main,
            ["install", "--client", "claude", "--plugin", "--no-embed-key"],
        )

        assert result.exit_code != 0
        assert "--plugin for Claude Code requires an API key" in result.output

    def test_codex_review_flags_require_codex_client(self, runner):
        result = runner.invoke(main, ["install", "--client", "claude", "--approve-tools"])

        assert result.exit_code == 2
        assert "--approve-tools requires --client codex or --client both" in result.output

    def test_install_both_reuses_env_key_confirmation(self, runner, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name in ("claude", "codex", "probe") else None,
        )
        monkeypatch.setenv("ZEROENTROPY_API_KEY", "env-key-xyz")
        seen = []

        def fake_run(cmd, *a, **kw):
            class R:
                pass
            r = R()
            r.returncode = 1 if cmd[1:3] == ["mcp", "get"] else 0
            r.stdout = b""
            r.stderr = b""
            seen.append(cmd)
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(main, ["install", "--client", "both"], input="\n")

        assert result.exit_code == 0, result.output
        assert result.output.count("Use $ZEROENTROPY_API_KEY from environment?") == 1
        claude_add = next(cmd for cmd in seen if cmd[:3] == ["/fake/claude", "mcp", "add-json"])
        codex_add = next(cmd for cmd in seen if cmd[:3] == ["/fake/codex", "mcp", "add"])
        assert json.loads(claude_add[-1])["env"]["ZEROENTROPY_API_KEY"] == "env-key-xyz"
        assert "ZEROENTROPY_API_KEY=env-key-xyz" in codex_add

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

    def test_uninstall_codex_calls_codex_mcp_remove(self, runner, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/fake/" + name if name == "codex" else None,
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

        result = runner.invoke(main, ["uninstall", "--client", "codex"])

        assert result.exit_code == 0
        assert ["/fake/codex", "mcp", "remove", "probe"] in seen

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
