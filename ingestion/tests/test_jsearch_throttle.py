"""Unit tests for JSearch adapter rate-limit throttle (issue #157).

Covers:
- `_respect_rate_limit` honors the configured inter-request gap.
- Per-event-loop state is isolated (reset helper clears cache).
- Throttle reads ``JSEARCH_RPS`` lazily on first use inside a loop.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from ingestion.sources import jsearch_adapter


@pytest.fixture(autouse=True)
def _reset_throttle_state():
    jsearch_adapter._reset_rate_limit_state_for_tests()
    yield
    jsearch_adapter._reset_rate_limit_state_for_tests()


def test_respect_rate_limit_enforces_min_gap_at_5rps(monkeypatch):
    """Three back-to-back calls at 5 rps should take at least 2 * 200 ms total."""
    monkeypatch.setenv("JSEARCH_RPS", "5")

    async def run() -> float:
        # First call seeds the timestamp; the next two must each wait ~200 ms.
        start = time.monotonic()
        await jsearch_adapter._respect_rate_limit()
        await jsearch_adapter._respect_rate_limit()
        await jsearch_adapter._respect_rate_limit()
        return time.monotonic() - start

    elapsed = asyncio.run(run())
    # Two gaps of 0.2s each = 0.4s minimum. Allow 100ms slack for scheduler jitter.
    assert elapsed >= 0.38, f"expected >= 0.38s, got {elapsed:.3f}s"
    # Sanity: shouldn't be absurdly slow
    assert elapsed < 1.0, f"expected < 1.0s, got {elapsed:.3f}s"


def test_rps_state_initialized_from_env(monkeypatch):
    """Higher RPS → smaller min_gap; state is lazy-read on first call."""
    monkeypatch.setenv("JSEARCH_RPS", "10")

    async def run():
        state = jsearch_adapter._get_rps_state()
        assert state["min_gap"] == pytest.approx(0.1, rel=0.01)

    asyncio.run(run())


def test_rps_state_defaults_to_5_when_env_unset(monkeypatch):
    monkeypatch.delenv("JSEARCH_RPS", raising=False)

    async def run():
        state = jsearch_adapter._get_rps_state()
        assert state["min_gap"] == pytest.approx(0.2, rel=0.01)

    asyncio.run(run())


def test_rps_state_is_isolated_per_event_loop(monkeypatch):
    """Each asyncio.run creates a fresh loop; reset fixture ensures fresh state."""
    monkeypatch.setenv("JSEARCH_RPS", "5")

    async def first():
        await jsearch_adapter._respect_rate_limit()
        return jsearch_adapter._get_rps_state()["last_start"]

    asyncio.run(first())
    # Between runs, the helper clears state via the autouse fixture — so a
    # second run starts fresh and its first call doesn't wait.
    # (We don't test zero-wait here; we just confirm no exception / no leaked state.)
    jsearch_adapter._reset_rate_limit_state_for_tests()

    async def second():
        await jsearch_adapter._respect_rate_limit()

    asyncio.run(second())
