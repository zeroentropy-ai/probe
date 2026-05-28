"""Tests for setup diagnostics."""

import json

from probe.diagnostics import FAIL, PASS, run_doctor


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_doctor_reports_missing_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ZEROENTROPY_API_KEY", raising=False)

    report = run_doctor(
        cwd=tmp_path,
        which=lambda name: None,
        run_command=lambda cmd: FakeCompletedProcess(returncode=1),
    )

    api_key_check = next(c for c in report.checks if c.name == "ZEROENTROPY_API_KEY")
    assert api_key_check.status == FAIL
    assert "export ZEROENTROPY_API_KEY" in api_key_check.fix
    assert report.status == FAIL


def test_doctor_never_serializes_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROENTROPY_API_KEY", "ze_secret_value")

    report = run_doctor(
        cwd=tmp_path,
        which=lambda name: None,
        run_command=lambda cmd: FakeCompletedProcess(returncode=1),
    )

    payload = json.dumps(report.to_dict())
    assert "ze_secret_value" not in payload
    assert "present" in payload


def test_doctor_strict_turns_optional_warnings_into_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROENTROPY_API_KEY", "ze_test")

    report = run_doctor(
        cwd=tmp_path,
        strict=True,
        which=lambda name: None,
        run_command=lambda cmd: FakeCompletedProcess(returncode=1),
    )

    claude_check = next(c for c in report.checks if c.name == "claude")
    assert claude_check.status == FAIL
    assert report.status == FAIL


def test_doctor_reports_direct_mcp_registration(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROENTROPY_API_KEY", "ze_test")

    def fake_which(name):
        return f"/usr/local/bin/{name}" if name in {"uvx", "claude"} else None

    def fake_run(cmd):
        if cmd[:3] == ["claude", "mcp", "get"]:
            return FakeCompletedProcess(returncode=0, stdout=b"probe configured")
        if cmd[:3] == ["claude", "plugin", "list"]:
            return FakeCompletedProcess(returncode=0, stdout=b"[]")
        return FakeCompletedProcess(returncode=1)

    report = run_doctor(cwd=tmp_path, which=fake_which, run_command=fake_run)

    mcp_check = next(c for c in report.checks if c.name == "Claude MCP probe")
    assert mcp_check.status == PASS


def test_doctor_recommends_https_plugin_marketplace_url(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROENTROPY_API_KEY", "ze_test")

    def fake_which(name):
        return f"/usr/local/bin/{name}" if name in {"uvx", "claude"} else None

    def fake_run(cmd):
        if cmd[:3] == ["claude", "plugin", "list"]:
            return FakeCompletedProcess(returncode=0, stdout=b"[]")
        return FakeCompletedProcess(returncode=1)

    report = run_doctor(cwd=tmp_path, which=fake_which, run_command=fake_run)

    plugin_check = next(
        c for c in report.checks if c.name == "Claude plugin probe@zeroentropy"
    )
    assert (
        "/plugin marketplace add https://github.com/zeroentropy-ai/probe.git "
        "--sparse .claude-plugin plugins"
    ) in plugin_check.fix
    assert "/plugin marketplace add zeroentropy-ai/probe " not in plugin_check.fix
