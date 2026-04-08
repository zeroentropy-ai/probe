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
pip install probe-search

export ZEROENTROPY_API_KEY="your-key"

# Index your project
probe index .

# Search with natural language
probe search "how does authentication work"
```

---

## MCP Server Setup

probe ships as an MCP server so AI agents can call it directly.

**Claude Code** (`~/.claude/claude_code_config.json`):

```json
{
  "mcpServers": {
    "probe": {
      "command": "probe",
      "args": ["mcp"]
    }
  }
}
```

**Cursor** (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "probe": {
      "command": "probe",
      "args": ["mcp"]
    }
  }
}
```

Once configured, your AI agent gains four tools: `probe_search`, `probe_index`, `probe_status`, and `probe_read`.

---

## How It Works

probe uses a three-step pipeline to find the best context for every query:

**1. Hybrid Retrieval**
Each query runs through two parallel search paths: semantic vector search (cosine similarity against embeddings) and keyword search (BM25 over SQLite FTS5). This catches both conceptual matches and exact term hits.

**2. Cross-Source Reranking**
Results from both paths are fused using Reciprocal Rank Fusion, then passed through a neural reranker that scores each chunk against the original query. This pushes the most relevant results to the top regardless of which retrieval path found them.

**3. Smart Context Assembly**
The top results are assembled into a response that fits within your token budget. Each result includes its source file, location metadata (header paths, symbol names, page numbers), and a relevance score.

---

## Example Output

```
$ probe search "how does the retry logic work"

 Found 5 results (127 chunks searched)

 [0.92] docs/architecture.md > Error Handling > Retry Strategy
   The retry mechanism uses exponential backoff with jitter.
   Base delay starts at 100ms, doubling on each attempt up to
   a maximum of 5 retries.

 [0.87] src/client/http.py > RetryHandler
   class RetryHandler:
       def __init__(self, max_retries=5, base_delay=0.1):
           self.max_retries = max_retries

 [0.71] docs/api-reference.md > Configuration > retry_policy
   The retry_policy field accepts an object with max_retries
   (int) and backoff_factor (float) keys.

 [0.58] tests/test_retry.py > TestRetryHandler
   def test_exponential_backoff(self):
       handler = RetryHandler(max_retries=3)

 [0.41] CHANGELOG.md > v2.1.0
   Added configurable retry logic for transient API failures.

 ------------------------------------------
 zembed-1 + zerank-2 | 1,847 tokens | 0.8s
```

---

## CLI Reference

| Command         | Description                                       |
| --------------- | ------------------------------------------------- |
| `probe index`   | Index project files for semantic search            |
| `probe search`  | Search project knowledge with natural language     |
| `probe status`  | Show index status and configuration                |
| `probe list`    | List all indexed files                             |
| `probe config`  | Show current provider configuration                |
| `probe init`    | Interactive setup: choose providers and API keys   |
| `probe mcp`     | Start the MCP server (stdio transport)             |

---

## Multi-Provider Support

| Provider       | Embedding Model             | Reranker       | Dimensions |
| -------------- | --------------------------- | -------------- | ---------- |
| ZeroEntropy    | `zembed-1` (default)        | `zerank-2`     | 1024       |
| OpenAI         | `text-embedding-3-large`    | --             | 1536       |
| Cohere         | `embed-v4.0`                | `rerank-v3.5`  | 1024       |

Install optional providers:

```bash
pip install "probe-search[openai]"     # OpenAI support
pip install "probe-search[cohere]"     # Cohere support
pip install "probe-search[all]"        # All providers
```

Set the corresponding API key environment variable (`ZEROENTROPY_API_KEY`, `OPENAI_API_KEY`, or `COHERE_API_KEY`) and run `probe init` to auto-detect and configure your provider.

---

## Why ZeroEntropy?

ZeroEntropy is the default provider because `zembed-1` outperforms alternatives across nine domains including code, documentation, and technical writing. Combined with `zerank-2` for reranking, it provides the best retrieval quality for software project context.

See the [ZeroEntropy benchmarks](https://www.zeroentropy.dev/blog/zembed-1) for detailed comparisons.

---

## Configuration

probe stores its configuration and index data in a `.probe/` directory at your project root.

```yaml
# .probe/config.yaml
providers:
  embedding:
    name: zeroentropy
    model: zembed-1
    dimensions: 1024
  reranker:
    name: zeroentropy
    model: zerank-2
```

---

## What's NOT in v1

- Incremental watch mode (file system watcher for auto-reindexing)
- Remote/shared indexes (team-wide search)
- Custom chunking strategies via config
- Plug-in extractors for additional file types (e.g., Jupyter notebooks, Confluence)
- Query history and analytics
- Streaming search results

---

## License

MIT -- see [LICENSE](LICENSE) for details.
