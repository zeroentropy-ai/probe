"""Tests for the distributable Claude Code plugin assets."""

import json
from pathlib import Path

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "probe"
PROJECT_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_marketplace_points_to_probe_plugin():
    marketplace_path = ROOT / ".claude-plugin" / "marketplace.json"
    marketplace = json.loads(marketplace_path.read_text())

    assert marketplace["name"] == "zeroentropy"
    assert marketplace["owner"]["name"] == "ZeroEntropy"

    probe_entry = next(
        plugin for plugin in marketplace["plugins"] if plugin["name"] == "probe"
    )
    assert probe_entry["source"] == "./plugins/probe"
    assert probe_entry["category"] == "Developer Tools"


def test_plugin_manifest_declares_skill_and_mcp_config():
    manifest_path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text())

    assert manifest["name"] == "probe"
    assert manifest["displayName"] == "probe"
    assert manifest["version"] == PROJECT_VERSION
    assert "project knowledge" in manifest["description"].lower()
    assert manifest["repository"] == "https://github.com/zeroentropy-ai/probe"
    assert manifest["mcpServers"] == "./.mcp.json"

    api_key = manifest["userConfig"]["zeroentropy_api_key"]
    assert api_key == {
        "type": "string",
        "title": "ZeroEntropy API key",
        "description": "API key used by probe for embeddings and reranking.",
        "sensitive": True,
        "required": True,
    }


def test_mcp_config_runs_probe_from_pypi_with_configured_api_key():
    mcp_path = PLUGIN_ROOT / ".mcp.json"
    mcp = json.loads(mcp_path.read_text())

    server = mcp["mcpServers"]["probe"]
    assert server["command"] == "uvx"
    assert server["args"] == ["--from", f"probe-search=={PROJECT_VERSION}", "probe", "mcp"]
    assert server["env"] == {
        "ZEROENTROPY_API_KEY": "${user_config.zeroentropy_api_key}",
    }


def test_skill_teaches_agents_to_use_probe_before_file_sweeps():
    skill_path = PLUGIN_ROOT / "skills" / "use-probe" / "SKILL.md"
    raw = skill_path.read_text()
    frontmatter = yaml.safe_load(raw.split("---", 2)[1])
    body = raw.split("---", 2)[2]

    assert frontmatter["name"] == "use-probe"
    assert "probe_search" in frontmatter["description"]
    assert "architecture" in frontmatter["description"].lower()
    assert "probe_search" in body
    assert "probe_read" in body
    assert "probe_index" in body
    assert "grep" in body.lower()
    assert len(body.splitlines()) <= 40


def test_readme_documents_plugin_install_path():
    readme = (ROOT / "README.md").read_text()

    assert "/plugin marketplace add zeroentropy-ai/probe" in readme
    assert "/plugin install probe@zeroentropy" in readme
    assert f"uvx --from probe-search=={PROJECT_VERSION} probe mcp" in readme
    assert "ZeroEntropy API key" in readme
