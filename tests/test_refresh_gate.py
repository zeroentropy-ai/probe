"""Tests for the refresh-before-search debounce gate."""

import threading

import pytest

from probe.indexer.refresh_gate import RefreshGate


class TestRefreshGate:
    def test_default_ttl_allows_first_refresh(self):
        gate = RefreshGate(ttl_seconds=5.0)
        assert gate.should_refresh() is True

    def test_mark_blocks_within_ttl(self, monkeypatch):
        """After marking, subsequent should_refresh within TTL returns False."""
        now = [100.0]
        monkeypatch.setattr("time.monotonic", lambda: now[0])

        gate = RefreshGate(ttl_seconds=5.0)
        assert gate.should_refresh() is True
        gate.mark()
        now[0] = 102.0  # 2s later, within 5s TTL
        assert gate.should_refresh() is False

    def test_refresh_allowed_after_ttl(self, monkeypatch):
        now = [100.0]
        monkeypatch.setattr("time.monotonic", lambda: now[0])

        gate = RefreshGate(ttl_seconds=5.0)
        gate.mark()
        now[0] = 106.0  # 6s later, past TTL
        assert gate.should_refresh() is True

    def test_ttl_zero_always_allows(self, monkeypatch):
        now = [100.0]
        monkeypatch.setattr("time.monotonic", lambda: now[0])

        gate = RefreshGate(ttl_seconds=0.0)
        gate.mark()  # marking has no effect
        assert gate.should_refresh() is True
        assert gate.should_refresh() is True

    def test_ttl_negative_never_allows(self):
        gate = RefreshGate(ttl_seconds=-1.0)
        assert gate.should_refresh() is False

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("PROBE_REFRESH_TTL", "0")
        gate = RefreshGate.from_env()
        # With ttl=0, always allowed
        gate.mark()
        assert gate.should_refresh() is True

    def test_env_var_disabled(self, monkeypatch):
        monkeypatch.setenv("PROBE_REFRESH_TTL", "-1")
        gate = RefreshGate.from_env()
        assert gate.should_refresh() is False

    def test_env_var_absent_defaults_to_5s(self, monkeypatch):
        monkeypatch.delenv("PROBE_REFRESH_TTL", raising=False)
        gate = RefreshGate.from_env()
        assert gate._ttl == 5.0

    def test_concurrent_should_refresh_is_safe(self):
        """Ensure no race when multiple threads hit should_refresh simultaneously."""
        gate = RefreshGate(ttl_seconds=5.0)
        results: list[bool] = []
        def worker():
            results.append(gate.should_refresh())
        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        # Without the lock this could throw; we just verify no exceptions and
        # that we got one result per thread.
        assert len(results) == 20
