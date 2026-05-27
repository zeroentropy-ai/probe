"""Release metadata checks for PyPI and README install snippets."""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

import probe

ROOT = Path(__file__).resolve().parents[1]


def test_project_version_matches_package_version():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert data["project"]["version"] == probe.__version__


def test_pypi_metadata_has_discovery_fields():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = data["project"]

    assert "semantic-search" in project["keywords"]
    assert "mcp" in project["keywords"]
    assert "claude-code" in project["keywords"]
    assert project["urls"]["Repository"] == "https://github.com/zeroentropy-ai/probe"
    assert project["urls"]["Issues"] == "https://github.com/zeroentropy-ai/probe/issues"
    assert project["urls"]["Changelog"] == "https://github.com/zeroentropy-ai/probe/blob/main/CHANGELOG.md"
    assert "Programming Language :: Python :: 3.14" in project["classifiers"]


def test_readme_uvx_snippet_uses_probe_executable():
    readme = (ROOT / "README.md").read_text()

    assert '"args": ["--from", "probe-search", "probe", "mcp"]' in readme
    assert '"args": ["probe-search", "mcp"]' not in readme
