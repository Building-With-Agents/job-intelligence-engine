"""HTTP client for ``POST /analytics/query`` — dashboard only (no ``analytics`` package imports)."""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

MOCK_ANALYTICS_QUERY_RESPONSE: dict[str, Any] = {
    "conversation_id": "00000000-0000-4000-8000-000000000001",
    "answer": "Mock answer for testing",
    "evidence": [
        {"title": "Mock", "source": "fixture", "snippet": "Source 1", "supporting_count": None, "time_period": None},
        {"title": "Mock", "source": "fixture", "snippet": "Source 2", "supporting_count": None, "time_period": None},
    ],
    "confidence": "high",
    "follow_up_questions": ["What skills are trending?", "Which roles are emerging?"],
    "sql_generated": "SELECT TOP 100 * FROM analytics_aggregates",
    "cost_usd": 0.002,
}


def analytics_query_uses_mock() -> bool:
    return os.getenv("DASHBOARD_ANALYTICS_QUERY_MOCK", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def analytics_query_base_url() -> str:
    return os.getenv("ANALYTICS_QUERY_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def post_analytics_query(
    query: str,
    *,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """POST JSON body to analytics query API. Returns a dict with an ``ok`` flag.

    On success: ``{"ok": True, **response_json}``.
    On failure: ``{"ok": False, "error": "human-readable message"}`` (no stack traces).
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "Please enter a question."}

    if analytics_query_uses_mock():
        out = {**MOCK_ANALYTICS_QUERY_RESPONSE}
        return {"ok": True, **out}

    url = f"{analytics_query_base_url()}/analytics/query"
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Tenant-Id": os.getenv("ANALYTICS_QUERY_X_TENANT_ID", "borderplex").strip() or "borderplex",
        "X-User-Email": os.getenv("ANALYTICS_QUERY_X_USER_EMAIL", "dashboard@thewaifinder.com").strip()
        or "dashboard@thewaifinder.com",
        "X-Request-Id": os.getenv("ANALYTICS_QUERY_X_REQUEST_ID", "").strip() or str(uuid.uuid4()),
    }
    api_key = os.getenv("ANALYTICS_QUERY_X_API_KEY", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.post(url, json={"question": q}, headers=headers)
    except httpx.TimeoutException:
        return {
            "ok": False,
            "error": "The analytics service took too long to respond. Try again in a moment.",
        }
    except httpx.RequestError:
        return {
            "ok": False,
            "error": "Could not reach the analytics API. Check that the service is running and "
            "``ANALYTICS_QUERY_BASE_URL`` is correct.",
        }
    except Exception:
        return {
            "ok": False,
            "error": "Something went wrong while contacting the analytics service.",
        }

    if resp.status_code >= 400:
        if resp.status_code == 401:
            return {
                "ok": False,
                "error": (
                    "The analytics API rejected the request (HTTP 401). "
                    "Set ANALYTICS_QUERY_X_API_KEY to a secret that matches JIE_API_KEYS on the API "
                    "server (JIE #226), and ensure X-Tenant-Id / X-User-Email / X-Request-Id are sent "
                    "(defaults come from ANALYTICS_QUERY_X_* env vars; JIE #222)."
                ),
            }
        return {
            "ok": False,
            "error": f"The analytics API returned an error (HTTP {resp.status_code}).",
        }

    try:
        data = resp.json()
    except Exception:
        return {"ok": False, "error": "The analytics API returned a response that was not valid JSON."}

    if not isinstance(data, dict):
        return {"ok": False, "error": "The analytics API returned an unexpected payload."}

    return {"ok": True, **data}
