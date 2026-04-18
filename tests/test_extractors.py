"""Tests for content extractors."""

from pathlib import Path

from probe.indexer.extractors import classify_file_type, extract_content


class TestClassifyFileType:
    def test_markdown(self):
        assert classify_file_type(Path("docs/design.md")) == "markdown"
        assert classify_file_type(Path("docs/intro.mdx")) == "markdown"

    def test_code(self):
        assert classify_file_type(Path("src/main.py")) == "code"
        assert classify_file_type(Path("src/app.ts")) == "code"

    def test_pdf(self):
        assert classify_file_type(Path("docs/spec.pdf")) == "pdf"

    def test_text(self):
        assert classify_file_type(Path("notes.txt")) == "text"
        assert classify_file_type(Path("api.yaml")) == "text"


class TestExtractContent:
    def test_extract_markdown(self, fixtures_dir: Path):
        content = extract_content(fixtures_dir / "docs" / "design.md")
        assert "OAuth" in content
        assert "PKCE" in content

    def test_extract_code(self, fixtures_dir: Path):
        content = extract_content(fixtures_dir / "src" / "auth.py")
        assert "class OAuthHandler" in content

    def test_extract_text(self, fixtures_dir: Path):
        content = extract_content(fixtures_dir / "notes.txt")
        assert "Sprint Planning" in content
