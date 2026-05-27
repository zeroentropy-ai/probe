"""MCP server for probe: expose search tools to AI agents."""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from probe.config import ProbeConfig, load_config
from probe.indexer.refresh_gate import RefreshGate
from probe.providers.base import EmbeddingProvider, RerankProvider
from probe.search.engine import ContextEngine
from probe.search.vector import VectorStore
from probe.store.database import ProbeDB

PROBE_DIR_NAME = ".probe"


class _ServerState:
    """Lazy-initialized shared state for the MCP server."""

    def __init__(self):
        self._engine: ContextEngine | None = None
        self._db: ProbeDB | None = None
        self._config: ProbeConfig | None = None
        self._project_root: Path = Path.cwd()
        self._refresh_gate: RefreshGate = RefreshGate.from_env()

    @property
    def probe_dir(self) -> Path:
        d = self._project_root / PROBE_DIR_NAME
        d.mkdir(exist_ok=True)
        return d

    @property
    def refresh_gate(self) -> RefreshGate:
        return self._refresh_gate

    @property
    def config(self) -> ProbeConfig:
        if self._config is None:
            self._config = load_config(self.probe_dir / "config.yaml")
        return self._config

    @property
    def db(self) -> ProbeDB:
        if self._db is None:
            self._db = ProbeDB(self.probe_dir / "probe.db")
            self._db.initialize()
        return self._db

    def get_engine(self) -> ContextEngine:
        if self._engine is None:
            vector_store = VectorStore(
                self.probe_dir / "vectors.npy",
                dimensions=self.config.embedding_dimensions,
            )
            vector_store.load()
            embedding, reranker = _build_providers(self.config)
            self._engine = ContextEngine(
                db=self.db, vector_store=vector_store,
                embedding_provider=embedding, rerank_provider=reranker,
            )
        return self._engine

    def invalidate(self):
        """Reset cached state after indexing changes."""
        self._engine = None


def _build_providers(config: ProbeConfig):
    """Build providers from config (shared with CLI)."""
    from probe.config import PROVIDER_ENV_VARS

    embedding: EmbeddingProvider
    reranker: RerankProvider | None = None
    env_var = PROVIDER_ENV_VARS.get(config.embedding_provider, "")
    api_key = os.environ.get(env_var, "") if env_var else ""
    if not api_key:
        raise ValueError(
            f"{env_var} not set. Required for {config.embedding_provider} embeddings. "
            f"Run `probe install` with an API key, or set {env_var} in the MCP server environment."
        )

    if config.embedding_provider == "zeroentropy":
        from probe.providers.zeroentropy import ZeroEntropyEmbedding
        embedding = ZeroEntropyEmbedding(
            api_key, config.embedding_model, config.embedding_dimensions,
        )
    elif config.embedding_provider == "openai":
        from probe.providers.openai import OpenAIEmbedding
        embedding = OpenAIEmbedding(
            api_key, config.embedding_model, config.embedding_dimensions,
        )
    elif config.embedding_provider == "cohere":
        from probe.providers.cohere import CohereEmbedding
        embedding = CohereEmbedding(
            api_key, config.embedding_model, config.embedding_dimensions,
        )
    else:
        raise ValueError(f"Unknown embedding provider: {config.embedding_provider}")

    if config.rerank_provider == "zeroentropy":
        api_key = os.environ.get("ZEROENTROPY_API_KEY", "")
        if api_key:
            from probe.providers.zeroentropy import ZeroEntropyRerank
            reranker = ZeroEntropyRerank(api_key, config.rerank_model)
    elif config.rerank_provider == "cohere":
        api_key = os.environ.get("COHERE_API_KEY", "")
        if api_key:
            from probe.providers.cohere import CohereRerank
            reranker = CohereRerank(api_key, config.rerank_model)

    return embedding, reranker


