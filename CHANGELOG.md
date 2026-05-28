# Changelog

All notable changes to probe are documented here.

## 0.3.0 — 2026-05-28

### Added
- `probe doctor` for local setup diagnostics across API keys, Claude Code, MCP, and index health.
- `probe smoke` for an end-to-end indexing and search validation.
- JSON output for `probe search`, `probe status`, `probe doctor`, and `probe smoke`.
- Line ranges in indexed chunks, CLI search output, and MCP `probe_search` results.
- Focused line-window reads in MCP `probe_read`.

## 0.2.4 — 2026-05-27

### Fixed
- Restored Python 3.10 CI compatibility for release metadata tests.

## 0.2.3 — 2026-05-27

### Added
- PyPI discovery metadata, project URLs, and public contributor/release guidance.
- CI packaging smoke tests and a guarded Trusted Publishing workflow for PyPI releases from `main` or `master`.

## 0.2.2 — 2026-05-27

### Changed
- Clarified Claude Code installation guidance: use a durable tool install, set `ZEROENTROPY_API_KEY`, verify with `claude mcp get probe`, approve the first probe tool call, and add `.probe/` to `.gitignore`.
- Fixed the advanced `uvx` MCP example to use the published `probe` executable from the `probe-search` package.

## 0.2.1 — 2026-05-27

### Fixed
- MCP searches and indexing now fail fast with setup guidance when the embedding provider API key is missing, instead of surfacing a generic SDK connection error.

## 0.2.0 — 2026-04-17

### Added
- `probe install` / `probe uninstall` for one-command Claude Code integration via `claude mcp add --scope user`.
- Refresh-before-search: the index incrementally updates when files change, driven by a two-phase (stat → hash) algorithm with a TTL debounce (`PROBE_REFRESH_TTL`, default 5s).
- `refreshed` field on every `probe_search` MCP response with counts of added/changed/removed files.

### Changed
- MCP `probe_search` no longer needs a separate `probe_index` call — auto-index is subsumed into the unified refresh path.
- `files` table schema gains `mtime_ns` and `size` columns (migration is automatic and backwards-compatible).

### Removed
- The "File system watcher" roadmap item (refresh-before-search covers the same UX need without a daemon).
