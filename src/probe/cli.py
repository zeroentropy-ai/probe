"""CLI interface for probe."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

import probe
from probe.config import DEFAULT_MODELS, ProbeConfig, detect_provider, load_config, save_config
from probe.indexer.refresh_gate import RefreshGate

console = Console()
PROBE_DIR_NAME = ".probe"


def _find_probe_dir(create: bool = False) -> Path:
    probe_dir = Path.cwd() / PROBE_DIR_NAME
    if create:
        probe_dir.mkdir(exist_ok=True)
    return probe_dir


def _require_probe_dir() -> Path:
    """Find .probe/ dir or exit with helpful message."""
    probe_dir = Path.cwd() / PROBE_DIR_NAME
    if not probe_dir.exists():
        console.print(
            "[yellow]Not indexed yet. Run 'probe index' first.[/yellow]"
        )
        raise SystemExit(0)
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
    probe_dir = _require_probe_dir()
    db = ProbeDB(probe_dir / "probe.db")
    db.initialize()
    vector_store = VectorStore(probe_dir / "vectors.npy", dimensions=config.embedding_dimensions)
    vector_store.load()

    # Refresh-before-search: update index if files changed since last index.
    gate = RefreshGate.from_env()
    if gate.should_refresh():
        from probe.indexer.pipeline import IndexPipeline
        embedding_for_refresh, _ = _build_providers(config)
        pipeline = IndexPipeline(
            db=db, vector_store=vector_store,
            embedding_provider=embedding_for_refresh,
        )
        try:
            refresh_stats = pipeline.refresh_changed([Path.cwd()])
            gate.mark()
            total_changed = (
                refresh_stats["added"] + refresh_stats["changed"] + refresh_stats["removed"]
            )
            if total_changed > 0:
                console.print(
                    f"[dim]Refreshed: +{refresh_stats['added']} "
                    f"±{refresh_stats['changed']} -{refresh_stats['removed']} "
                    f"({refresh_stats['elapsed_ms']}ms)[/dim]"
                )
        except Exception as e:
            from rich.markup import escape
            console.print(
                f"[yellow]Warning: refresh failed ({escape(str(e))}); using stale index.[/yellow]"
            )

    # Note: providers are built twice on search — once for the refresh pass above
    # and once here for the search. Provider constructors are cheap; keeping the
    # two paths independent avoids having the refresh block reach into search state.
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

    probe_dir = _require_probe_dir()
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

    probe_dir = _require_probe_dir()
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


@main.command()
@click.option("--api-key", default=None, help="ZeroEntropy API key (skip prompt).")
@click.option("--no-embed-key", is_flag=True,
              help="Register without embedding API key (rely on shell env).")
@click.option("--force", is_flag=True, help="Skip already-installed confirmation.")
def install(api_key, no_embed_key, force):
    """Register probe as a user-scope MCP server in Claude Code."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        console.print(
            "[red]Claude Code CLI not found.[/red] "
            "Install it from the official Claude Code documentation, then rerun `probe install`."
        )
        sys.exit(1)

    # Check if already installed. `claude mcp get` doesn't accept --scope; it
    # searches across scopes, which is fine for our "already installed?" check.
    get_result = subprocess.run(
        [claude_bin, "mcp", "get", "probe"],
        capture_output=True,
    )
    if get_result.returncode == 0:
        if not force:
            if not click.confirm("probe is already registered. Reinstall?", default=False):
                console.print("No changes made.")
                return
        subprocess.run(
            [claude_bin, "mcp", "remove", "probe", "--scope", "user"],
            capture_output=True,
        )

    # Resolve API key
    resolved_key: str | None = None
    if not no_embed_key:
        if api_key:
            resolved_key = api_key
        else:
            env_key = os.environ.get("ZEROENTROPY_API_KEY")
            if env_key and click.confirm(
                "Use $ZEROENTROPY_API_KEY from environment?", default=True,
            ):
                resolved_key = env_key
            else:
                for _ in range(3):
                    entered = click.prompt(
                        "Enter your ZeroEntropy API key",
                        hide_input=True, default="", show_default=False,
                    )
                    if entered.strip():
                        resolved_key = entered.strip()
                        break
                else:
                    console.print("[red]API key required.[/red]")
                    sys.exit(1)

    # Resolve probe command + args
    probe_bin = shutil.which("probe")
    if probe_bin:
        probe_command = probe_bin
        probe_args = ["mcp"]
    else:
        probe_command = sys.executable
        probe_args = ["-m", "probe.cli", "mcp"]
        console.print(
            f"[yellow]Note: probe binary not on PATH; using {sys.executable} -m probe.cli. "
            "If you move this Python env, rerun `probe install`.[/yellow]"
        )

    # Build the JSON config. Using `claude mcp add-json` instead of
    # `claude mcp add` because the latter's -e flag is variadic and eats
    # the server-name positional in some arg orderings.
    mcp_config: dict = {
        "type": "stdio",
        "command": probe_command,
        "args": probe_args,
    }
    if resolved_key:
        mcp_config["env"] = {"ZEROENTROPY_API_KEY": resolved_key}

    add_cmd = [
        claude_bin, "mcp", "add-json", "--scope", "user", "probe",
        json.dumps(mcp_config),
    ]
    add_result = subprocess.run(add_cmd, capture_output=True)
    if add_result.returncode != 0:
        console.print(
            f"[red]claude mcp add-json failed:[/red]\n{add_result.stderr.decode(errors='replace')}"
        )
        sys.exit(1)

    console.print(
        "[green]✓ probe installed at user scope.[/green]\n"
        "  Open any project in Claude Code and ask a question — "
        "probe will auto-index on first search.\n"
        "  To uninstall: probe uninstall"
    )


@main.command()
@click.option("--purge", is_flag=True, help="Also delete .probe/ from cwd.")
def uninstall(purge):
    """Unregister probe from Claude Code."""
    claude_bin = shutil.which("claude")
    if claude_bin:
        subprocess.run(
            [claude_bin, "mcp", "remove", "probe", "--scope", "user"],
            capture_output=True,
        )
        # Ignore errors: "not found" is fine.

    if purge:
        probe_dir = Path.cwd() / ".probe"
        if probe_dir.exists():
            shutil.rmtree(probe_dir, ignore_errors=True)
            console.print(f"[dim]Deleted {probe_dir}[/dim]")

    console.print("[green]✓ probe uninstalled.[/green]")
