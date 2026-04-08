"""MCP server for probe: expose search tools to AI agents."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from probe.config import load_config
from probe.search.vector import VectorStore
from probe.store.database import ProbeDB

PROBE_DIR_NAME = ".probe"


def _get_probe_dir() -> Path:
    probe_dir = Path.cwd() / PROBE_DIR_NAME
    probe_dir.mkdir(exist_ok=True)
    return probe_dir


def create_mcp_server() -> FastMCP:
    server = FastMCP("probe")

    @server.tool()
    def probe_search(query: str, top_k: int = 10, max_tokens: int = 4096,
                     file_types: list[str] | None = None) -> str:
        """Search project knowledge (docs, specs, code) and return curated, reranked context.
        Use this when you need to understand how something works, find requirements,
        or locate relevant code and documentation."""
        from probe.cli import _build_providers
        from probe.search.engine import ContextEngine

        probe_dir = _get_probe_dir()
        config = load_config(probe_dir / "config.yaml")
        db = ProbeDB(probe_dir / "probe.db")
        db.initialize()
        vector_store = VectorStore(
            probe_dir / "vectors.npy",
            dimensions=config.embedding_dimensions,
        )
        vector_store.load()

        embedding, reranker = _build_providers(config)
        engine = ContextEngine(
            db=db, vector_store=vector_store,
            embedding_provider=embedding, rerank_provider=reranker,
        )

        response = engine.search(
            query=query, top_k=top_k,
            max_tokens=max_tokens, file_types=file_types,
        )
        db.close()

        return json.dumps({
            "query": response.query,
            "results": [
                {"score": r.score, "file": r.file, "type": r.file_type,
                 "header_path": r.header_path, "symbol": r.symbol_name,
                 "page": r.page_number, "content": r.content,
                 "char_range": list(r.char_range)}
                for r in response.results
            ],
            "total_tokens": response.total_tokens,
            "sources_searched": response.sources_searched,
        }, indent=2)

    @server.tool()
    def probe_index(paths: list[str] | None = None, full: bool = False) -> str:
        """Index or re-index project files. Run this if you've added new docs
        or if search results seem stale."""
        from probe.cli import _build_providers
        from probe.indexer.pipeline import IndexPipeline

        probe_dir = _get_probe_dir()
        config = load_config(probe_dir / "config.yaml")
        db = ProbeDB(probe_dir / "probe.db")
        db.initialize()

        embedding, _ = _build_providers(config)
        vector_store = VectorStore(
            probe_dir / "vectors.npy",
            dimensions=config.embedding_dimensions,
        )

        pipeline = IndexPipeline(db=db, vector_store=vector_store, embedding_provider=embedding)
        index_paths = [Path(p) for p in paths] if paths else [Path.cwd()]
        stats = pipeline.index(index_paths, full=full)
        db.close()
        return json.dumps(stats)

    @server.tool()
    def probe_status() -> str:
        """Show indexing status: file counts, chunk counts, last indexed time, and providers."""
        probe_dir = _get_probe_dir()
        config = load_config(probe_dir / "config.yaml")
        db = ProbeDB(probe_dir / "probe.db")
        db.initialize()
        stats = db.get_stats()
        db.close()
        return json.dumps({
            **stats, "providers": {
                "embedding": f"{config.embedding_provider}/{config.embedding_model}",
                "reranker": f"{config.rerank_provider}/{config.rerank_model}",
            },
        })

    @server.tool()
    def probe_read(file_path: str) -> str:
        """Read the full content of an indexed file. Use after probe_search
        to get more context from a specific source."""
        target = Path(file_path)
        if not target.exists():
            target = Path.cwd() / file_path
        if not target.exists():
            return json.dumps({"error": f"File not found: {file_path}"})
        return target.read_text(encoding="utf-8", errors="replace")

    return server


def run_mcp_server():
    server = create_mcp_server()
    server.run(transport="stdio")
