# Smoother UX & auto re-index — design

**Status**: Draft — awaiting review
**Date**: 2026-04-17
**Target version**: 0.2.0

## 1. Motivation

Today, a user who wants to try probe with Claude Code has to: install the Python package, get an API key, hand-write a `.mcp.json` file, paste the key into it, restart Claude Code, and remember to run `probe index` if anything changed on disk. That is four manual steps where there should be one, and one missing refresh path where there should be zero.

This spec closes both gaps:

- **Feature A — `probe install`**: one command that registers probe as a user-scope MCP server in Claude Code, so every project on the machine gets probe automatically.
- **Feature B — refresh-before-search**: probe detects on-disk changes before each search and incrementally re-indexes only what actually changed, so the user never has to think about the index.

The two features are independent: either ships without the other. They are bundled here because they share the same goal (make probe invisible) and the same release window (0.2.0).

## 2. Feature A — `probe install`

### 2.1 Command shape

```
probe install [--api-key KEY] [--no-embed-key] [--force]
probe uninstall [--purge]
```

- `--api-key KEY`: non-interactive; skip prompting, embed the given key.
- `--no-embed-key`: register probe without an `env` block; rely on shell-inherited `ZEROENTROPY_API_KEY`.
- `--force`: skip the "already installed, reinstall?" confirmation.
- `probe uninstall --purge`: also delete `.probe/` in the current working directory.

### 2.2 Algorithm

Happy path, numbered:

1. `shutil.which("claude")` — if missing, print install instructions pointing to the official Claude Code docs and exit `1`. (Actual URL resolved at implementation time — avoid hardcoding an outdated link.)
2. `claude mcp get probe --scope user`:
   - Exit code 0 (already registered): prompt "probe is already registered. Reinstall? \[y/N]". Unless `--force` or user says yes, exit 0.
   - On reinstall: `claude mcp remove probe --scope user`.
3. Resolve API key:
   - If `--api-key KEY` given: use `KEY`, skip prompts.
   - Else if `os.environ.get("ZEROENTROPY_API_KEY")` is set: `click.confirm("Use $ZEROENTROPY_API_KEY from environment?", default=True)`. If yes: use env value. If no: fall through to interactive prompt.
   - Else: `click.prompt("Enter your ZeroEntropy API key", hide_input=True)`. On empty input, re-prompt up to 3 times, then exit 1 with "API key required".
4. Resolve probe binary path. This yields a list of argv tokens:
   - First try `shutil.which("probe")` → `[<absolute path to probe>]`.
   - Fallback: `[sys.executable, "-m", "probe.cli"]`.
5. Build and run the registration command. Tokens after `--` are passed through as the MCP subprocess argv:
   ```
   claude mcp add --scope user --transport stdio \
     [-e ZEROENTROPY_API_KEY=<key>] \
     probe -- <argv-tokens-from-step-4> mcp
   ```
   Concretely: either `claude mcp add ... probe -- /Users/x/.local/bin/probe mcp` or `claude mcp add ... probe -- /path/to/python -m probe.cli mcp`.
   Use `subprocess.run(..., check=True)`. On non-zero exit: print claude's stderr verbatim and exit `1`.
6. Print success summary:
   ```
   ✓ probe installed at user scope.
     Open any project in Claude Code and ask a question — probe will auto-index on first search.
     To uninstall: probe uninstall
   ```

### 2.3 Why shell out to `claude mcp add` instead of editing `~/.claude.json`

Claude Code owns its config-file schema. Writing the JSON ourselves means tracking schema changes across Claude Code versions. Shelling out to the CLI means Claude Code handles the shape and we only own the command arguments.

### 2.4 Error handling

| Failure | Response |
|---|---|
| `claude` not on PATH | Print: "Claude Code CLI not found. Install from https://docs.anthropic.com/claude-code, then rerun `probe install`." Exit 1. |
| `claude mcp add` exits non-zero | Print claude's stderr verbatim. Exit 1. |
| User cancels prompts (Ctrl-C) | Clean exit 130; no partial state (nothing was written yet). |
| `ZEROENTROPY_API_KEY` prompt answered empty | Re-prompt up to 3 times, then exit 1 with "API key required" message. |
| `probe` not on PATH at registration time | Fall back to `python -m probe.cli` using the current `sys.executable`. Warn: "probe binary not found on PATH; using `<sys.executable> -m probe.cli`. If you move this Python env, rerun `probe install`." |

### 2.5 `probe uninstall`

1. `claude mcp remove probe --scope user` — ignore "not found" errors.
2. If `--purge`: `shutil.rmtree(Path.cwd() / ".probe", ignore_errors=True)`. Print what was deleted.
3. Print "✓ probe uninstalled." and exit 0.

### 2.6 Out of scope for this feature

- Claude Code plugin-marketplace listing (tracked for a future release; the README-advertised `probe install` flow is already "one command").
- Touching a global CLAUDE.md (scope creep; the MCP instructions probe already ships are enough).
- Automatically indexing the current directory during install. Install is machine-global; indexing is per-project. Forcing an index during install means guessing at a project root.

