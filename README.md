# probe

**Give your AI agent a brain beyond code.**

[![CI](https://github.com/zeroentropy-ai/probe/actions/workflows/ci.yml/badge.svg)](https://github.com/zeroentropy-ai/probe/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/probe-search.svg)](https://pypi.org/project/probe-search/)
[![Python](https://img.shields.io/pypi/pyversions/probe-search.svg)](https://pypi.org/project/probe-search/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

probe is a CLI tool and MCP server that indexes your project's docs and code,
then serves curated, reranked context to AI coding agents in milliseconds.

---

## Contents

- [Why probe](#why-probe)
- [Quick Start](#quick-start)
- [MCP Server Setup](#mcp-server-setup-claude-code-cursor)
- [Indexing](#indexing)
- [How It Works](#how-it-works)
- [Example Output](#example-output)
- [CLI Reference](#cli-reference)
- [Configuration](#configuration)
- [Data Handling](#data-handling)

---

## Why probe

AI coding agents often answer project questions by grepping, reading files one
by one, and assembling context over several tool calls. probe gives them one
ranked search across docs and code.

What you get:

- Claude Code plugin with bundled MCP server and usage skill
- Manual Claude Code setup with `probe install`
- First-use indexing and incremental refresh-before-search
- Hybrid keyword + semantic retrieval across docs, code, text, and PDFs
- Cross-encoder reranking with ZeroEntropy `zerank-2`
- Local index storage in `.probe/`
- MCP tools for Claude Code, Cursor, and other MCP-compatible agents

---

## Quick Start

### Claude Code plugin (recommended)

Get a free ZeroEntropy API key at <https://dashboard.zeroentropy.dev>. Then run
inside Claude Code:

```text
/plugin marketplace add zeroentropy-ai/probe --sparse .claude-plugin plugins
/plugin install probe@zeroentropy
/reload-plugins
```

Claude Code asks for your ZeroEntropy API key during plugin install. The plugin
starts probe with `uvx --from probe-search==0.3.0 probe mcp`, so Claude Code
does not need a separate probe install.

If you use the `claude plugin install` shell command instead of the `/plugin`
slash command, export `ZEROENTROPY_API_KEY` before starting Claude Code.

### Manual CLI/MCP setup

```bash
uv tool install probe-search
# or: pipx install probe-search

export ZEROENTROPY_API_KEY="ze_xxx"
probe install
claude mcp list
```

Use `uv tool install` or `pipx` so the registered `probe` path stays valid. If
you install probe in a temporary virtual environment, rerun `probe install`
after recreating that environment.

For CLI-only use:

```bash
pip install probe-search
export ZEROENTROPY_API_KEY="ze_xxx"
probe index .
probe search "how does authentication work"
```

### Verify your setup

```bash
probe doctor
probe smoke
```

`probe doctor` checks your API key, Claude Code wiring, MCP registration, and
local index health without printing secrets or uploading project content.
`probe smoke` indexes a tiny sample project and confirms search works. Use
`probe smoke --current` to validate the current repo.

---

## MCP Server Setup (Claude Code, Cursor)

The Claude Code plugin is the best default. For Cursor or advanced use, add a
`.mcp.json` file to your project root:

```json
{
  "mcpServers": {
    "probe": {
      "command": "uvx",
      "args": ["--from", "probe-search", "probe", "mcp"],
      "env": {
        "ZEROENTROPY_API_KEY": "ze_xxx"
      }
    }
  }
}
```

Your agent gets four tools:

| Tool | What it does |
|------|-------------|
| `probe_search` | Search docs and code with automatic refresh and reranking |
| `probe_index` | Index or re-index project files |
| `probe_status` | Show what's indexed |
| `probe_read` | Read a file, optionally with focused line ranges |

---

## Indexing

probe indexes Markdown, MDX, plain text, reStructuredText, AsciiDoc, TeX, YAML,
JSON, PDFs, and code in Python, JavaScript, TypeScript, TSX, JSX, Go, Rust, and
Java.

File discovery respects root `.gitignore` and `.probeignore` files. It also
skips `.git/`, `.probe/`, `__pycache__/`, `.venv/`, and `*.pyc`.

On first search, probe creates the local `.probe/` index automatically. Before
later CLI or MCP searches, it checks for added, changed, and deleted files, then
refreshes only affected chunks. Set `PROBE_REFRESH_TTL=0` to check before every
search, or `PROBE_REFRESH_TTL=-1` to disable automatic refresh.

Chunks keep Markdown header paths, code symbol names, PDF page numbers, and
line ranges so agents can cite focused source locations.

---

## How It Works

1. **Hybrid retrieval**: each query uses semantic vector search and SQLite FTS5
   keyword search.
2. **Reranking**: candidates are fused and reranked with ZeroEntropy `zerank-2`.
3. **Context assembly**: results are deduplicated, trimmed to your token budget,
   and returned with file, section, and line metadata.

---

## Example Output

```text
$ probe search "how does authentication work"

 Found 5 results (342 chunks searched)

 [0.94] docs/design/auth.md > Authentication > OAuth Flow
   We use PKCE-based OAuth 2.0 with Auth0 as the identity provider.
   The flow works as follows: 1) Client generates a code verifier...

 [0.87] src/auth/oauth.py:42-71 > class OAuthHandler
   class OAuthHandler:
       """Handles OAuth2 PKCE flow for web and mobile clients."""
       def __init__(self, client_id: str, redirect_uri: str):

 ------------------------------------------
 zembed-1 + zerank-2 | 1,847 tokens | 0.3s
```

For scripts and agents:

```bash
probe search "how does authentication work" --json
probe status --json
probe doctor --json
probe smoke --json
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `probe index [paths...]` | Index project files for semantic search |
| `probe index --full` | Force full re-index |
| `probe install` | Register probe as a user-scope MCP server in Claude Code |
| `probe search "query"` | Search project knowledge with natural language |
| `probe search --top-k N` | Limit number of results |
| `probe search --type code` | Filter by file type |
| `probe search --no-rerank` | Skip reranking |
| `probe search --max-tokens N` | Set result token budget |
| `probe search --json` | Emit machine-readable results with line ranges |
| `probe status` | Show index stats and model config |
| `probe status --json` | Emit machine-readable index status |
| `probe list` | List indexed files |
| `probe config` | Show current model configuration |
| `probe init` | Create local config from environment |
| `probe doctor` | Diagnose API key, Claude Code, MCP, and index setup |
| `probe doctor --json` | Emit machine-readable diagnostics |
| `probe smoke` | Run an end-to-end indexing and search validation |
| `probe smoke --current` | Smoke-test the current project |
| `probe mcp` | Start MCP server |
| `probe uninstall [--purge]` | Unregister probe; `--purge` also deletes `.probe/` |

---

## Configuration

probe stores its index and config in `.probe/` at your project root. Add
`.probe/` to `.gitignore`.

```yaml
# .probe/config.yaml
providers:
  embedding:
    name: zeroentropy
    model: zembed-1
    dimensions: 1280
  reranker:
    name: zeroentropy
    model: zerank-2
```

---

## Data Handling

Documents are chunked and stored locally in `.probe/` with SQLite and numpy.
Only chunk text is sent to the embedding and reranking API for processing.
Documents are never uploaded or stored on an external server.

---

## Links

- [ZeroEntropy](https://www.zeroentropy.dev) -- the AI retrieval engine powering probe
- [API Dashboard](https://dashboard.zeroentropy.dev) -- get your API key
- [Documentation](https://docs.zeroentropy.dev) -- ZeroEntropy API docs
- [Models](https://docs.zeroentropy.dev/models) -- zembed-1 and zerank-2 details

---

## License

MIT -- see [LICENSE](LICENSE) for details.
