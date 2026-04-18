"""Debounce gate for refresh-before-search."""

from __future__ import annotations

import os
import threading
import time


class RefreshGate:
    """Guards refresh-before-search from running more than once per TTL window.

    TTL semantics:
    - positive: refresh allowed once per `ttl_seconds` window
    - 0: always allowed (mark() is a no-op)
    - negative: never allowed (refresh fully disabled)

    Note: should_refresh() is a read-only check; two threads may both observe
    True in the same window. Callers that need strict single-flight semantics
    should wrap the refresh call itself in their own lock.
    """

    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._ttl = ttl_seconds
        self._last_refresh = 0.0  # monotonic
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "RefreshGate":
        raw = os.environ.get("PROBE_REFRESH_TTL")
        if raw is None:
            return cls(ttl_seconds=5.0)
        try:
            return cls(ttl_seconds=float(raw))
        except ValueError:
            # Malformed value: fall back to default rather than crashing the search.
            return cls(ttl_seconds=5.0)

    def should_refresh(self) -> bool:
        if self._ttl < 0:
            return False
        if self._ttl == 0:
            return True
        with self._lock:
            return (time.monotonic() - self._last_refresh) >= self._ttl

    def mark(self) -> None:
        if self._ttl == 0:
            return
        with self._lock:
            self._last_refresh = time.monotonic()
