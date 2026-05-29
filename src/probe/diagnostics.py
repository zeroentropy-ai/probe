"""Local setup diagnostics for probe."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import probe

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass
class DiagnosticCheck:
    name: str
    status: str
    detail: str
    fix: str = ""
    optional: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
            "optional": self.optional,
        }


@dataclass
class DoctorReport:
    status: str
    version: str
    cwd: str
    checks: list[DiagnosticCheck]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "version": self.version,
            "cwd": self.cwd,
            "checks": [check.to_dict() for check in self.checks],
        }


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess]
WhichFunc = Callable[[str], str | None]


def _run_command(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=5)


def _status(checks: list[DiagnosticCheck]) -> str:
    if any(check.status == FAIL for check in checks):
        return FAIL
    if any(check.status == WARN for check in checks):
        return WARN
    return PASS


def _maybe_strict(check: DiagnosticCheck, strict: bool) -> DiagnosticCheck:
    if strict and check.status == WARN and check.optional:
        return DiagnosticCheck(
            name=check.name,
            status=FAIL,
            detail=check.detail,
            fix=check.fix,
            optional=check.optional,
        )
    return check


def _check_executable(name: str, which: WhichFunc, optional: bool, fix: str) -> DiagnosticCheck:
    path = which(name)
    if path:
        return DiagnosticCheck(name=name, status=PASS, detail=path, optional=optional)
    status = WARN if optional else FAIL
    return DiagnosticCheck(
        name=name,
        status=status,
        detail=f"{name} not found on PATH",
        fix=fix,
        optional=optional,
    )


def _check_api_key(env: Mapping[str, str]) -> DiagnosticCheck:
    if env.get("ZEROENTROPY_API_KEY"):
        return DiagnosticCheck("ZEROENTROPY_API_KEY", PASS, "present")
    return DiagnosticCheck(
        "ZEROENTROPY_API_KEY",
        FAIL,
        "missing",
        "export ZEROENTROPY_API_KEY=\"ze_xxx\"",
    )


def _check_index(cwd: Path) -> DiagnosticCheck:
    probe_dir = cwd / ".probe"
    if not probe_dir.exists():
        return DiagnosticCheck(
            ".probe index",
            WARN,
            "No local index found",
            "Run `probe index .` or ask your MCP client a question to auto-index.",
            optional=True,
        )

    try:
        from probe.search.vector import VectorStore
        from probe.store.database import ProbeDB

        db = ProbeDB(probe_dir / "probe.db")
        db.initialize()
        stats = db.get_stats()
        vector_store = VectorStore(probe_dir / "vectors.npy", dimensions=1)
        vector_store.load()
        vector_count = len(vector_store._ids)
        db.close()
    except Exception as e:
        return DiagnosticCheck(
            ".probe index",
            FAIL,
            f"Index cannot be opened: {e}",
            "Run `probe index --full` or remove `.probe/` and re-index.",
        )

    if stats["total_chunks"] > 0 and vector_count == 0:
        return DiagnosticCheck(
            ".probe index",
            FAIL,
            f"{stats['total_chunks']} chunks in SQLite but no vectors loaded",
            "Run `probe index --full`.",
        )
    return DiagnosticCheck(
        ".probe index",
        PASS,
        f"{stats['total_files']} files, {stats['total_chunks']} chunks",
    )


def _check_claude_mcp(claude_path: str | None, run_command: CommandRunner) -> DiagnosticCheck:
    if not claude_path:
        return DiagnosticCheck(
            "Claude MCP probe",
            WARN,
            "Claude Code CLI not found",
            "Install Claude Code or use `probe mcp` with another MCP client.",
            optional=True,
        )
    try:
        result = run_command(["claude", "mcp", "get", "probe"])
    except Exception as e:
        return DiagnosticCheck(
            "Claude MCP probe",
            WARN,
            f"check failed: {e}",
            "Run `claude mcp get probe` manually for details.",
            optional=True,
        )
    if result.returncode == 0:
        return DiagnosticCheck("Claude MCP probe", PASS, "registered")
    return DiagnosticCheck(
        "Claude MCP probe",
        WARN,
        "not registered at direct MCP scope",
        "Use the Claude plugin flow or run `probe install`.",
        optional=True,
    )


def _check_claude_plugin(claude_path: str | None, run_command: CommandRunner) -> DiagnosticCheck:
    if not claude_path:
        return DiagnosticCheck(
            "Claude plugin probe@zeroentropy",
            WARN,
            "Claude Code CLI not found",
            "Install Claude Code to use the plugin.",
            optional=True,
        )

    try:
        result = run_command(["claude", "plugin", "list", "--available", "--json"])
    except Exception as e:
        return DiagnosticCheck(
            "Claude plugin probe@zeroentropy",
            WARN,
            f"check failed: {e}",
            "Run `claude plugin list --available` manually for details.",
            optional=True,
        )
    output = result.stdout.decode(errors="replace")
    if result.returncode == 0 and "probe@zeroentropy" in output:
        return DiagnosticCheck("Claude plugin probe@zeroentropy", PASS, "available")
    return DiagnosticCheck(
        "Claude plugin probe@zeroentropy",
        WARN,
        "not found in available plugin list",
        "Run `/plugin marketplace add https://github.com/zeroentropy-ai/probe.git`.",
        optional=True,
    )


def _check_codex_mcp(codex_path: str | None, run_command: CommandRunner) -> DiagnosticCheck:
    if not codex_path:
        return DiagnosticCheck(
            "Codex MCP probe",
            WARN,
            "Codex CLI not found",
            "Install Codex or use `probe mcp` with another MCP client.",
            optional=True,
        )
    try:
        result = run_command(["codex", "mcp", "get", "probe"])
    except Exception as e:
        return DiagnosticCheck(
            "Codex MCP probe",
            WARN,
            f"check failed: {e}",
            "Run `codex mcp get probe` manually for details.",
            optional=True,
        )
    if result.returncode == 0:
        return DiagnosticCheck("Codex MCP probe", PASS, "registered")
    return DiagnosticCheck(
        "Codex MCP probe",
        WARN,
        "not registered at direct MCP scope",
        "Run `probe install --client codex`.",
        optional=True,
    )


def _check_codex_plugin(codex_path: str | None, run_command: CommandRunner) -> DiagnosticCheck:
    if not codex_path:
        return DiagnosticCheck(
            "Codex plugin probe@zeroentropy",
            WARN,
            "Codex CLI not found",
            "Install Codex to use the plugin.",
            optional=True,
        )

    try:
        result = run_command(["codex", "plugin", "list", "--marketplace", "zeroentropy"])
    except Exception as e:
        return DiagnosticCheck(
            "Codex plugin probe@zeroentropy",
            WARN,
            f"check failed: {e}",
            "Run `codex plugin list --marketplace zeroentropy` manually for details.",
            optional=True,
        )
    output = result.stdout.decode(errors="replace")
    if result.returncode == 0 and "probe@zeroentropy" in output:
        return DiagnosticCheck("Codex plugin probe@zeroentropy", PASS, "available")
    return DiagnosticCheck(
        "Codex plugin probe@zeroentropy",
        WARN,
        "not found in zeroentropy marketplace",
        "Run `codex plugin marketplace add https://github.com/zeroentropy-ai/probe.git "
        "--sparse .agents/plugins --sparse plugins/probe-codex` then "
        "`codex plugin add probe@zeroentropy`.",
        optional=True,
    )


def run_doctor(
    cwd: Path | None = None,
    strict: bool = False,
    no_network: bool = False,
    which: WhichFunc = shutil.which,
    run_command: CommandRunner = _run_command,
    env: Mapping[str, str] | None = None,
) -> DoctorReport:
    """Run local diagnostics without printing secrets or modifying config."""
    del no_network  # Reserved for future provider/version reachability checks.

    cwd = cwd or Path.cwd()
    env = env or os.environ
    claude_path = which("claude")
    codex_path = which("codex")
    checks = [
        DiagnosticCheck("probe", PASS, f"{probe.__version__} via {sys.executable}"),
        _check_executable("uvx", which, optional=True, fix="Install uv from https://docs.astral.sh/uv/."),
        _check_executable(
            "claude", which, optional=True,
            fix="Install Claude Code from https://code.claude.com/docs/.",
        ),
        _check_executable(
            "codex", which, optional=True,
            fix="Install Codex from https://developers.openai.com/codex/.",
        ),
        _check_api_key(env),
        _check_index(cwd),
        _check_claude_plugin(claude_path, run_command),
        _check_claude_mcp(claude_path, run_command),
        _check_codex_plugin(codex_path, run_command),
        _check_codex_mcp(codex_path, run_command),
    ]
    checks = [_maybe_strict(check, strict) for check in checks]
    return DoctorReport(
        status=_status(checks),
        version=probe.__version__,
        cwd=str(cwd),
        checks=checks,
    )
