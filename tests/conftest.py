"""Shared test fixtures for probe."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sample_project"


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to the sample project fixtures."""
    return FIXTURES_DIR


@pytest.fixture
def tmp_probe_dir(tmp_path: Path):
    """A temporary .probe directory for testing."""
    probe_dir = tmp_path / ".probe"
    probe_dir.mkdir()
    return probe_dir


@pytest.fixture
def sample_markdown() -> str:
    """Sample markdown content for testing."""
    return (FIXTURES_DIR / "docs" / "design.md").read_text()


@pytest.fixture
def sample_code() -> str:
    """Sample Python code for testing."""
    return (FIXTURES_DIR / "src" / "auth.py").read_text()


@pytest.fixture
def sample_text() -> str:
    """Sample plain text for testing."""
    return (FIXTURES_DIR / "notes.txt").read_text()
