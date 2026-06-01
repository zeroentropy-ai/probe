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
- [MCP Server Setup](#mcp-server-setup)
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

- Claude Code and Codex plugins with bundled MCP server and usage skill
- Manual Claude Code and Codex setup with `probe install`
- MCP first-use indexing and incremental refresh-before-search
- Hybrid keyword + semantic retrieval across text files, code, docs, and PDFs
- Cross-encoder reranking with ZeroEntropy `zerank-2`
- Local index storage in `.probe/`
- MCP tools and resources for Claude Code, Codex, and other MCP-compatible agents

---

## Quick Start

### Claude Code plugin (recommended)

Get a free ZeroEntropy API key at <https://dashboard.zeroentropy.dev>. Then run
inside Claude Code:

```text
/plugin marketplace add https://github.com/zeroentropy-ai/probe.git
/plugin install probe@zeroentropy
/reload-plugins
```

Use the HTTPS URL above. The `zeroentropy-ai/probe` shorthand makes Claude Code
clone over SSH, which requires a configured GitHub SSH key. The Claude Code
slash command treats `--sparse` as part of the URL, so do not pass sparse
checkout options there.

Claude Code asks for your ZeroEntropy API key during plugin install. The plugin
starts probe with `uvx --from probe-search==0.4.6 probe mcp`, so Claude Code
does not need a separate probe install.

If you use the `claude plugin install` shell command instead of the `/plugin`
slash command, export `ZEROENTROPY_API_KEY` before starting Claude Code. From a
shell, sparse checkout options are supported:

```bash
claude plugin marketplace add https://github.com/zeroentropy-ai/probe.git --sparse .claude-plugin plugins
claude plugin install probe@zeroentropy
```

### Codex plugin

Get a free ZeroEntropy API key at <https://dashboard.zeroentropy.dev>. Then run
from a shell:

```bash
codex plugin marketplace add https://github.com/zeroentropy-ai/probe.git --sparse .agents/plugins --sparse plugins/probe-codex
codex plugin add probe@zeroentropy
export ZEROENTROPY_API_KEY="ze_xxx"
```

The Codex plugin starts probe with `uvx --from probe-search==0.4.6 probe mcp`.
Keep `ZEROENTROPY_API_KEY` in your shell environment before starting Codex, or
run the direct installer below to persist the key in Codex's MCP config.

For Codex auto-review workflows, pre-approve probe's MCP tools and allow
ZeroEntropy network egress once:

```bash
uv tool install probe-search
probe install --client codex --approve-tools --allow-zeroentropy-network
```

This writes a narrow Codex config allowlist for ZeroEntropy API access and
package downloads, then pre-approves the probe MCP tools so auto-review does
not block each search.

### Direct CLI/MCP setup

```bash
uv tool install probe-search
# or: pipx install probe-search

export ZEROENTROPY_API_KEY="ze_xxx"
probe install --client claude
probe install --client codex
claude mcp list
codex mcp list
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

`probe doctor` checks your API key, Claude Code/Codex wiring, MCP registration, and
local index health without printing secrets or uploading project content.
`probe smoke` indexes a tiny sample project and confirms search works. Use
`probe smoke --current` to validate the current repo, `probe smoke --claude`
to validate Claude wiring, and `probe smoke --codex` to validate Codex wiring.

---

## MCP Server Setup

The Claude Code and Codex plugins are the best defaults. For another
MCP-compatible agent, add a `.mcp.json` file to your project root:

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

It also gets MCP resources:

| Resource | What it exposes |
|----------|-----------------|
| `probe://status` | Index status and provider configuration |
| `probe://files` | Indexed file list |
| `probe://file/{path}` | Read a project file by URL-encoded path |

When Claude Code starts probe, probe uses `CLAUDE_PROJECT_DIR` as the project
root. Other agents should start probe from the project root or set their MCP
server working directory to the project root.

---

## Indexing

probe indexes all text-like files and PDFs, not just a fixed extension list.
Unknown extensions and extensionless files such as `Makefile`, `Dockerfile`, and
local config files are indexed if they look like text.

File discovery respects nested `.gitignore` files. It also reads `.ignore`
files with higher precedence than `.gitignore`, which lets you keep files out
of Git while still letting probe index them. Use `.probeignore` for
probe-specific exclusions. probe always skips `.git/`, `.probe/`,
`__pycache__/`, `.venv/`, compiled Python files, and obvious binary artifacts.

In MCP mode, the first `probe_search` creates the local `.probe/` index
automatically. For CLI-only use, run `probe index .` once. After `.probe/`
exists, CLI and MCP searches check for added, changed, and deleted files, then
refresh only affected chunks. Set `PROBE_REFRESH_TTL=0` to check before every
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
| `probe install --client claude` | Register probe as a user-scope MCP server in Claude Code |
| `probe install --client codex` | Register probe as an MCP server in Codex |
| `probe install --client codex --approve-tools --allow-zeroentropy-network` | Configure Codex auto-review to allow probe tool calls and ZeroEntropy egress |
| `probe install --client both` | Register probe in Claude Code and Codex |
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
| `probe doctor` | Diagnose API key, Claude Code, Codex, MCP, and index setup |
| `probe doctor --json` | Emit machine-readable diagnostics |
| `probe smoke` | Run an end-to-end indexing and search validation |
| `probe smoke --current` | Smoke-test the current project |
| `probe smoke --claude` | Smoke-test search and Claude wiring |
| `probe smoke --codex` | Smoke-test search and Codex wiring |
| `probe mcp` | Start MCP server |
| `probe uninstall --client claude` | Unregister probe from Claude Code |
| `probe uninstall --client codex` | Unregister probe from Codex |
| `probe uninstall --purge` | Unregister probe and delete `.probe/` |

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

Project files are chunked and stored locally in `.probe/` with SQLite and
numpy. During indexing and search, probe sends query text and chunk text to
ZeroEntropy for embedding and reranking. It does not create a remote document
index; `.probe/` is the durable project index.

---

## Links

- [ZeroEntropy](https://www.zeroentropy.dev) -- the AI retrieval engine powering probe
- [API Dashboard](https://dashboard.zeroentropy.dev) -- get your API key
- [Documentation](https://docs.zeroentropy.dev) -- ZeroEntropy API docs
- [Models](https://docs.zeroentropy.dev/models) -- zembed-1 and zerank-2 details

---

## License

MIT -- see [LICENSE](LICENSE) for details.
