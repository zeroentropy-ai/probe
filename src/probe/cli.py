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


def _search_result_to_dict(result):
    return {
        "score": result.score,
        "file": result.file,
        "type": result.file_type,
        "header_path": result.header_path,
        "symbol": result.symbol_name,
        "page": result.page_number,
        "content": result.content,
        "char_range": list(result.char_range),
        "line_start": result.line_start,
        "line_end": result.line_end,
    }


def _search_response_to_dict(response, refreshed=None):
    data = {
        "query": response.query,
        "results": [_search_result_to_dict(result) for result in response.results],
        "total_tokens": response.total_tokens,
        "sources_searched": response.sources_searched,
    }
    if refreshed is not None:
        data["refreshed"] = refreshed
    return data


def _print_json(data) -> None:
    click.echo(json.dumps(data, indent=2))


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
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON")
def search(query, top_k, max_tokens, file_types, no_rerank, json_output):
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
    refreshed_info = None

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
            refreshed_info = refresh_stats
            if total_changed > 0 and not json_output:
                console.print(
                    f"[dim]Refreshed: +{refresh_stats['added']} "
                    f"±{refresh_stats['changed']} -{refresh_stats['removed']} "
                    f"({refresh_stats['elapsed_ms']}ms)[/dim]"
                )
        except Exception as e:
            from rich.markup import escape
            refreshed_info = {
                "added": 0, "changed": 0, "removed": 0, "elapsed_ms": 0,
                "error": str(e),
            }
            if not json_output:
                console.print(
                    f"[yellow]Warning: refresh failed ({escape(str(e))}); "
                    "using stale index.[/yellow]"
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

    if json_output:
        data = _search_response_to_dict(response, refreshed=refreshed_info)
        data["elapsed_seconds"] = round(elapsed, 3)
        _print_json(data)
        db.close()
        return

    if not response.results:
        console.print("[yellow]No results found.[/yellow]")
        db.close()
        return

    console.print(f"\n [bold]Found {len(response.results)} results[/bold] "
                  f"({response.sources_searched} chunks searched)\n")

    for result in response.results:
        score_color = "green" if result.score > 0.7 else "yellow" if result.score > 0.4 else "dim"
        score_str = f"[{score_color}][{result.score:.2f}][/{score_color}]"

        line_suffix = ""
        if result.line_start is not None:
            line_suffix = f":{result.line_start}"
            if result.line_end and result.line_end != result.line_start:
                line_suffix += f"-{result.line_end}"

        loc = f"[cyan]{result.file}{line_suffix}[/cyan]"
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
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON")
def status(json_output):
    """Show index status and configuration."""
    from probe.store.database import ProbeDB

    probe_dir = _require_probe_dir()
    config = _get_config()
    db = ProbeDB(probe_dir / "probe.db")
    db.initialize()
    stats = db.get_stats()

    if json_output:
        _print_json({
            **stats,
            "providers": {
                "embedding": f"{config.embedding_provider}/{config.embedding_model}",
                "reranker": f"{config.rerank_provider}/{config.rerank_model}",
            },
        })
        db.close()
        return

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


@main.command()
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON")
@click.option("--strict", is_flag=True, help="Treat optional warnings as failures")
@click.option("--no-network", is_flag=True, help="Skip network reachability checks")
def doctor(json_output, strict, no_network):
    """Check local probe, API key, index, Claude Code, and Codex setup."""
    from probe.diagnostics import FAIL, PASS, WARN, run_doctor

    report = run_doctor(strict=strict, no_network=no_network)
    if json_output:
        _print_json(report.to_dict())
    else:
        table = Table(title="probe doctor")
        table.add_column("Status", style="bold")
        table.add_column("Check", style="cyan")
        table.add_column("Detail")
        table.add_column("Fix")
        styles = {PASS: "green", WARN: "yellow", FAIL: "red"}
        for check in report.checks:
            table.add_row(
                f"[{styles.get(check.status, 'white')}]{check.status}[/]",
                check.name,
                check.detail,
                check.fix,
            )
        console.print(table)

    if report.status == FAIL:
        raise SystemExit(1)


@main.command()
@click.option("--current", is_flag=True, help="Smoke-test the current project")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON")
@click.option("--keep", is_flag=True, help="Keep the temporary sample project")
@click.option("--claude", is_flag=True, help="Also validate local Claude wiring")
@click.option("--codex", is_flag=True, help="Also validate local Codex wiring")
def smoke(current, json_output, keep, claude, codex):
    """Run an end-to-end indexing and search validation."""
    from probe.smoke import run_smoke

    report = run_smoke(current=current, keep=keep, claude=claude, codex=codex)
    if json_output:
        _print_json(report.to_dict())
    else:
        if report.status == "PASS":
            console.print("[green]PASS[/green] probe smoke succeeded")
            console.print(f"  Project: {report.project_path}")
            console.print(f"  Indexed: {report.indexed_files} files, {report.chunks} chunks")
            console.print(f"  Search results: {report.search_result_count}")
            if report.temp_project_kept:
                console.print("  Temp project kept for inspection.")
        else:
            console.print("[red]FAIL[/red] probe smoke failed")
            if report.error:
                console.print(f"  {report.error}")

    if report.status != "PASS":
        raise SystemExit(1)


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


def _enable_probe_in_all_projects() -> int:
    """Remove "probe" from every project's disabledMcpServers list in ~/.claude.json.

    Claude Code stores per-project MCP enable/disable state there; a newly-added
    user-scope MCP server can appear as disabled in some projects. This helper
    is a narrowly-scoped post-install cleanup so users don't have to toggle
    probe on per-project via /mcp.

    Returns the number of projects modified. Silently returns 0 on missing file;
    prints a yellow warning on malformed JSON or write failure but never raises.
    """
    claude_json_path = Path.home() / ".claude.json"
    if not claude_json_path.exists():
        return 0

    try:
        data = json.loads(claude_json_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        console.print(
            f"[yellow]Warning: could not parse {claude_json_path} ({e}); "
            "probe may need to be enabled manually via /mcp in Claude Code.[/yellow]"
        )
        return 0

    projects = data.get("projects")
    if not isinstance(projects, dict):
        return 0

    modified = 0
    for _proj_path, proj_data in projects.items():
        if not isinstance(proj_data, dict):
            continue
        disabled = proj_data.get("disabledMcpServers")
        if isinstance(disabled, list) and "probe" in disabled:
            proj_data["disabledMcpServers"] = [s for s in disabled if s != "probe"]
            modified += 1

    if modified == 0:
        return 0

    # Atomic write: temp file in same dir + os.replace
    tmp_path = claude_json_path.with_suffix(".json.probe-tmp")
    try:
        tmp_path.write_text(json.dumps(data, indent=2))
        os.replace(tmp_path, claude_json_path)
    except OSError as e:
        console.print(
            f"[yellow]Warning: could not rewrite {claude_json_path} ({e}); "
            "probe may need to be enabled manually via /mcp.[/yellow]"
        )
        # Best-effort cleanup of tmp file
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return 0

    return modified


def _resolve_zeroentropy_key(api_key: str | None, no_embed_key: bool) -> str | None:
    if no_embed_key:
        return None
    if api_key:
        return api_key

    env_key = os.environ.get("ZEROENTROPY_API_KEY")
    if env_key and click.confirm("Use $ZEROENTROPY_API_KEY from environment?", default=True):
        return env_key

    for _ in range(3):
        entered = click.prompt(
            "Enter your ZeroEntropy API key",
            hide_input=True, default="", show_default=False,
        )
        if entered.strip():
            return entered.strip()

    console.print("[red]API key required.[/red]")
    sys.exit(1)


def _resolve_probe_command() -> tuple[str, list[str]]:
    probe_bin = shutil.which("probe")
    if probe_bin:
        return probe_bin, ["mcp"]

    console.print(
        f"[yellow]Note: probe binary not on PATH; using {sys.executable} -m probe.cli. "
        "If you move this Python env, rerun `probe install`.[/yellow]"
    )
    return sys.executable, ["-m", "probe.cli", "mcp"]


def _install_claude(api_key: str | None, no_embed_key: bool, force: bool) -> bool:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        console.print(
            "[red]Claude Code CLI not found.[/red] "
            "Install it from the official Claude Code documentation, then rerun "
            "`probe install --client claude`."
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
            if not click.confirm(
                "probe is already registered in Claude Code. Reinstall?", default=False,
            ):
                console.print("No changes made.")
                return False
        subprocess.run(
            [claude_bin, "mcp", "remove", "probe", "--scope", "user"],
            capture_output=True,
        )

    resolved_key = _resolve_zeroentropy_key(api_key, no_embed_key)
    probe_command, probe_args = _resolve_probe_command()

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
        "[green]✓ probe installed for Claude Code at user scope.[/green]\n"
        "  Open any project in Claude Code and ask a question — "
        "probe will auto-index on first search.\n"
        "  To uninstall: probe uninstall"
    )

    # Auto-enable probe in any project that had it on its disabledMcpServers list.
    n_enabled = _enable_probe_in_all_projects()
    if n_enabled > 0:
        console.print(f"[dim]  Enabled probe in {n_enabled} project(s) that had it disabled.[/dim]")
    return True


def _configure_codex_auto_review(
    approve_tools: bool,
    allow_zeroentropy_network: bool,
) -> None:
    if not (approve_tools or allow_zeroentropy_network):
        return

    from probe.codex_config import configure_codex_probe_auto_review

    changes = configure_codex_probe_auto_review(
        approve_tools=approve_tools,
        allow_zeroentropy_network=allow_zeroentropy_network,
    )
    console.print(
        "[green]✓ Codex auto-review can use probe without per-call approval.[/green]"
    )
    for change in changes:
        console.print(f"[dim]  Configured {change}.[/dim]")


def _install_codex(
    api_key: str | None,
    no_embed_key: bool,
    force: bool,
    approve_tools: bool = False,
    allow_zeroentropy_network: bool = False,
) -> bool:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        console.print(
            "[red]Codex CLI not found.[/red] "
            "Install Codex, then rerun `probe install --client codex`."
        )
        sys.exit(1)

    get_result = subprocess.run([codex_bin, "mcp", "get", "probe"], capture_output=True)
    if get_result.returncode == 0:
        if not force:
            if not click.confirm("probe is already registered in Codex. Reinstall?", default=False):
                _configure_codex_auto_review(approve_tools, allow_zeroentropy_network)
                if approve_tools or allow_zeroentropy_network:
                    return True
                console.print("No changes made.")
                return False
        subprocess.run([codex_bin, "mcp", "remove", "probe"], capture_output=True)

    resolved_key = _resolve_zeroentropy_key(api_key, no_embed_key)
    probe_command, probe_args = _resolve_probe_command()
    add_cmd = [codex_bin, "mcp", "add", "probe"]
    if resolved_key:
        add_cmd.extend(["--env", f"ZEROENTROPY_API_KEY={resolved_key}"])
    add_cmd.extend(["--", probe_command, *probe_args])

    add_result = subprocess.run(add_cmd, capture_output=True)
    if add_result.returncode != 0:
        console.print(
            f"[red]codex mcp add failed:[/red]\n{add_result.stderr.decode(errors='replace')}"
        )
        sys.exit(1)

    console.print(
        "[green]✓ probe installed for Codex.[/green]\n"
        "  Open any project in Codex and ask a question — "
        "probe will auto-index on first search.\n"
        "  To uninstall: probe uninstall --client codex"
    )
    _configure_codex_auto_review(approve_tools, allow_zeroentropy_network)
    return True


@main.command()
@click.option(
    "--client",
    type=click.Choice(["claude", "codex", "both"]),
    default="claude",
    show_default=True,
    help="Agent client to configure.",
)
@click.option("--api-key", default=None, help="ZeroEntropy API key (skip prompt).")
@click.option("--no-embed-key", is_flag=True,
              help="Register without embedding API key (rely on shell env).")
@click.option("--force", is_flag=True, help="Skip already-installed confirmation.")
@click.option(
    "--approve-tools",
    is_flag=True,
    help="For Codex: pre-approve probe MCP tools so auto-review does not block them.",
)
@click.option(
    "--allow-zeroentropy-network",
    is_flag=True,
    help="For Codex: allow api.zeroentropy.dev network access for probe indexing/reranking.",
)
def install(client, api_key, no_embed_key, force, approve_tools, allow_zeroentropy_network):
    """Register probe as a user-scope MCP server in Claude Code or Codex."""
    if client == "claude" and approve_tools:
        raise click.UsageError("--approve-tools requires --client codex or --client both")
    if client == "claude" and allow_zeroentropy_network:
        raise click.UsageError(
            "--allow-zeroentropy-network requires --client codex or --client both"
        )

    if client == "both":
        resolved_key = _resolve_zeroentropy_key(api_key, no_embed_key)
        _install_claude(resolved_key, no_embed_key, force)
        _install_codex(
            resolved_key,
            no_embed_key,
            force,
            approve_tools=approve_tools,
            allow_zeroentropy_network=allow_zeroentropy_network,
        )
        return

    if client in {"claude", "both"}:
        _install_claude(api_key, no_embed_key, force)
    if client in {"codex", "both"}:
        _install_codex(
            api_key,
            no_embed_key,
            force,
            approve_tools=approve_tools,
            allow_zeroentropy_network=allow_zeroentropy_network,
        )


@main.command()
@click.option(
    "--client",
    type=click.Choice(["claude", "codex", "both"]),
    default="claude",
    show_default=True,
    help="Agent client to unregister.",
)
@click.option("--purge", is_flag=True, help="Also delete .probe/ from cwd.")
def uninstall(client, purge):
    """Unregister probe from Claude Code or Codex."""
    claude_bin = shutil.which("claude")
    if client in {"claude", "both"} and claude_bin:
        subprocess.run(
            [claude_bin, "mcp", "remove", "probe", "--scope", "user"],
            capture_output=True,
        )
        # Ignore errors: "not found" is fine.

    codex_bin = shutil.which("codex")
    if client in {"codex", "both"} and codex_bin:
        subprocess.run(
            [codex_bin, "mcp", "remove", "probe"],
            capture_output=True,
        )
        # Ignore errors: "not found" is fine.

    if purge:
        probe_dir = Path.cwd() / ".probe"
        if probe_dir.exists():
            shutil.rmtree(probe_dir, ignore_errors=True)
            console.print(f"[dim]Deleted {probe_dir}[/dim]")

    console.print("[green]✓ probe uninstalled.[/green]")
