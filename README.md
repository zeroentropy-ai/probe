# probe

**Give your AI agent a brain beyond code.**

[![CI](https://github.com/zeroentropy-ai/probe/actions/workflows/ci.yml/badge.svg)](https://github.com/zeroentropy-ai/probe/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/probe-search.svg)](https://pypi.org/project/probe-search/)
[![Python](https://img.shields.io/pypi/pyversions/probe-search.svg)](https://pypi.org/project/probe-search/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

probe is a CLI tool and MCP server that indexes your project's documentation, specs, and code, then serves curated, reranked context to AI coding agents in milliseconds.

---

## The Problem

AI coding agents waste time searching. When you ask Claude Code or Cursor "how does our auth flow work?", they `grep` through files, read them one by one, and piece together an answer over multiple tool calls. On a large project with docs, specs, ADRs, and runbooks, this takes 30-60 seconds and often misses the most relevant context entirely.

probe makes this instant. It indexes everything -- markdown docs, code, PDFs, plain text -- into a hybrid search engine powered by [ZeroEntropy's](https://www.zeroentropy.dev) state-of-the-art embedding and reranking models. One query, one API call, sub-second results with the best context from docs AND code ranked together.

---

## What You Get

- One-command Claude Code setup with `probe install`
- Automatic first-use indexing and refresh-before-search
- Hybrid keyword + semantic retrieval across docs, code, text, and PDFs
- Cross-encoder reranking with `zerank-2`
- Local index storage in `.probe/`
- MCP tools for Claude Code, Cursor, and other MCP-compatible agents

---

## Quick Start

```bash
# 1. Get a free API key at https://dashboard.zeroentropy.dev
# 2. Install probe as a durable command
uv tool install probe-search
# or: pipx install probe-search

# 3. Set your API key
export ZEROENTROPY_API_KEY="ze_xxx"

# 4. Register probe with Claude Code (one-time, machine-wide)
probe install

# 5. Verify Claude Code sees probe
claude mcp get probe

# Now open any project in Claude Code and ask a question —
# probe will auto-index on first search and refresh on subsequent ones.
```

`probe install` stores the path to the `probe` executable in Claude Code. Use
`uv tool install` or `pipx` so that path stays valid. If you install probe in a
temporary virtual environment, rerun `probe install` after recreating that
environment.

Claude Code may ask you to approve probe the first time it calls `probe_search`.
Allow it once, and future searches will use the same MCP server.

probe stores local index data in `.probe/`; add `.probe/` to your project's
`.gitignore`.

For CLI-only use:

```bash
pip install probe-search
export ZEROENTROPY_API_KEY="ze_xxx"
probe index .
probe search "how does authentication work"
```

The index auto-refreshes before each search; set `PROBE_REFRESH_TTL=0` to
force refresh every time, or `-1` to disable refresh.

---

## MCP Server Setup (Claude Code, Cursor)

**Claude Code users**: `probe install` (see Quick Start) does this automatically.

**For Cursor or advanced use**: add a `.mcp.json` file to your project root:

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

This works with Claude Code, Cursor, and any MCP-compatible agent. No `pip install` required -- `uvx` handles it.

On first use, probe automatically indexes your project. Your agent may ask you
to approve the probe tool before the first search.

Your agent gains four tools:

| Tool | What it does |
|------|-------------|
| `probe_search` | Semantic search across docs and code with reranking |
| `probe_index` | Index or re-index project files |
| `probe_status` | Show what's indexed |
| `probe_read` | Read a specific file by path |

---

## How It Works

**1. Hybrid Retrieval**
Each query runs through two parallel search paths: semantic vector search (cosine similarity against [zembed-1](https://www.zeroentropy.dev/articles/introducing-zembed-1-the-worlds-best-multilingual-text-embedding-model) embeddings) and keyword search (BM25 via SQLite FTS5). This catches both conceptual matches and exact term hits.

**2. Cross-Source Reranking**
Results from both paths are fused using Reciprocal Rank Fusion, then passed through [zerank-2](https://www.zeroentropy.dev/articles/zerank-2-advanced-instruction-following-multilingual-reranker), a neural cross-encoder reranker that scores each chunk against the original query. Docs and code are ranked together -- the best answer wins regardless of file type.

**3. Smart Context Assembly**
Results are deduplicated, trimmed to your token budget, and returned with source citations. Each result includes its file path, location metadata (header paths for markdown, symbol names for code, page numbers for PDFs), and a relevance score.

---

## Example Output

```
$ probe search "how does authentication work"

 Found 5 results (342 chunks searched)

 [0.94] docs/design/auth.md > Authentication > OAuth Flow
   We use PKCE-based OAuth 2.0 with Auth0 as the identity provider.
   The flow works as follows: 1) Client generates a code verifier
   and challenge, 2) User is redirected to Auth0's /authorize...

 [0.87] src/auth/oauth.py > class OAuthHandler
   class OAuthHandler:
       """Handles OAuth2 PKCE flow for web and mobile clients."""
       def __init__(self, client_id: str, redirect_uri: str):
           self.client_id = client_id...

 [0.82] docs/adr/003-auth-provider.md > ADR-003: Auth Provider Selection
   ## Decision
   We chose Auth0 over Cognito because: 1) Better PKCE support,
   2) Built-in MFA, 3) Superior documentation...

 ------------------------------------------
 zembed-1 + zerank-2 | 1,847 tokens | 0.3s
```

One query returns the design spec, the implementation code, and the architectural decision record -- ranked by relevance, in under a second.

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `probe index [paths...]` | Index project files for semantic search |
| `probe index --full` | Force full re-index (ignore file hashes) |
| `probe install` | Register probe as a user-scope MCP server in Claude Code |
| `probe search "query"` | Search project knowledge with natural language |
| `probe search --top-k N` | Limit number of results (default: 10) |
| `probe search --type code` | Filter by file type (markdown, code, pdf, text) |
| `probe search --no-rerank` | Skip reranking (faster, lower quality) |
| `probe search --max-tokens N` | Token budget for results (default: 4096) |
| `probe status` | Show index stats and provider config |
| `probe list` | List all indexed files |
| `probe config` | Show current provider configuration |
| `probe init` | Auto-detect provider and save config |
| `probe mcp` | Start MCP server (stdio transport) |
| `probe uninstall [--purge]` | Unregister probe; `--purge` also deletes `.probe/` in cwd |

---

## Multi-Provider Support

probe defaults to ZeroEntropy but works with other providers:

| Provider | Embedding | Reranker | Install |
|----------|-----------|----------|---------|
| **[ZeroEntropy](https://www.zeroentropy.dev)** (default) | zembed-1 | zerank-2 | included |
| OpenAI | text-embedding-3-large | -- | `pip install "probe-search[openai]"` |
| Cohere | embed-v4.0 | rerank-v3.5 | `pip install "probe-search[cohere]"` |

Set the corresponding API key (`ZEROENTROPY_API_KEY`, `OPENAI_API_KEY`, or `COHERE_API_KEY`) and run `probe init`. You can mix providers -- for example, OpenAI for embeddings with ZeroEntropy for reranking.

---

## Why ZeroEntropy?

[zembed-1](https://www.zeroentropy.dev/articles/introducing-zembed-1-the-worlds-best-multilingual-text-embedding-model) is a 4B-parameter open-weight embedding model that outperforms OpenAI, Cohere, and Voyage across nine domains including code, legal, finance, and healthcare. Combined with [zerank-2](https://www.zeroentropy.dev/articles/zerank-2-advanced-instruction-following-multilingual-reranker) for cross-encoder reranking, it delivers the best retrieval quality available.

Pricing: $0.05 per 1M tokens for embeddings, $0.025 per 1M tokens for reranking. [Free trial available](https://www.zeroentropy.dev/pricing).

---

## Configuration

probe stores its index and config in `.probe/` at your project root. Add it to `.gitignore`.

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

## How Data Is Handled

Documents are chunked and stored locally in `.probe/` (SQLite + numpy). Only chunk text is sent to the embedding/reranking API for processing. Documents are never uploaded or stored on any external server.

---

## What's NOT in v1

- Web sources (Notion, Confluence, Google Docs)
- Git-aware context (commit history, blame)
- Image/diagram understanding within PDFs
- Custom chunking strategies

---

## Links

- [ZeroEntropy](https://www.zeroentropy.dev) -- the AI retrieval engine powering probe
- [API Dashboard](https://dashboard.zeroentropy.dev) -- get your API key
- [Documentation](https://docs.zeroentropy.dev) -- ZeroEntropy API docs
- [Models](https://docs.zeroentropy.dev/models) -- zembed-1 and zerank-2 details

---

## License

MIT -- see [LICENSE](LICENSE) for details.
