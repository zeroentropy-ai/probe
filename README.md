# probe

**Give your coding agent a brain beyond code.**

[![CI](https://github.com/zeroentropy-ai/probe/actions/workflows/ci.yml/badge.svg)](https://github.com/zeroentropy-ai/probe/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/probe-search.svg)](https://pypi.org/project/probe-search/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

probe gives Claude Code, Codex, and any MCP agent semantic search over your
repo's code and docs — so it finds the right context by *meaning*, not keywords,
and stops guessing.

## Why it matters

When your agent explores a codebase with `grep`, it only matches exact strings:

- It finds `getUser`, but misses the `fetch_account` that does the real work —
  so it never reads the code that matters.
- To "look around," it reloads whole files, burning its context window on noise.
- With no ranking, it grabs the first plausible match and hallucinates the rest.

probe returns ranked `file:line` references by meaning, auto-refreshed on every
search, so your agent reads the right code the first time.

## Results

We ran an agent (Claude Sonnet 4.6) on 8 real-world coding tasks, with and
without probe. With probe, the agent used it for all code exploration.

| | With probe | Without |
|---|---|---|
| Avg. test-pass rate | **93.1%** | 84.1% |
| `eicrud` (a hard task) | **100% — cracked it** | 44% |

probe lifted the average test-pass rate by ~9 points and cracked a task the
baseline couldn't. The honest tradeoff: it used **~39% more tokens**, because
search results stay in the agent's context across turns — probe's own
embedding/rerank API was negligible (**$0.80** across all 8 tasks).

<sub>Source: ZeroEntropy internal benchmark — 8 tasks graded to completion, Sonnet 4.6, seed 0.</sub>

## See it in action

<!-- TODO(GTM): embed Dilawar's videos (probe vs plain Claude Code). Need the links. -->
_Walkthrough videos coming soon._

## Quick Start

Get a free ZeroEntropy API key at <https://dashboard.zeroentropy.dev>, then, in
Claude Code:

```text
/plugin marketplace add https://github.com/zeroentropy-ai/probe.git
/plugin install probe@zeroentropy
```

Claude Code asks for your key during install and runs probe for you — nothing to
install separately. Ask a question about your repo and probe auto-indexes on the
first search.

<details>
<summary><b>Codex, CLI-only, and other MCP agents</b></summary>

### Codex

```bash
codex plugin marketplace add https://github.com/zeroentropy-ai/probe.git --sparse .agents/plugins --sparse plugins/probe-codex
codex plugin add probe@zeroentropy
export ZEROENTROPY_API_KEY="ze_xxx"
```

For Codex auto-review:

```bash
uv tool install probe-search
probe install --client codex --approve-tools --allow-zeroentropy-network
```

### CLI only

```bash
pip install probe-search
export ZEROENTROPY_API_KEY="ze_xxx"
probe index .
probe search "how does authentication work"
```

### Direct MCP registration

```bash
uv tool install probe-search
export ZEROENTROPY_API_KEY="ze_xxx"
probe install --client claude   # or: --client codex
```

For any other MCP client, add to `.mcp.json`:

```json
{
  "mcpServers": {
    "probe": {
      "command": "uvx",
      "args": ["--from", "probe-search", "probe", "mcp"],
      "env": { "ZEROENTROPY_API_KEY": "ze_xxx" }
    }
  }
}
```

Start probe from the project root, or set the MCP server working directory to
the project root. Claude Code sets `CLAUDE_PROJECT_DIR` automatically.

</details>

## How It Works

1. probe chunks files with section, symbol, page, and line metadata.
2. It retrieves candidates with semantic vector search and SQLite FTS5.
3. It fuses and reranks results with ZeroEntropy `zerank-2`.
4. It returns focused file, section, and line references to the agent.

Hybrid retrieval plus cross-encoder reranking is why probe surfaces the code that
keyword search walks right past.

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

<details>
<summary><b>All commands</b></summary>

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

</details>

## Configuration

<details>
<summary><b>Configuration reference</b></summary>

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

</details>

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