MCP_INSTRUCTIONS = """Use this server to search project knowledge — documentation, design specs, \
ADRs, runbooks, API references, and source code — using semantic search with reranking.

IMPORTANT: probe auto-indexes on first search and incrementally refreshes on every \
subsequent search (within a debounce window), so you normally do not need to call \
probe_index manually. Every probe_search response includes a `refreshed` field with \
counts of files that were newly indexed, changed, or removed.

ALWAYS use probe_search BEFORE reading individual files or grepping when you need to:
- Understand how something works in the project
- Find where a feature is documented or implemented
- Answer questions about architecture, requirements, or design decisions
- Locate relevant code and documentation for a task

probe_search returns ranked results from docs AND code simultaneously, which is faster and \
more accurate than manually grepping or reading files. It uses hybrid retrieval (keyword + \
semantic) with cross-encoder reranking.

Do not use for: writing new code, making edits, or tasks unrelated to understanding the project."""


def create_mcp_server() -> FastMCP:
    server = FastMCP("probe", instructions=MCP_INSTRUCTIONS)
    state = _ServerState()

    @server.tool()
    def probe_search(
        query: str, top_k: int = 10, max_tokens: int = 4096,
        file_types: list[str] | None = None,
    ) -> str:
        """Search project knowledge (docs, specs, code) and return curated, reranked context.
        Use this when you need to understand how something works, find requirements,
        or locate relevant code and documentation."""
        import time as _time

        from probe.indexer.pipeline import IndexPipeline

        config = state.config
        vector_store = VectorStore(
            state.probe_dir / "vectors.npy",
            dimensions=config.embedding_dimensions,
        )

        # Unified refresh (replaces the old "auto-index if empty" path — when the
        # DB is empty, every file is "new" so phase 2 indexes the whole project).
        refreshed_info: dict = {"added": 0, "changed": 0, "removed": 0, "elapsed_ms": 0}
        gate = state.refresh_gate
        if gate.should_refresh():
            t_refresh = _time.monotonic()
            try:
                embedding_for_refresh, _ = _build_providers(config)
                pipeline = IndexPipeline(
                    db=state.db, vector_store=vector_store,
                    embedding_provider=embedding_for_refresh,
                )
                refreshed_info = pipeline.refresh_changed([Path.cwd()])
                gate.mark()
                total_changed = (
                    refreshed_info["added"]
                    + refreshed_info["changed"]
                    + refreshed_info["removed"]
                )
                if total_changed > 0:
                    state.invalidate()
            except Exception as e:
                elapsed_ms = int((_time.monotonic() - t_refresh) * 1000)
                refreshed_info = {
                    "added": 0, "changed": 0, "removed": 0, "elapsed_ms": elapsed_ms,
                    "error": str(e),
                }

        # Note: providers for search are built lazily inside state.get_engine().
        # When refresh ran, it built its own provider pair above; intentional
        # duplication keeps the refresh block self-contained (see cli.py for the
        # parallel comment).
        engine = state.get_engine()
        response = engine.search(
            query=query, top_k=top_k, max_tokens=max_tokens, file_types=file_types,
        )
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
            "refreshed": refreshed_info,
        }, indent=2)

    @server.tool()
    def probe_index(paths: list[str] | None = None, full: bool = False) -> str:
        """Index or re-index project files. Run this if you've added new docs
        or if search results seem stale."""
        from probe.indexer.pipeline import IndexPipeline

        config = state.config
        embedding, _ = _build_providers(config)
        vector_store = VectorStore(
            state.probe_dir / "vectors.npy", dimensions=config.embedding_dimensions,
        )
        pipeline = IndexPipeline(
            db=state.db, vector_store=vector_store, embedding_provider=embedding,
        )
        index_paths = [Path(p) for p in paths] if paths else [Path.cwd()]
        stats = pipeline.index(index_paths, full=full)
        state.invalidate()  # reset cached engine so next search picks up new data
        return json.dumps(stats)

    @server.tool()
    def probe_status() -> str:
        """Show indexing status: file counts, chunk counts, last indexed time, and providers."""
        config = state.config
        stats = state.db.get_stats()
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
        if not target.is_absolute():
            target = Path.cwd() / file_path

        # Security: restrict reads to the project directory
        try:
            target.resolve().relative_to(Path.cwd().resolve())
        except ValueError:
            return json.dumps({"error": "Access denied: path is outside project directory"})

        if not target.exists():
            return json.dumps({"error": f"File not found: {file_path}"})
        return target.read_text(encoding="utf-8", errors="replace")

    return server


def run_mcp_server():
    server = create_mcp_server()
    server.run(transport="stdio")
