#!/usr/bin/env python3
"""Week 8 smoke test — verify orchestration audit log writes (Pair D).

Sends Q&A requests to the running FastAPI API with distinct correlation IDs,
then queries ``dbo.orchestration_audit_log`` to confirm rows landed with
populated fields.  Also sends one adversarial request to verify the
failure-path audit row.

Requires the API to be running first:

    python scripts/run_analytics_api.py

Set ``ANALYTICS_QUERY_X_API_KEY`` when the API enforces ``JIE_API_KEYS`` (JIE #226). Optional
``ANALYTICS_QUERY_X_TENANT_ID``, ``ANALYTICS_QUERY_X_USER_EMAIL`` tune LaborPulse headers for
``POST /analytics/query`` (JIE #222); each audit request sends a distinct ``X-Request-Id``.

Usage (from any shell, any CWD):

    python scripts/smoke/audit_log_check.py
    python scripts/smoke/audit_log_check.py --base-url http://127.0.0.1:8000
    python scripts/smoke/audit_log_check.py --question "Top employers by posting count"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# Ensure repo root is on sys.path so common.* imports resolve
# when the script is invoked from any CWD.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from common.data_store.database import session_scope  # noqa: E402
from common.data_store.models import OrchestrationAuditLog  # noqa: E402


def _post(url: str, body: dict, *, extra_headers: dict[str, str] | None = None) -> tuple[int, dict]:
    """POST JSON and return (status_code, response_json)."""
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        try:
            return e.code, json.loads(body_text)
        except json.JSONDecodeError:
            return e.code, {"raw": body_text}
    except urllib.error.URLError as e:
        return 0, {"error": str(e.reason)}


def _laborpulse_query_headers(*, request_id: str | None = None) -> dict[str, str]:
    """Headers for ``POST /analytics/query`` (JIE #222); ``Content-Type`` is set in ``_post``."""
    h: dict[str, str] = {
        "X-Tenant-Id": os.environ.get("ANALYTICS_QUERY_X_TENANT_ID", "borderplex").strip() or "borderplex",
        "X-User-Email": os.environ.get("ANALYTICS_QUERY_X_USER_EMAIL", "smoke@thewaifinder.com").strip()
        or "smoke@thewaifinder.com",
        "X-Request-Id": request_id
        or os.environ.get("ANALYTICS_QUERY_X_REQUEST_ID", "").strip()
        or str(uuid.uuid4()),
    }
    xk = os.environ.get("ANALYTICS_QUERY_X_API_KEY", "").strip()
    if xk:
        h["X-API-Key"] = xk
    return h


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify orchestration audit log writes after Q&A requests.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="API base URL (default: http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--question",
        default="Top skills by posting count",
        help="Question to send in Q&A requests.",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    correlation_ids = ["audit-check-1", "audit-check-2", "audit-check-3"]
    adversarial_cid = "audit-check-adversarial"
    results: list[tuple[str, bool]] = []

    # --- Check API is up ---
    print(f"API base: {base}")
    status, body = _post(
        f"{base}/analytics/triggers/emerging_skills_scan",
        {},
    )
    if status == 0:
        print(f"\nERROR: API not reachable at {base}")
        print("  Start it first: python scripts/run_analytics_api.py")
        print(f"  Detail: {body}")
        return 1
    print(f"API reachable (HTTP {status})")

    # --- Step 1: Send 3 Q&A requests with distinct correlation IDs ---
    print("\n--- Sending 3 Q&A requests ---")
    for cid in correlation_ids:
        s, b = _post(
            f"{base}/analytics/query",
            {"question": args.question},
            extra_headers=_laborpulse_query_headers(request_id=cid),
        )
        status_label = "OK" if s == 200 else f"HTTP {s}"
        print(f"  {cid}: {status_label}")

    # --- Step 2: Send 1 adversarial request ---
    print("\n--- Sending adversarial request ---")
    s, b = _post(
        f"{base}/analytics/triggers/role_benchmark",
        {
            "canonical_role_id": "bad;role--injection",
        },
    )
    print(f"  {adversarial_cid}: HTTP {s} (expected 400)")

    # Brief pause for async commits
    time.sleep(1)

    # --- Step 3: Query audit log for the correlation IDs ---
    print("\n--- Checking dbo.orchestration_audit_log ---")
    all_cids = correlation_ids + [adversarial_cid]

    with session_scope() as session:
        rows = (
            session.query(OrchestrationAuditLog)
            .filter(OrchestrationAuditLog.correlation_id.in_(all_cids))
            .order_by(OrchestrationAuditLog.created_at.desc())
            .all()
        )

        found_cids = {r.correlation_id for r in rows}

        # Check 1: rows exist for the 3 happy-path requests
        for cid in correlation_ids:
            present = cid in found_cids
            results.append((f"Row exists for {cid}", present))
            if not present:
                print(f"  MISS: no audit row for correlation_id={cid}")

        # Check 2: endpoint populated
        for r in rows:
            if r.correlation_id in correlation_ids:
                has_endpoint = bool(r.endpoint)
                results.append((f"endpoint populated ({r.correlation_id})", has_endpoint))

        # Check 3: question_hash length = 64
        for r in rows:
            if r.correlation_id in correlation_ids:
                qh = getattr(r, "question_hash", None) or ""
                ok = len(qh) == 64
                results.append((f"question_hash len=64 ({r.correlation_id})", ok))
                if not ok:
                    print(f"  question_hash for {r.correlation_id}: len={len(qh)} (expected 64)")

        # Check 4: success field populated
        for r in rows:
            if r.correlation_id in correlation_ids:
                has_success = r.success is not None
                results.append((f"success populated ({r.correlation_id})", has_success))

        # Check 5: adversarial request audit row
        adversarial_present = adversarial_cid in found_cids
        results.append((f"Adversarial audit row ({adversarial_cid})", adversarial_present))
        if not adversarial_present:
            print(
                f"  NOTE: No audit row for adversarial request ({adversarial_cid}).\n"
                f"        This is expected — failure-path auditing for triggers is a\n"
                f"        known gap (#187). Not a blocker."
            )

    # --- Summary ---
    print(f"\n{'=' * 60}")
    print("AUDIT LOG CHECK SUMMARY")
    print(f"{'=' * 60}")
    for name, passed in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    total = sum(1 for _, p in results if p)
    print(f"\n{total}/{len(results)} passed")

    if not adversarial_present:
        print(
            "\nNOTE: Adversarial audit row missing is expected (#187).\n"
            "      Audit writes happen only at the API route-handler layer.\n"
            "      Direct function calls and rejected triggers may not audit."
        )

    return 0 if all(p for _, p in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
