"""CLI interface for probe."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

import probe
from probe.config import DEFAULT_MODELS, ProbeConfig, detect_provider, load_config, save_config

console = Console()
PROBE_DIR_NAME = ".probe"


def _find_probe_dir(create: bool = False) -> Path:
    probe_dir = Path.cwd() / PROBE_DIR_NAME
    if create:
        probe_dir.mkdir(exist_ok=True)
    return probe_dir


def _get_config() -> ProbeConfig:
    probe_dir = _find_probe_dir()
    return load_config(probe_dir / "config.yaml")


def _build_providers(config: ProbeConfig):
    from probe.config import PROVIDER_ENV_VARS
    from probe.providers.base import EmbeddingProvider, RerankProvider

    embedding: EmbeddingProvider
    reranker: RerankProvider | None = None

    env_var = PROVIDER_ENV_VARS.get(config.embedding_provider, "")
    api_key = os.environ.get(env_var, "") if env_var else ""
    if not api_key:
        console.print(
            f"[red]Error: {env_var} not set."
            f" Required for {config.embedding_provider} embeddings.[/red]"
        )
        sys.exit(1)

    if config.embedding_provider == "zeroentropy":
        from probe.providers.zeroentropy import ZeroEntropyEmbedding
        embedding = ZeroEntropyEmbedding(
            api_key, config.embedding_model, config.embedding_dimensions,
        )
    elif config.embedding_provider == "openai":
        from probe.providers.openai import OpenAIEmbedding
        embedding = OpenAIEmbedding(api_key, config.embedding_model, config.embedding_dimensions)
    elif config.embedding_provider == "cohere":
        from probe.providers.cohere import CohereEmbedding
        embedding = CohereEmbedding(api_key, config.embedding_model, config.embedding_dimensions)
    else:
        console.print(f"[red]Unknown embedding provider: {config.embedding_provider}[/red]")
        sys.exit(1)

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


@click.group()
@click.version_option(version=probe.__version__, prog_name="probe")
def main():
    """probe -- AI Agent Context Engine. Give your coding agent a brain beyond code."""
    pass


@main.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option("--full", is_flag=True, help="Force full re-index")
def index(paths, full):
    """Index project files for semantic search."""
    from probe.indexer.pipeline import IndexPipeline
    from probe.search.vector import VectorStore
    from probe.store.database import ProbeDB

    if not paths:
        paths = (".",)

    config = _get_config()
    probe_dir = _find_probe_dir(create=True)
    db = ProbeDB(probe_dir / "probe.db")
    db.initialize()

    embedding, _ = _build_providers(config)
    vector_store = VectorStore(probe_dir / "vectors.npy", dimensions=config.embedding_dimensions)

    pipeline = IndexPipeline(db=db, vector_store=vector_store, embedding_provider=embedding)

    console.print(f"[bold]Indexing {len(paths)} path(s)...[/bold]")
    stats = pipeline.index([Path(p) for p in paths], full=full)

    console.print(
        f"\n[green]Done![/green] "
        f"Indexed {stats['files_indexed']} files, "
        f"created {stats['chunks_created']} chunks, "
        f"skipped {stats['files_skipped']} unchanged files."
    )
    db.close()


@main.command()
@click.argument("query")
@click.option("--top-k", default=10, help="Max results to return")
@click.option("--max-tokens", default=4096, help="Token budget for results")
@click.option("--type", "file_types", multiple=True, help="Filter by file type")
@click.option("--no-rerank", is_flag=True, help="Skip reranking")
def search(query, top_k, max_tokens, file_types, no_rerank):
    """Search project knowledge with natural language."""
    from probe.search.engine import ContextEngine
    from probe.search.vector import VectorStore
    from probe.store.database import ProbeDB

    config = _get_config()
    probe_dir = _find_probe_dir()
    db = ProbeDB(probe_dir / "probe.db")
    db.initialize()
    vector_store = VectorStore(probe_dir / "vectors.npy", dimensions=config.embedding_dimensions)
    vector_store.load()

    embedding, reranker = _build_providers(config)

    engine = ContextEngine(db=db, vector_store=vector_store,
                           embedding_provider=embedding,
                           rerank_provider=reranker if not no_rerank else None)

    t0 = time.time()
    response = engine.search(query=query, top_k=top_k, max_tokens=max_tokens,
                             file_types=list(file_types) if file_types else None,
                             rerank=not no_rerank)
    elapsed = time.time() - t0

    if not response.results:
        console.print("[yellow]No results found.[/yellow]")
        db.close()
        return

    console.print(f"\n [bold]Found {len(response.results)} results[/bold] "
                  f"({response.sources_searched} chunks searched)\n")

    for result in response.results:
        score_color = "green" if result.score > 0.7 else "yellow" if result.score > 0.4 else "dim"
        score_str = f"[{score_color}][{result.score:.2f}][/{score_color}]"

        loc = f"[cyan]{result.file}[/cyan]"
        if result.header_path:
            loc += f" > [dim]{result.header_path}[/dim]"
        elif result.symbol_name:
            loc += f" > [dim]{result.symbol_name}[/dim]"
        elif result.page_number:
            loc += f" > [dim]page {result.page_number}[/dim]"

        console.print(f" {score_str} {loc}")
        lines = result.content.strip().split("\n")[:3]
        for line in lines:
            console.print(f"   [dim]{line[:100]}[/dim]")
        console.print()

    model_info = f"{config.embedding_model}"
    if not no_rerank and reranker is not None:
        model_info += f" + {config.rerank_model}"
    console.print(f" [dim]{'---' * 14}[/dim]")
    console.print(f" [dim]{model_info} | {response.total_tokens:,} tokens | {elapsed:.1f}s[/dim]\n")
    db.close()


@main.command()
def status():
    """Show index status and configuration."""
    from probe.store.database import ProbeDB

    probe_dir = _find_probe_dir()
    config = _get_config()
    db = ProbeDB(probe_dir / "probe.db")
    db.initialize()
    stats = db.get_stats()

    table = Table(title="probe status")
    table.add_column("Property", style="cyan")
    table.add_column("Value")
    table.add_row("Indexed files", str(stats["total_files"]))
    table.add_row("Total chunks", str(stats["total_chunks"]))
    table.add_row("Last indexed", stats["last_indexed"] or "never")
    for ft, count in stats.get("file_types", {}).items():
        table.add_row(f"  {ft}", str(count))
    table.add_row("Embedding", f"{config.embedding_provider}/{config.embedding_model}")
    table.add_row("Reranker", f"{config.rerank_provider}/{config.rerank_model}")
    console.print(table)
    db.close()


@main.command(name="list")
def list_files():
    """List all indexed files."""
    from probe.store.database import ProbeDB

    probe_dir = _find_probe_dir()
    db = ProbeDB(probe_dir / "probe.db")
    db.initialize()
    files = db.list_files()

    if not files:
        console.print("[yellow]No files indexed. Run 'probe index' first.[/yellow]")
        db.close()
        return

    for f in files:
        console.print(f"  \\[{f['file_type']}] {f['path']}")
    console.print(f"\n[dim]{len(files)} files[/dim]")
    db.close()


@main.command()
def config():
    """Show current provider configuration."""
    cfg = _get_config()
    dims = cfg.embedding_dimensions
    console.print(
        f"Embedding: [cyan]{cfg.embedding_provider}[/cyan]"
        f" / {cfg.embedding_model} ({dims}d)"
    )
    console.print(f"Reranker:  [cyan]{cfg.rerank_provider}[/cyan] / {cfg.rerank_model}")


@main.command()
def init():
    """Interactive setup: choose providers and configure API keys."""
    probe_dir = _find_probe_dir(create=True)

    provider = detect_provider()
    if provider:
        console.print(f"[green]Auto-detected provider: {provider}[/green]")
        models = DEFAULT_MODELS[provider]
        cfg = ProbeConfig(
            embedding_provider=provider,
            embedding_model=models["embedding"],
            rerank_provider=provider if models["rerank"] else "zeroentropy",
            rerank_model=models["rerank"] or "zerank-2",
        )
    else:
        console.print("[yellow]No API keys found in environment.[/yellow]")
        console.print("Set one of: ZEROENTROPY_API_KEY, OPENAI_API_KEY, or COHERE_API_KEY")
        console.print("\nUsing default config (ZeroEntropy).")
        cfg = ProbeConfig()

    save_config(cfg, probe_dir / "config.yaml")
    console.print(f"\n[green]Config saved to {probe_dir / 'config.yaml'}[/green]")


@main.command()
def mcp():
    """Start the MCP server (stdio transport)."""
    from probe.mcp.server import run_mcp_server
    run_mcp_server()
