"""Helpers for Codex config entries used by probe."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

PROBE_TOOLS = ("probe_search", "probe_index", "probe_status", "probe_read")
ZEROENTROPY_NETWORK_DOMAINS = (
    "api.zeroentropy.dev",
    "pypi.org",
    "files.pythonhosted.org",
)

_TABLE_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")


@dataclass(frozen=True)
class CodexProbeAutoReviewStatus:
    config_path: Path
    exists: bool
    zeroentropy_network_allowed: bool
    probe_tools_approved: bool

    @property
    def ready(self) -> bool:
        return self.zeroentropy_network_allowed and self.probe_tools_approved


def codex_config_path(env: Mapping[str, str] | None = None) -> Path:
    env = env or os.environ
    codex_home = env.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def configure_codex_probe_auto_review(
    *,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    approve_tools: bool = False,
    allow_zeroentropy_network: bool = False,
) -> list[str]:
    """Persist Codex policy that lets probe run without auto-review denials."""
    path = config_path or codex_config_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    changes: list[str] = []

    if allow_zeroentropy_network:
        text = _set_table_key(text, "sandbox_workspace_write", "network_access", "true")
        text = _set_table_key(text, "features.network_proxy", "enabled", "true")
        for domain in ZEROENTROPY_NETWORK_DOMAINS:
            text = _set_table_key(
                text,
                "features.network_proxy.domains",
                f'"{domain}"',
                '"allow"',
            )
        changes.append("ZeroEntropy network allowlist")

    if approve_tools:
        for server_table in (
            "mcp_servers.probe",
            'plugins."probe@zeroentropy".mcp_servers.probe',
        ):
            text = _set_table_key(
                text,
                server_table,
                "default_tools_approval_mode",
                '"approve"',
            )
            for tool in PROBE_TOOLS:
                text = _set_table_key(
                    text,
                    f"{server_table}.tools.{tool}",
                    "approval_mode",
                    '"approve"',
                )
        changes.append("probe MCP tool approvals")

    path.write_text(text)
    return changes


def inspect_codex_probe_auto_review(
    env: Mapping[str, str] | None = None,
    *,
    config_path: Path | None = None,
) -> CodexProbeAutoReviewStatus:
    path = config_path or codex_config_path(env)
    if not path.exists():
        return CodexProbeAutoReviewStatus(
            config_path=path,
            exists=False,
            zeroentropy_network_allowed=False,
            probe_tools_approved=False,
        )

    text = path.read_text()
    network_allowed = (
        _get_table_key(text, "sandbox_workspace_write", "network_access") == "true"
        and _get_table_key(text, "features.network_proxy", "enabled") == "true"
        and _get_table_key(
            text,
            "features.network_proxy.domains",
            '"api.zeroentropy.dev"',
        ) == '"allow"'
    )
    tools_approved = any(
        _server_tools_approved(text, table)
        for table in (
            "mcp_servers.probe",
            'plugins."probe@zeroentropy".mcp_servers.probe',
        )
    )
    return CodexProbeAutoReviewStatus(
        config_path=path,
        exists=True,
        zeroentropy_network_allowed=network_allowed,
        probe_tools_approved=tools_approved,
    )


def _server_tools_approved(text: str, table: str) -> bool:
    return (
        _get_table_key(text, table, "default_tools_approval_mode") == '"approve"'
        and all(
            _get_table_key(text, f"{table}.tools.{tool}", "approval_mode") == '"approve"'
            for tool in PROBE_TOOLS
        )
    )


def _set_table_key(text: str, table: str, key: str, value: str) -> str:
    lines = text.splitlines()
    bounds = _table_bounds(lines, table)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{table}]")
        lines.append(f"{key} = {value}")
        return "\n".join(lines) + "\n"

    start, end = bounds
    for i in range(start + 1, end):
        if _line_has_key(lines[i], key):
            lines[i] = f"{key} = {value}"
            return "\n".join(lines) + "\n"

    lines.insert(end, f"{key} = {value}")
    return "\n".join(lines) + "\n"


def _get_table_key(text: str, table: str, key: str) -> str | None:
    lines = text.splitlines()
    bounds = _table_bounds(lines, table)
    if bounds is None:
        return None
    start, end = bounds
    for line in lines[start + 1:end]:
        if _line_has_key(line, key):
            return line.split("=", 1)[1].strip()
    return None


def _table_bounds(lines: list[str], table: str) -> tuple[int, int] | None:
    start: int | None = None
    for i, line in enumerate(lines):
        match = _TABLE_RE.match(line)
        if not match:
            continue
        if match.group(1) == table:
            start = i
            continue
        if start is not None:
            return start, i
    if start is None:
        return None
    return start, len(lines)


def _line_has_key(line: str, key: str) -> bool:
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#"):
        return False
    return stripped.startswith(key) and stripped[len(key):].lstrip().startswith("=")
