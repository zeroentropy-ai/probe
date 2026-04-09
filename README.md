# probe

**Give your AI agent a brain beyond code.**

probe is a CLI tool and MCP server that indexes your project's documentation, specs, and code, then serves curated, reranked context to AI coding agents. It combines semantic vector search with keyword matching and cross-source reranking to surface the most relevant information from your entire project knowledge base -- not just the code.

---

## The Problem

AI coding agents can grep your source files, but they cannot semantically search your design specs, API documentation, architecture decision records, or runbooks. They are like a brilliant engineer who never read the project wiki. When you ask "how does our auth flow work?", they search for string matches instead of understanding what you mean.

probe bridges this gap. It ingests everything -- markdown docs, code, PDFs, plain text -- and gives your AI agent a semantic search engine over all of it.

---

## Quick Start

```bash
# 1. Get a free API key at https://www.zeroentropy.dev
# 2. Install
pip install probe-search

# 3. Set your API key
export ZEROENTROPY_API_KEY="ze_xxx"

# 4. Index and search
probe index .
probe search "how does authentication work"
```

Or run without installing:

```bash
uvx probe-search search "how does authentication work"
```

---

## MCP Server Setup (Claude Code, Cursor)

Add a `.mcp.json` file to your project root:

```json
{
  "mcpServers": {
    "probe": {
      "command": "uvx",
      "args": ["probe-search", "mcp"],
      "env": {
        "ZEROENTROPY_API_KEY": "ze_xxx"
      }
    }
  }
}
```

This works with Claude Code, Cursor, and any MCP-compatible agent. No `pip install` required -- `uvx` handles it.

On first use, probe automatically indexes your project and serves results. No manual setup needed -- just ask your agent a question and it works.

Your agent gains four tools: `probe_search`, `probe_index`, `probe_status`, and `probe_read`.

---

## How It Works

probe uses a three-step pipeline to find the best context for every query:

**1. Hybrid Retrieval**
Each query runs through two parallel search paths: semantic vector search (cosine similarity against zembed-1 embeddings) and keyword search (BM25 over SQLite FTS5). This catches both conceptual matches and exact term hits.

**2. Cross-Source Reranking**
Results from both paths are fused using Reciprocal Rank Fusion, then passed through zerank-2, a neural cross-encoder reranker that scores each chunk against the original query. Docs and code are ranked together -- the best answer wins regardless of file type.

**3. Smart Context Assembly**
The top results are deduplicated, trimmed to your token budget, and assembled into a response. Each result includes its source file, location metadata (header paths for markdown, symbol names for code, page numbers for PDFs), and a relevance score.

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

Notice how the agent gets three different kinds of context from one query: the design spec, the implementation code, and the architectural decision record.

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `probe index [paths...]` | Index project files for semantic search |
| `probe index --full` | Force full re-index (ignore file hashes) |
| `probe search "query"` | Search project knowledge with natural language |
| `probe search --top-k N` | Limit number of results (default: 10) |
| `probe search --type code` | Filter results by file type (markdown, code, pdf, text) |
| `probe search --no-rerank` | Skip reranking (faster, lower quality) |
| `probe search --max-tokens N` | Set token budget for results (default: 4096) |
| `probe status` | Show index status and configuration |
| `probe list` | List all indexed files |
| `probe config` | Show current provider configuration |
| `probe init` | Auto-detect provider and save configuration |
| `probe mcp` | Start the MCP server (stdio transport) |

---

## Multi-Provider Support

probe defaults to ZeroEntropy but supports multiple embedding and reranking providers:

| Provider | Embedding | Reranker | Install |
|----------|-----------|----------|---------|
| **ZeroEntropy** (default) | zembed-1 | zerank-2 | included |
| OpenAI | text-embedding-3-large | -- | `pip install "probe-search[openai]"` |
| Cohere | embed-v4.0 | rerank-v3.5 | `pip install "probe-search[cohere]"` |

Set the corresponding API key (`ZEROENTROPY_API_KEY`, `OPENAI_API_KEY`, or `COHERE_API_KEY`) and run `probe init` to auto-detect and configure your provider. You can also mix providers -- for example, use OpenAI for embeddings with ZeroEntropy for reranking.

---

## Why ZeroEntropy?

zembed-1 is a 4B-parameter open-weight embedding model that outperforms OpenAI, Cohere, and Voyage across nine domains including code, legal, finance, and healthcare. Combined with zerank-2 for cross-encoder reranking, it provides the best retrieval quality for software project context.

See the [ZeroEntropy benchmarks](https://www.zeroentropy.dev/blog/zembed-1) for detailed comparisons.

---

## Configuration

probe stores its index and configuration in a `.probe/` directory at your project root. Add `.probe/` to your `.gitignore`.

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

Your documents are chunked and stored locally in `.probe/` (SQLite + numpy). Only chunk text is sent to the embedding/reranking API for processing -- the same as any RAG system. Documents themselves are never uploaded or stored on any external server.

---

## What's NOT in v1

- File system watcher for auto-reindexing on changes
- Web sources (Notion, Confluence, Google Docs crawling)
- Git-aware context (commit history, blame, change tracking)
- Image/diagram understanding within PDFs
- Custom chunking strategies via config
- Streaming search results

---

## License

MIT -- see [LICENSE](LICENSE) for details.
