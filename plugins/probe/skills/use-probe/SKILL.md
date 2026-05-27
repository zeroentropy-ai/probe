---
name: use-probe
description: Use probe_search and probe_read to search indexed project docs and code before broad grep/read sweeps. Use for repository architecture, implementation details, APIs, setup flows, design docs, runbooks, or questions about where behavior is defined.
---

# Use probe

Use probe as the first pass when the user asks about project knowledge that may span files, docs, or code.

1. Start with `probe_search` before broad `grep`, `find`, or reading many files.
2. Ask focused natural-language queries. Prefer one or two targeted searches over a vague sweep.
3. Check the `refreshed` field in `probe_search`. If it contains an error, say the search used the last good local index.
4. Use `probe_read` after search identifies a relevant file and the full file is needed.
5. Use normal file tools after probe narrows the search, especially for exact line checks or edits.
6. Run `probe_index` only when the user asks, search is empty, or freshness looks wrong.

Do not use probe for a file the user already named unless repo context or related docs would help.