## 3. Feature B — refresh-before-search

### 3.1 Summary

One new method on `IndexPipeline`, called by both the CLI `search` command and the MCP `probe_search` tool before they invoke the engine.

```python
def refresh_changed(self, paths: list[Path]) -> dict:
    """Incrementally re-index files that changed since last index.
    Returns stats: {'added': int, 'changed': int, 'removed': int, 'elapsed_ms': int}.
    Cheap (<100ms) when nothing changed."""
```

### 3.2 Two-phase algorithm

**Phase 1 — stat sweep** (cheap):

1. Walk `paths` via the existing `discover_files()`.
2. For each file, read `(mtime_ns, size)` via `os.stat`.
3. Compare to `files.mtime_ns` and `files.size` columns in the DB. Bucket:
   - *Unchanged* (mtime + size both match): skip.
   - *Likely changed* (either differs): add to phase-2 queue.
   - *New* (path not in DB): add to phase-2 queue.
4. *Deleted*: rows in `files` whose path is not on disk — delete immediately (cascade drops chunks; vector store entries removed by chunk id, as existing pipeline already does).

**Phase 2 — hash confirm** (only for phase-1 survivors):

For each file in the queue:

1. Compute SHA-256.
2. If hash matches DB (mtime/size changed but content didn't — e.g. `touch`): just update `mtime_ns`/`size` in DB; skip re-embed.
3. If hash differs (or file is new): re-chunk, embed new chunks, replace DB rows and vector entries.

**Prerequisite refactor**: extract the single-file indexing logic (extract → chunk → embed → persist) from `IndexPipeline.index()` into a private `_index_file()` helper, so both `index()` and `refresh_changed()` share one code path. This refactor lands in the same change and is covered by the existing `test_pipeline.py` suite plus the new refresh tests.

After phase 2, return the stats dict.

### 3.3 DB migration

Add two columns to the existing `files` table:

```sql
ALTER TABLE files ADD COLUMN mtime_ns INTEGER NOT NULL DEFAULT 0;
ALTER TABLE files ADD COLUMN size INTEGER NOT NULL DEFAULT 0;
```

On the first refresh after upgrade, existing rows have `mtime_ns=0` → they all fall into "likely changed" → phase 2 runs, hash confirms no change, mtime/size get backfilled, zero API calls.

Applied idempotently in `ProbeDB.initialize()` by wrapping each ALTER in a try/except on `sqlite3.OperationalError: duplicate column`.

### 3.4 `RefreshGate` (debounce)

```python
# src/probe/indexer/refresh_gate.py
class RefreshGate:
    def __init__(self, ttl_seconds: float = 5.0):
        self._last_refresh = 0.0
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
    def should_refresh(self) -> bool: ...
    def mark(self) -> None: ...  # sets _last_refresh = time.monotonic()
```

One instance per process. TTL resolution:

- `PROBE_REFRESH_TTL` env var, in seconds (float).
- `0`: refresh on every search (never debounce).
- `-1`: disable refresh entirely.
- Unset: default 5.0.

### 3.5 Integration points

**CLI** (`cli.py search` command):

```python
gate = RefreshGate()
if gate.should_refresh():
    stats = pipeline.refresh_changed([Path.cwd()])
    gate.mark()
    if stats["added"] + stats["changed"] + stats["removed"] > 0:
        console.print(
            f"[dim]Refreshed: +{stats['added']} ±{stats['changed']} "
            f"-{stats['removed']} ({stats['elapsed_ms']}ms)[/dim]"
        )
```

**MCP** (`mcp/server.py probe_search`):

Same call before `engine.search(...)`. The existing "auto-index if empty" block is subsumed: when DB is empty, every file is "new" → phase 2 indexes everything. The two code paths merge into one.

Return shape gains a `refreshed` field:

```json
{
  "query": "...",
  "results": [...],
  "total_tokens": 612,
  "sources_searched": 56,
  "refreshed": {"added": 1, "changed": 2, "removed": 0, "elapsed_ms": 143}
}
```

Always present; zeroes when nothing changed.

After a non-zero refresh, `state.invalidate()` is called to drop the cached `ContextEngine` so the next search loads the fresh vector store.

### 3.6 Failure modes

| Failure | Response |
|---|---|
| File deleted during walk (`FileNotFoundError`) | Swallow; treat as deleted. |
| `os.stat` permission denied | Log at `PROBE_VERBOSE=1`; skip file; continue. |
| SHA-256 read fails | Log; skip; continue. |
| Embedding API 429 / 5xx | `refresh_changed` raises. CLI prints yellow warning: `refresh failed: <reason>, using stale index`. MCP returns `refreshed: {"added": 0, "changed": 0, "removed": 0, "elapsed_ms": N, "error": "<reason>"}`. Search still runs against whatever is in the DB. |
| Concurrent refresh calls in the same process | `RefreshGate._lock` serializes; only one refresh executes. |
| Concurrent CLI + MCP writing the same DB | SQLite WAL handles reader-during-write; concurrent writers serialize on the DB lock. |

Partial refresh is always better than no search — failure during refresh never blocks the search.

### 3.7 User-visible surface

- CLI: one dim line before results, only if anything changed. Silent otherwise.
- MCP: `refreshed` field in every JSON response; Claude Code's tool-result renderer handles presentation; the LLM can cite it naturally ("I notice you just edited `auth.py`…").
- `PROBE_VERBOSE=1`: one stderr line per file in the refresh queue, with phase (stat/hash/embed/skip) and timing.

## 4. Testing strategy

### 4.1 New unit tests

`tests/test_pipeline.py` — extend:
- `test_refresh_no_changes` — stat-only pass, zero embed calls.
- `test_refresh_edited_file` — mtime differs, hash differs, re-embed happens once.
- `test_refresh_touched_file` — mtime differs, hash matches; DB mtime updated; no embed.
- `test_refresh_size_change_same_mtime` — size differs alone triggers phase 2.
- `test_refresh_new_file` — not in DB, gets indexed.
- `test_refresh_deleted_file` — row and vectors removed.
- `test_refresh_stats_shape` — returned dict has required keys.

`tests/test_refresh_gate.py` — new:
- TTL=0 always allows.
- TTL=-1 never allows.
- TTL=5 allows first, denies second within window, allows after window passes (use monkeypatch on `time.monotonic`).
- `PROBE_REFRESH_TTL` env override respected.
- Concurrent `should_refresh` under the lock.

`tests/test_cli.py` — extend:
- `test_install_no_claude_cli` — monkeypatch `shutil.which` to return None; assert exit code 1 and instructional message.
- `test_install_uses_env_key` — env set, confirm defaults yes; assert `subprocess.run` called with `-e ZEROENTROPY_API_KEY=<env>`.
- `test_install_prompts_for_key` — env unset; assert prompt called with `hide_input=True`.
- `test_install_no_embed_key_flag` — assert no `-e` in args.
- `test_install_api_key_flag` — assert no prompt, embedded value matches flag.
- `test_install_already_installed` — `claude mcp get probe` exits 0; assert prompt, on "no" exit is clean.
- `test_uninstall` — assert `claude mcp remove` called; `--purge` also removes `.probe/`.

`tests/test_mcp.py` — extend: drive a fake `probe_search` with file changes between indexing and search; assert response includes `refreshed` with expected counts.

All `subprocess.run` calls mocked — tests never shell out to real `claude`.

### 4.2 Manual end-to-end validation

Run inside `/Users/dilawar/tmp/probe-toy` (the toy project from earlier):

1. `probe install` in a fresh shell → confirm prompts, confirm `claude mcp list` shows probe under user scope.
2. Open a *new* Claude Code session in an unrelated directory → ask a probe-flavored question → observe `probe_search` being called.
3. Edit one of the toy files → ask a follow-up → observe `refreshed: {"changed": 1}` in the MCP response.
4. `probe uninstall` → confirm `claude mcp list` no longer shows probe.

## 5. Documentation updates

- **README Quick Start** — replace the manual `.mcp.json` snippet with:
  ```
  pip install probe-search
  probe install
  ```
  Add one-line note: "The index auto-refreshes on each search; set `PROBE_REFRESH_TTL=0` for immediate freshness or `-1` to disable."
- **README MCP Server Setup** — keep the manual `.mcp.json` as an advanced/CI option, move below the install flow.
- **CLI Reference table** — add rows for `probe install`, `probe uninstall`.
- **What's NOT in v1** — remove the "File system watcher" bullet; replace with "Real-time filesystem watcher (refresh-before-search is sufficient for most projects)."

## 6. Non-goals

- **Filesystem watcher daemon**: refresh-before-search is sufficient for projects up to ~50k files at expected edit rates. Watcher adds a background thread, watchdog dependency, and lifecycle complications for a latency improvement only megarepos will notice.
- **OS keychain for API key**: adds per-OS wiring and a failure mode on headless Linux / WSL; the `--no-embed-key` + shell env flow already gives users a disk-free option.
- **Plugin marketplace listing**: a thin distribution layer on top of `probe install` can come later. Install command works standalone.
- **Global CLAUDE.md edits**: the MCP `instructions` block already strong-arms Claude into preferring `probe_search` over grep/read.

## 7. Rollout

- Version bump: `0.1.0` → `0.2.0` in `pyproject.toml` and `src/probe/__init__.py`.
- Changelog entry (add `CHANGELOG.md` if absent):
  ```
  ## 0.2.0 — 2026-04-TBD
  - Added `probe install` / `probe uninstall` for one-command Claude Code integration.
  - Added refresh-before-search: indexes now update automatically when files change.
  ```
- Migration notes: none required for users; DB migration is automatic and idempotent.
