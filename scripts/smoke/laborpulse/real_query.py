#!/usr/bin/env python3
"""Week 11 LaborPulse smoke — five locked demo questions against live JIE API.

Mirrors wfd-os → ``POST /analytics/query`` (LaborPulse headers, JIE #222).

Requires JIE running, e.g.::

    python scripts/run_analytics_api.py

Headers match ``scripts/smoke/api_smoke.py``. Set ``ANALYTICS_QUERY_X_API_KEY`` when
``JIE_API_KEYS`` is configured (see ``.env.example``).

Usage::

    python scripts/smoke/laborpulse/real_query.py
    python scripts/smoke/laborpulse/real_query.py --base-url http://localhost:8000

Exit code 0 only if every question returns HTTP 200 with a non-empty answer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.smoke.laborpulse._demo_common import (  # noqa: E402
    DEFAULT_JIE_BASE_URL,
    LOCKED_DEMO_QUESTIONS,
    default_jie_base_url,
    likely_refusal,
)


def _headers(*, request_id: str | None = None) -> dict[str, str]:
    h: dict[str, str] = {
        "X-Tenant-Id": os.environ.get("ANALYTICS_QUERY_X_TENANT_ID", "borderplex").strip() or "borderplex",
        "X-User-Email": os.environ.get("ANALYTICS_QUERY_X_USER_EMAIL", "smoke@thewaifinder.com").strip()
        or "smoke@thewaifinder.com",
        "X-Request-Id": request_id or os.environ.get("ANALYTICS_QUERY_X_REQUEST_ID", "").strip() or str(uuid.uuid4()),
    }
    xk = os.environ.get("ANALYTICS_QUERY_X_API_KEY", "").strip()
    if xk:
        h["X-API-Key"] = xk
    return h


def _post(base: str, question: str, *, req_id: str) -> tuple[int, dict]:
    url = f"{base.rstrip('/')}/analytics/query"
    data = json.dumps({"question": question}).encode()
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(_headers(request_id=req_id))
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode() if e.fp else ""
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw}
    except urllib.error.URLError as e:
        return 0, {"error": str(e.reason)}


def _default_base_url() -> str:
    """Backward-compatible alias for tests and callers."""
    return default_jie_base_url()


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke the five locked LaborPulse demo questions.")
    parser.add_argument(
        "--base-url",
        default=default_jie_base_url(),
        help=f"JIE Analytics API base URL (default: {DEFAULT_JIE_BASE_URL} or JIE_BASE_URL).",
    )
    parser.add_argument(
        "--require-non-empty-sql",
        action="store_true",
        help="Treat empty sql_generated as failure (stricter “live pipeline” check).",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print(f"Base URL: {base}")
    print(f"Questions: {len(LOCKED_DEMO_QUESTIONS)}")
    print()

    all_ok = True
    for i, q in enumerate(LOCKED_DEMO_QUESTIONS, start=1):
        req_id = f"laborpulse-real-query-{i}-{uuid.uuid4().hex[:8]}"
        status, body = _post(base, q, req_id=req_id)

        answer = ""
        confidence = None
        evidence_count = None
        err_parts: list[str] = []

        if status != 200:
            err_parts.append(f"HTTP {status}")
            detail = body.get("detail")
            if detail is not None:
                err_parts.append(json.dumps(detail, default=str)[:500])
            elif body.get("error"):
                err_parts.append(str(body["error"]))
        else:
            answer = str(body.get("answer") or "")
            confidence = body.get("confidence")
            ev = body.get("evidence")
            evidence_count = len(ev) if isinstance(ev, list) else None
            sql_gen = str(body.get("sql_generated") or "")
            if args.require_non_empty_sql and not sql_gen.strip():
                err_parts.append("empty sql_generated")
            if likely_refusal(answer):
                err_parts.append("answer looks like refusal or empty")

        answer_ok = bool(status == 200 and answer.strip() and not likely_refusal(answer))
        if args.require_non_empty_sql:
            answer_ok = answer_ok and bool(str(body.get("sql_generated") or "").strip())

        print(f"--- Question {i} ---")
        print(q)
        print(f"Answer received: {'yes' if answer_ok else 'no'}")
        print(f"Confidence label shown: {confidence if confidence is not None else '(n/a)'}")
        print(f"Evidence count: {evidence_count if evidence_count is not None else '(n/a)'}")
        if err_parts:
            print(f"Errors / warnings: {'; '.join(err_parts)}")
        else:
            print("Errors / warnings: none")
        print()

        if not answer_ok:
            all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
