"""Run async adapter coroutines from synchronous EnrichmentAgent code.

``asyncio.run`` must not be used when an event loop is already running; Phase 1 pipeline
invokes ``process`` from sync contexts only. Phase 2 should reduce per-record loop overhead
(e.g. shared event loop, batched adapter calls, or an async agent entrypoint). See
``.cursor/rules/integration-schema.mdc`` § Phase 1 async bridge.

Issue #149 fix: ``_get_persistent_event_loop()`` returns a single long-lived event loop
that is reused across all batches. This avoids the cascade of bugs that ``asyncio.run()``
per-batch causes (orphaned ``httpx.AsyncClient.aclose`` tasks bound to dead loops,
scheduler-state corruption that makes ``await asyncio.sleep(0)`` hang in subsequent
batches even though the new batch creates a fresh loop).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

# Module-level persistent event loop shared across batches by both
# EnrichmentAgent and SkillsExtractionAgent. Created lazily on first use,
# never closed until process exit (OS reclaims at shutdown).
_persistent_loop: asyncio.AbstractEventLoop | None = None


def _get_persistent_event_loop() -> asyncio.AbstractEventLoop:
    """Return a single long-lived event loop reused across all sync→async bridges.

    Repeated ``asyncio.run()`` per batch leaks ``httpx.AsyncClient`` tasks bound to
    each closed loop. When the next batch creates a new loop, those orphaned tasks
    fire ``aclose()`` against the dead loop (RuntimeError: Event loop is closed)
    AND corrupt the new loop's scheduler — the next ``await asyncio.sleep(0)``
    inside the new batch never resumes. Sharing one loop sidesteps both issues.
    """
    global _persistent_loop
    if _persistent_loop is None or _persistent_loop.is_closed():
        _persistent_loop = asyncio.new_event_loop()
    return _persistent_loop


def run_coroutine(coro: Coroutine[Any, Any, T]) -> T:
    """Execute one coroutine to completion (uses the persistent event loop when none is running)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Fix for #149: reuse the persistent loop instead of asyncio.run() per call,
        # so any cached httpx.AsyncClient/openai client survives across batches
        # without being bound to a dead loop's selectors.
        return _get_persistent_event_loop().run_until_complete(coro)
    msg = (
        "Cannot run enrichment external adapters: an event loop is already running. "
        "Call EnrichmentAgent.process from a synchronous context, or refactor to async."
    )
    raise RuntimeError(msg)
