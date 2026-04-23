"""Shared LaborPulse headers for ``POST /analytics/query`` contract tests (JIE #222)."""

from __future__ import annotations

import uuid


def laborpulse_query_headers(api_key: str | None = "", **overrides: str) -> dict[str, str]:
    """LaborPulse required headers for ``POST /analytics/query``. Pass ``api_key=None`` to omit ``X-API-Key``."""
    h = {
        "Content-Type": "application/json",
        "X-Tenant-Id": "borderplex",
        "X-User-Email": "contract-test@example.com",
        "X-Request-Id": f"test-req-{uuid.uuid4().hex[:16]}",
    }
    if api_key is not None:
        h["X-API-Key"] = api_key
    h.update(overrides)
    return h
