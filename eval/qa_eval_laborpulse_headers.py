"""LaborPulse `POST /analytics/query` request headers (JIE #222) for the qa_eval HTTP path.

Env contract matches ``scripts/smoke/smoke_issue197.py`` ``_laborpulse_headers``;
`X-Request-Id` is the harness per-item `correlation_id` (not a random UUID).
"""

from __future__ import annotations

import os


def laborpulse_analytics_query_headers(*, x_request_id: str) -> dict[str, str]:
    h: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Tenant-Id": os.environ.get("ANALYTICS_QUERY_X_TENANT_ID", "borderplex").strip() or "borderplex",
        "X-User-Email": os.environ.get("ANALYTICS_QUERY_X_USER_EMAIL", "smoke@thewaifinder.com").strip()
        or "smoke@thewaifinder.com",
        "X-Request-Id": x_request_id,
    }
    xk = os.environ.get("ANALYTICS_QUERY_X_API_KEY", "").strip()
    if xk:
        h["X-API-Key"] = xk
    return h
