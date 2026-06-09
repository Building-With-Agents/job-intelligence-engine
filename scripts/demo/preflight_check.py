#!/usr/bin/env python3
"""Demo pre-flight: JIE health, portal up, env, and five locked LaborPulse questions.

Run from repo root with venv activated::

    python scripts/demo/preflight_check.py

Optional::

    python scripts/demo/preflight_check.py --jie-base-url http://127.0.0.1:8000
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

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=False)

from analytics.api._config import allow_no_api_keys
from common.data_store.database import _resolve_primary_database_url
from scripts.smoke.laborpulse._demo_common import (
    LOCKED_DEMO_QUESTIONS,
    default_jie_base_url,
    likely_refusal,
)


def _default_jie_base_url() -> str:
    """Backward-compatible alias for tests and callers."""
    return default_jie_base_url()


def _get(url: str, *, timeout: float = 10.0) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()[:200].decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read()[:200].decode("utf-8", errors="replace") if e.fp else "")
    except urllib.error.URLError as e:
        return 0, str(e.reason)


def _laborpulse_headers(*, request_id: str) -> dict[str, str]:
    h: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Tenant-Id": os.environ.get("ANALYTICS_QUERY_X_TENANT_ID", "borderplex").strip() or "borderplex",
        "X-User-Email": os.environ.get("ANALYTICS_QUERY_X_USER_EMAIL", "smoke@thewaifinder.com").strip()
        or "smoke@thewaifinder.com",
        "X-Request-Id": request_id,
    }
    xk = os.environ.get("ANALYTICS_QUERY_X_API_KEY", "").strip()
    if xk:
        h["X-API-Key"] = xk
    return h


def _post_query(base: str, question: str, *, req_id: str) -> tuple[int, dict]:
    url = f"{base.rstrip('/')}/analytics/query"
    data = json.dumps({"question": question}).encode()
    hdrs = _laborpulse_headers(request_id=req_id)
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


def _likely_refusal(answer: str) -> bool:
    return likely_refusal(answer)


def main() -> int:
    parser = argparse.ArgumentParser(description="Week 11 demo pre-flight checks.")
    parser.add_argument(
        "--jie-base-url",
        default=default_jie_base_url(),
        help="JIE Analytics API base URL (default from JIE_BASE_URL or http://127.0.0.1:8000).",
    )
    parser.add_argument("--portal-url", default="http://localhost:3000")
    args = parser.parse_args()
    jie_base = args.jie_base_url.rstrip("/")

    print("=== DEMO PREFLIGHT (checks) ===\n")

    # 1 JIE healthz
    status, _ = _get(f"{jie_base}/healthz")
    jie_ok = status == 200
    print(f"1. JIE GET {jie_base}/healthz → {'PASS' if jie_ok else 'FAIL'} (HTTP {status})")

    # 2 Portal
    p_status, _ = _get(args.portal_url)
    portal_ok = p_status == 200
    print(f"2. wfd-os GET {args.portal_url} → {'PASS' if portal_ok else 'FAIL'} (HTTP {p_status})")

    # 3 DB (PYTHON_DATABASE_URL or AZURE_POSTGRES_DATABASE_URL)
    db_url = _resolve_primary_database_url()
    db_ok = bool(db_url)
    print(f"3. Database URL set (PYTHON_DATABASE_URL or AZURE_POSTGRES_DATABASE_URL) → {'PASS' if db_ok else 'FAIL'}")

    # 4 LLM
    llm = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    llm_ok = bool(llm) and llm != "mock"
    print(f"4. LLM_PROVIDER set and not mock → {'PASS' if llm_ok else 'FAIL'} (value={llm or '(unset)'})")

    jie_keys = bool((os.getenv("JIE_API_KEYS") or "").strip())
    dev_no_keys = allow_no_api_keys()
    auth_backend_ok = jie_keys or dev_no_keys
    print(
        f"4b. JIE API keys (JIE_API_KEYS non-empty OR LaborPulse allow_no_api_keys) → "
        f"{'PASS' if auth_backend_ok else 'FAIL'} "
        f"(JIE_API_KEYS={'set' if jie_keys else 'empty'}, "
        f"LABORPULSE_ALLOW_NO_API_KEYS={dev_no_keys})"
    )
    client_key = bool((os.getenv("ANALYTICS_QUERY_X_API_KEY") or "").strip())
    client_auth_ok = dev_no_keys or (jie_keys and client_key)
    print(
        f"4c. Client X-API-Key (ANALYTICS_QUERY_X_API_KEY) when keys required → "
        f"{'PASS' if client_auth_ok else 'FAIL'} "
        f"(header={'set' if client_key else 'missing'}; empty JIE_API_KEYS → HTTP 500 "
        f"server_misconfigured_no_keys)"
    )

    print()
    print("5. Locked demo questions (POST /analytics/query)")
    questions_pass = 0
    for i, q in enumerate(LOCKED_DEMO_QUESTIONS, start=1):
        req_id = f"preflight-q{i}-{uuid.uuid4().hex[:8]}"
        http_st, body = _post_query(jie_base, q, req_id=req_id)
        answer = ""
        confidence = None
        evidence_count = 0
        if http_st == 200:
            answer = str(body.get("answer") or "")
            confidence = body.get("confidence")
            ev = body.get("evidence")
            evidence_count = len(ev) if isinstance(ev, list) else 0

        ok = bool(
            http_st == 200 and answer.strip() and not _likely_refusal(answer),
        )
        if ok:
            questions_pass += 1

        print(f"   Question {i}")
        print(f"   HTTP {http_st}")
        if http_st != 200:
            detail = body.get("detail", body)
            print(f"   Error detail: {detail}")
        print(f"   Answer received: {'yes' if ok else 'no'}")
        print(f"   Confidence: {confidence if confidence is not None else '(n/a)'}")
        print(f"   Evidence count: {evidence_count}")
        print(f"   → {'PASS' if ok else 'FAIL'}")
        print()

    svc_up = sum([jie_ok, portal_ok])
    env_ok = db_ok and llm_ok and auth_backend_ok and client_auth_ok
    ready = svc_up == 2 and questions_pass == 5 and env_ok

    print("=== DEMO PREFLIGHT ===")
    print(f"Services: {svc_up}/2 up")
    print(f"Questions: {questions_pass}/5 answered")
    print(f"Ready for demo: {'YES' if ready else 'NO'}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
