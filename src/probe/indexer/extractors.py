"""Content extraction by file format."""

from __future__ import annotations

from pathlib import Path

MARKDOWN_EXTS = {".md", ".mdx"}
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java"}
PDF_EXTS = {".pdf"}
TEXT_EXTS = {".txt", ".rst", ".adoc", ".tex", ".yaml", ".yml", ".json"}


def classify_file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in MARKDOWN_EXTS:
        return "markdown"
    if suffix in CODE_EXTS:
        return "code"
    if suffix in PDF_EXTS:
        return "pdf"
    return "text"


def extract_content(path: Path) -> str:
    file_type = classify_file_type(path)
    if file_type == "pdf":
        return _extract_pdf(path)
    return path.read_text(encoding="utf-8", errors="replace")


def _extract_pdf(path: Path) -> str:
    import pymupdf
    doc = pymupdf.open(str(path))
    pages = []
    for page in doc:
        text = page.get_text()
        if text.strip():
            pages.append(text)
    doc.close()
    return "\n\n--- PAGE BREAK ---\n\n".join(pages)
