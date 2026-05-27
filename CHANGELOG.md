# Changelog

All notable changes to probe are documented here.

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
