"""Tests for smart chunking."""

from probe.indexer.chunkers import chunk_code, chunk_content, chunk_markdown, chunk_pdf, chunk_text


class TestMarkdownChunker:
    def test_splits_on_headers(self, sample_markdown: str):
        chunks = chunk_markdown(sample_markdown, "docs/design.md")
        assert len(chunks) >= 4
        oauth_chunk = next((c for c in chunks if c.header_path and "OAuth" in c.header_path), None)
        assert oauth_chunk is not None
        assert "PKCE" in oauth_chunk.content

    def test_preserves_header_hierarchy(self, sample_markdown: str):
        chunks = chunk_markdown(sample_markdown, "docs/design.md")
        oauth_chunk = next(c for c in chunks if c.header_path and "OAuth" in c.header_path)
        assert "Authentication" in oauth_chunk.header_path

    def test_chunk_indices_are_sequential(self, sample_markdown: str):
        chunks = chunk_markdown(sample_markdown, "docs/design.md")
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i

    def test_all_chunks_have_content(self, sample_markdown: str):
        chunks = chunk_markdown(sample_markdown, "docs/design.md")
        for chunk in chunks:
            assert len(chunk.content.strip()) > 0


class TestCodeChunker:
    def test_splits_on_functions_and_classes(self, sample_code: str):
        chunks = chunk_code(sample_code, "src/auth.py")
        assert len(chunks) >= 2
        has_class = any(c.symbol_name and "OAuthHandler" in c.symbol_name for c in chunks)
        assert has_class

    def test_fallback_sliding_window(self):
        content = "x = 1\n" * 500
        chunks = chunk_code(content, "script.lua")
        assert len(chunks) >= 1
        for chunk in chunks:
            assert len(chunk.content) <= 2000

    def test_chunks_include_line_ranges(self):
        content = (
            "import os\n"
            "\n"
            "class OAuthHandler:\n"
            "    def login(self):\n"
            "        return 'ok'\n"
            "\n"
            "def helper():\n"
            "    return True\n"
        )
        chunks = chunk_code(content, "src/auth.py")

        oauth_chunk = next(c for c in chunks if c.symbol_name == "OAuthHandler")
        assert oauth_chunk.line_start == 3
        assert oauth_chunk.line_end == 5


class TestTextChunker:
    def test_splits_on_paragraphs(self, sample_text: str):
        chunks = chunk_text(sample_text, "notes.txt")
        assert len(chunks) >= 1
        assert all(c.file_type == "text" for c in chunks)

    def test_merges_small_paragraphs(self):
        content = "Short.\n\nAlso short.\n\nStill short."
        chunks = chunk_text(content, "tiny.txt")
        assert len(chunks) == 1

    def test_counts_tiktoken_special_token_text_as_normal_content(self):
        chunks = chunk_text("before <|endoftext|> after", "notes.txt")
        assert len(chunks) == 1
        assert chunks[0].token_count > 0


class TestPDFChunker:
    def test_splits_on_page_breaks(self):
        content = "Page one content\n\n--- PAGE BREAK ---\n\nPage two content"
        chunks = chunk_pdf(content, "doc.pdf")
        assert len(chunks) == 2
        assert chunks[0].page_number == 1
        assert chunks[1].page_number == 2


class TestChunkContent:
    def test_dispatches_by_file_type(self, sample_markdown: str):
        chunks = chunk_content(sample_markdown, "docs/design.md", "markdown")
        assert all(c.file_type == "markdown" for c in chunks)

    def test_dispatches_code(self, sample_code: str):
        chunks = chunk_content(sample_code, "src/auth.py", "code")
        assert all(c.file_type == "code" for c in chunks)
