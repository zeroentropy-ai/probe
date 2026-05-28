"""End-to-end smoke validation for probe."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from probe.config import load_config
from probe.indexer.pipeline import IndexPipeline
from probe.providers.base import EmbeddingProvider, RerankProvider
from probe.search.engine import ContextEngine
from probe.search.vector import VectorStore
from probe.store.database import ProbeDB


@dataclass
class SmokeReport:
    status: str
    project_path: str
    indexed_files: int
    chunks: int
    search_result_count: int
    expected_file: str | None
    expected_file_matched: bool
    temp_project_kept: bool = False
    claude_ready: bool | None = None
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "project_path": self.project_path,
            "indexed_files": self.indexed_files,
            "chunks": self.chunks,
            "search_result_count": self.search_result_count,
            "expected_file": self.expected_file,
            "expected_file_matched": self.expected_file_matched,
            "temp_project_kept": self.temp_project_kept,
            "claude_ready": self.claude_ready,
            "error": self.error,
        }


def _write_sample_project(path: Path) -> str:
    (path / "docs").mkdir(parents=True)
    (path / "src").mkdir(parents=True)
    expected = path / "docs" / "probe-smoke.md"
    expected.write_text(
        "# Probe smoke test\n\n"
        "The rainbow handshake confirms that probe can index and retrieve project knowledge.\n"
    )
    (path / "src" / "app.py").write_text("def healthcheck():\n    return 'ok'\n")
    return "docs/probe-smoke.md"


def _first_words(text: str, limit: int = 6) -> str:
    words = [word.strip("#`.,:;()[]{}") for word in text.split()]
    words = [word for word in words if word]
    return " ".join(words[:limit]) or "project"


def _default_providers(config):
    from probe.config import PROVIDER_ENV_VARS

    env_var = PROVIDER_ENV_VARS.get(config.embedding_provider, "")
    api_key = os.environ.get(env_var, "") if env_var else ""
    if not api_key:
        raise ValueError(f"{env_var} not set. Required for probe smoke.")

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

    reranker = None
    if config.rerank_provider == "zeroentropy":
        rerank_key = os.environ.get("ZEROENTROPY_API_KEY", "")
        if rerank_key:
            from probe.providers.zeroentropy import ZeroEntropyRerank
            reranker = ZeroEntropyRerank(rerank_key, config.rerank_model)
    elif config.rerank_provider == "cohere":
        rerank_key = os.environ.get("COHERE_API_KEY", "")
        if rerank_key:
            from probe.providers.cohere import CohereRerank
            reranker = CohereRerank(rerank_key, config.rerank_model)

    return embedding, reranker


def _run_in_project(
    project_path: Path,
    expected_file: str | None,
    embedding_provider: EmbeddingProvider,
    rerank_provider: RerankProvider | None,
) -> SmokeReport:
    project_path = project_path.resolve()
    old_cwd = Path.cwd()
    os.chdir(project_path)
    try:
        probe_dir = project_path / ".probe"
        probe_dir.mkdir(exist_ok=True)
        config = load_config(probe_dir / "config.yaml")
        db = ProbeDB(probe_dir / "probe.db")
        db.initialize()
        vector_store = VectorStore(
            probe_dir / "vectors.npy", dimensions=config.embedding_dimensions,
        )
        pipeline = IndexPipeline(
            db=db, vector_store=vector_store, embedding_provider=embedding_provider,
        )
        pipeline.index([project_path], full=True)
        stats = db.get_stats()

        if stats["total_chunks"] == 0:
            db.close()
            return SmokeReport(
                status="FAIL",
                project_path=str(project_path),
                indexed_files=stats["total_files"],
                chunks=stats["total_chunks"],
                search_result_count=0,
                expected_file=expected_file,
                expected_file_matched=False,
                error="No chunks were indexed.",
            )

        if expected_file is None:
            first_chunk = db.get_all_chunks()[0]
            expected_file = first_chunk["file_path"]
            query = _first_words(first_chunk["content"])
        else:
            query = "rainbow handshake"

        engine = ContextEngine(
            db=db,
            vector_store=vector_store,
            embedding_provider=embedding_provider,
            rerank_provider=rerank_provider,
        )
        response = engine.search(query=query, top_k=5)
        matched = any(result.file == expected_file for result in response.results)
        db.close()
        return SmokeReport(
            status="PASS" if matched else "FAIL",
            project_path=str(project_path),
            indexed_files=stats["total_files"],
            chunks=stats["total_chunks"],
            search_result_count=len(response.results),
            expected_file=expected_file,
            expected_file_matched=matched,
            error="" if matched else f"Expected {expected_file} in search results.",
        )
    finally:
        os.chdir(old_cwd)


def run_smoke(
    current: bool = False,
    keep: bool = False,
    claude: bool = False,
    embedding_provider: EmbeddingProvider | None = None,
    rerank_provider: RerankProvider | None = None,
) -> SmokeReport:
    """Index and search a tiny project to prove the product works end to end."""
    if current:
        project_path = Path.cwd()
        expected_file = None
        cleanup_path = None
    else:
        if keep:
            project_path = Path(tempfile.mkdtemp(prefix="probe-smoke-"))
            cleanup_path = None
        else:
            cleanup_path = tempfile.mkdtemp(prefix="probe-smoke-")
            project_path = Path(cleanup_path)
        expected_file = _write_sample_project(project_path)

    try:
        if claude and shutil.which("claude") is None:
            return SmokeReport(
                status="FAIL",
                project_path=str(project_path),
                indexed_files=0,
                chunks=0,
                search_result_count=0,
                expected_file=expected_file,
                expected_file_matched=False,
                temp_project_kept=bool(keep and not current),
                claude_ready=False,
                error="Claude Code CLI not found.",
            )
        if claude:
            from probe.diagnostics import PASS, run_doctor

            doctor = run_doctor(cwd=project_path)
            claude_ready = any(
                check.name in {"Claude MCP probe", "Claude plugin probe@zeroentropy"}
                and check.status == PASS
                for check in doctor.checks
            )
            if not claude_ready:
                return SmokeReport(
                    status="FAIL",
                    project_path=str(project_path),
                    indexed_files=0,
                    chunks=0,
                    search_result_count=0,
                    expected_file=expected_file,
                    expected_file_matched=False,
                    temp_project_kept=bool(keep and not current),
                    claude_ready=False,
                    error="Claude Code does not currently expose probe via MCP or plugin.",
                )
        config = load_config(project_path / ".probe" / "config.yaml")
        if embedding_provider is None:
            embedding_provider, rerank_provider = _default_providers(config)
        report = _run_in_project(
            project_path=project_path,
            expected_file=expected_file,
            embedding_provider=embedding_provider,
            rerank_provider=rerank_provider,
        )
        report.temp_project_kept = bool(keep and not current)
        if claude:
            report.claude_ready = True
        return report
    except Exception as e:
        return SmokeReport(
            status="FAIL",
            project_path=str(project_path),
            indexed_files=0,
            chunks=0,
            search_result_count=0,
            expected_file=expected_file,
            expected_file_matched=False,
            temp_project_kept=bool(keep and not current),
            claude_ready=False if claude else None,
            error=str(e),
        )
    finally:
        if not current and not keep and cleanup_path:
            shutil.rmtree(cleanup_path, ignore_errors=True)
