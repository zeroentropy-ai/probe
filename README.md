# probe

**Fast project context for AI coding agents.**

[![CI](https://github.com/zeroentropy-ai/probe/actions/workflows/ci.yml/badge.svg)](https://github.com/zeroentropy-ai/probe/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/probe-search.svg)](https://pypi.org/project/probe-search/)
[![Python](https://img.shields.io/pypi/pyversions/probe-search.svg)](https://pypi.org/project/probe-search/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

probe indexes your repo's docs and code, then gives Claude Code, Codex, and
other MCP agents a ranked search tool for project knowledge.

## Contents

- [Quick Start](#quick-start)
- [Claude Code](#claude-code)
- [Codex](#codex)
- [CLI and MCP](#cli-and-mcp)
- [Indexing](#indexing)
- [Commands](#commands)
- [Configuration](#configuration)
- [Data Handling](#data-handling)

## Quick Start

Get a free ZeroEntropy API key at <https://dashboard.zeroentropy.dev>.

### Claude Code

Run this inside Claude Code:

```text
/plugin marketplace add https://github.com/zeroentropy-ai/probe.git
/plugin install probe@zeroentropy
/reload-plugins
```

Claude Code asks for your ZeroEntropy API key during install. The plugin starts
probe with `uvx --from probe-search==0.4.9 probe mcp`, so you do not need to
install probe separately.

Use the HTTPS URL above. The `zeroentropy-ai/probe` shorthand makes Claude Code
clone over SSH, which requires a configured GitHub SSH key. The Claude Code
slash command treats `--sparse` as part of the URL, so do not pass sparse
checkout options there.

### Codex

Run this from a shell:

```bash
codex plugin marketplace add https://github.com/zeroentropy-ai/probe.git --sparse .agents/plugins --sparse plugins/probe-codex
codex plugin add probe@zeroentropy
export ZEROENTROPY_API_KEY="ze_xxx"
```

The Codex plugin starts probe with `uvx --from probe-search==0.4.9 probe mcp`.
Keep `ZEROENTROPY_API_KEY` in your shell before starting Codex, or use the
direct installer below to persist the key in Codex's MCP config.

For Codex auto-review, run this once:

```bash
uv tool install probe-search
probe install --client codex --approve-tools --allow-zeroentropy-network
```

This pre-approves probe's MCP tools and allows the narrow ZeroEntropy network
egress Codex needs for indexing and reranking.

## Plugin Install From A Shell

If you prefer shell commands, probe can install the plugin and direct MCP entry
for you:

```bash
uv tool install probe-search

probe install --client claude --plugin --api-key "$ZEROENTROPY_API_KEY"
probe install --client codex --plugin --api-key "$ZEROENTROPY_API_KEY" --approve-tools --allow-zeroentropy-network
```

If you use the `claude plugin install` shell command directly, pass the plugin
config explicitly:

```bash
claude plugin marketplace add https://github.com/zeroentropy-ai/probe.git --sparse .claude-plugin plugins
claude plugin install --config zeroentropy_api_key="$ZEROENTROPY_API_KEY" probe@zeroentropy
```

If Claude says `--config not applied`, open Claude Code and run
`/plugin configure probe@zeroentropy`, or use `probe install --client claude
--plugin --api-key "$ZEROENTROPY_API_KEY"` so the direct MCP entry also has the
key.

## CLI and MCP

For CLI-only use:

```bash
pip install probe-search
export ZEROENTROPY_API_KEY="ze_xxx"

probe index .
probe search "how does authentication work"
```

For direct Claude Code or Codex MCP registration:

```bash
uv tool install probe-search
export ZEROENTROPY_API_KEY="ze_xxx"

probe install --client claude
probe install --client codex
```

`probe install` registers direct MCP by default. It installs the Claude Code or
Codex plugin only when you pass `--plugin`.

For custom Codex installs:

```bash
probe install --client codex --codex-home ~/.codex-work --codex-bin /path/to/codex
probe doctor --codex-home ~/.codex-work --codex-bin /path/to/codex
```

For another MCP-compatible agent, add this to `.mcp.json`:

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

Start probe from the project root, or set the MCP server working directory to
the project root. Claude Code sets `CLAUDE_PROJECT_DIR` automatically.

## Verify Setup

```bash
probe doctor
probe smoke
```

`probe doctor` checks the API key, Claude Code/Codex wiring, MCP registration,
and local index health without printing secrets. `probe smoke` indexes a tiny
sample project and confirms search works. Use `probe smoke --current`,
`probe smoke --claude`, or `probe smoke --codex` for deeper checks.

## Indexing

probe indexes text-like files and PDFs. It does not rely on a fixed extension
allowlist, so files such as `Makefile`, `Dockerfile`, and local config files
are indexed when they look like text.

Discovery respects nested `.gitignore` files. `.ignore` has higher precedence
than `.gitignore`, which lets you keep files out of Git while still letting
probe index them. Use `.probeignore` for probe-specific exclusions.

probe always skips `.git/`, `.probe/`, `__pycache__/`, `.venv/`, compiled
Python files, obvious binary artifacts, and likely secret files such as
`.env*`, `*.pem`, `*.key`, `.npmrc`, `.pypirc`, and private SSH keys. Set
`PROBE_INDEX_SECRET_FILES=1` only if you explicitly want those files indexed.

In MCP mode, the first `probe_search` builds the local `.probe/` index. After
that, CLI and MCP searches refresh added, changed, and deleted files before
search. Set `PROBE_REFRESH_TTL=0` to check before every search, or
`PROBE_REFRESH_TTL=-1` to disable automatic refresh.

If a file cannot be extracted or embedded, probe skips that file, reports it,
and continues indexing the rest of the repo. Existing chunks for that file stay
in place until the replacement chunks have been embedded successfully.

## How Search Works

1. probe chunks files with section, symbol, page, and line metadata.
2. It retrieves candidates with semantic vector search and SQLite FTS5.
3. It fuses and reranks results with ZeroEntropy `zerank-2`.
4. It returns focused file, section, and line references to the agent.

## MCP Tools

| Tool | Purpose |
|------|---------|
| `probe_search` | Search docs and code with refresh and reranking |
| `probe_index` | Index or re-index project files |
| `probe_status` | Show index status |
| `probe_read` | Read a file, optionally with line ranges |

MCP resources include `probe://status`, `probe://files`, and
`probe://file/{path}`.

## Commands

| Command | Description |
|---------|-------------|
| `probe index [paths...]` | Index project files |
| `probe index --full` | Force full re-index |
| `probe search "query"` | Search project knowledge |
| `probe search --json` | Emit machine-readable results |
| `probe status` | Show index stats and model config |
| `probe list` | List indexed files |
| `probe install --client claude` | Register direct MCP in Claude Code |
| `probe install --client codex` | Register direct MCP in Codex |
| `probe install --client codex --plugin` | Install Codex plugin and direct MCP |
| `probe install --client codex --approve-tools --allow-zeroentropy-network` | Configure Codex auto-review |
| `probe install --client codex --codex-home PATH --codex-bin PATH` | Use a custom Codex config or binary |
| `probe doctor` | Diagnose API key, agent wiring, and index health |
| `probe smoke` | Run an end-to-end search check |
| `probe smoke --claude` | Include Claude wiring in smoke check |
| `probe smoke --codex` | Include Codex wiring in smoke check |
| `probe mcp` | Start the MCP server |
| `probe uninstall --client claude` | Unregister Claude Code MCP |
| `probe uninstall --client codex` | Unregister Codex MCP |
| `probe uninstall --purge` | Unregister and delete `.probe/` |

For scripts:

```bash
probe search "how does authentication work" --json
probe status --json
probe doctor --json
probe smoke --json
```

## Configuration

probe stores its index and config in `.probe/` at the project root. Add
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

Useful environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `PROBE_REFRESH_TTL` | `2` | Seconds between refresh checks before search; `0` means every search, `-1` disables refresh |
| `PROBE_MAX_CHUNK_CHARS` | `2000` | Maximum characters per indexed chunk before splitting |
| `PROBE_EMBED_BATCH_MAX_CHUNKS` | `96` | Maximum chunks per embedding request |
| `PROBE_EMBED_BATCH_MAX_BYTES` | `4500000` | Maximum UTF-8 payload bytes per embedding request |
| `PROBE_INDEX_SECRET_FILES` | unset | Set to `1` to opt into indexing likely secret files |

## Data Handling

Project chunks and vectors are stored locally in `.probe/` with SQLite and
numpy. During indexing and search, probe sends query text and chunk text to
ZeroEntropy for embedding and reranking. It does not create a remote document
index; `.probe/` is the durable project index. Likely secret files are skipped
by default, but review `.probeignore` before indexing repositories with unusual
credential locations.

## Links

- [ZeroEntropy](https://www.zeroentropy.dev)
- [API Dashboard](https://dashboard.zeroentropy.dev)
- [ZeroEntropy API docs](https://docs.zeroentropy.dev)
- [zembed-1 and zerank-2](https://docs.zeroentropy.dev/models)

## License

MIT. See [LICENSE](LICENSE).
