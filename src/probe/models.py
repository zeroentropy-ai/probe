"""Shared data models for probe."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chunk:
    """A chunk of content from an indexed file."""

    id: str
    file_path: str
    file_type: str  # "markdown" | "code" | "pdf" | "text"
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    token_count: int
    header_path: str | None = None  # markdown only
    symbol_name: str | None = None  # code only
    page_number: int | None = None  # pdf only


@dataclass
class SearchResult:
    """A single result from the context engine."""

    score: float
    file: str
    file_type: str
    content: str
    char_range: tuple[int, int]
    header_path: str | None = None
    symbol_name: str | None = None
    page_number: int | None = None


@dataclass
class ContextResponse:
    """The assembled context response from a query."""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    total_tokens: int = 0
    sources_searched: int = 0


@dataclass
class RerankResult:
    """Result from a reranking provider."""

    index: int
    score: float
    text: str
