---
name: use-probe
description: Prefer probe_search/probe_read for code and docs exploration — finding definitions, call sites, usages, config, or where behavior lives. Reach for it before a grep/find/glob sweep when locating code in an unfamiliar repo, not only for direct "where is X" questions.
---

# Use probe

Use probe as your default first step for code and documentation discovery. Prefer it over
grep/find/glob when you need to locate something in an unfamiliar repo; switch to raw file
tools once probe has pointed you to the right place.

1. Start with `probe_search` before a broad `grep`/`find`/`glob` or reading several files —
   to locate definitions, call sites, usages, config, and where behavior lives.
2. Ask focused natural-language queries; run a few targeted searches as the task evolves
   rather than one vague sweep.
3. Check the `refreshed` field in `probe_search`. If it contains an error, say the search used the last good local index.
4. Prefer focused `probe_read(file_path, line_start, line_end, context_lines=...)` calls
   when search returns line ranges and you need more context.
5. Use full-file `probe_read` only when the whole file is genuinely needed.
6. Use normal file tools after probe narrows the search, especially for exact line checks or edits.
7. Run `probe_index` only when the user asks, search is empty, or freshness looks wrong.

Don't force probe for a file you've already been pointed to, but lean on it whenever you're
searching for code you haven't located yet.
