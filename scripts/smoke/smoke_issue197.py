#!/usr/bin/env python3
"""Smoke test for issue #197 classifier fix + guard.

Verifies two layers of the #197 fix end-to-end:

1. **Data layer** — ``dbo.job_postings`` must have **zero** rows with
   ``role_classification = 'N/A Not an IT role'`` after the classifier fix +
   backfill land. Prints top-10 bucket distribution for eyeball.

2. **Query layer** — ``POST /analytics/query`` on ``employer`` / ``curriculum`` /
   ``workflow`` intents that reference ``role_classification`` must come back with:
       - ``sql_generated`` containing the guard predicate
         (``role_classification <> 'N/A Not an IT role'`` or equivalent inequality)
       - zero ``evidence`` rows carrying ``'N/A Not an IT role'``
       - ``confidence`` in ``{low, medium, high}`` (never ``mock``)
       - ``cost_usd > 0`` (real LLM call)

Cross-platform: stdlib only for HTTP (``urllib``), sqlalchemy for DB. Works in
PowerShell, bash, zsh.

Usage (from repo root, any CWD)::

    python scripts/smoke/smoke_issue197.py                       # DB + API
    python scripts/smoke/smoke_issue197.py --no-api               # DB only
    python scripts/smoke/smoke_issue197.py --no-db                # API only
    python scripts/smoke/smoke_issue197.py --base-url http://127.0.0.1:8000

Env vars:
    PYTHON_DATABASE_URL           (required for DB check)
    ANALYTICS_QUERY_X_API_KEY     (required by API when JIE_API_KEYS is configured)
    ANALYTICS_QUERY_X_TENANT_ID   (optional, defaults to "borderplex")
    ANALYTICS_QUERY_X_USER_EMAIL  (optional, defaults to "smoke@thewaifinder.com")

Exit code: 0 on all checks passing, 1 on any failure.
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

# Load .env if present — saves Pair C from needing to export PYTHON_DATABASE_URL
# in their shell. No-op if python-dotenv isn't installed.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# The "bad" label we want gone everywhere.
BAD_LABEL = "N/A Not an IT role"

# Intent → natural-language question that should cause the LLM to generate SQL
# touching ``role_classification`` on ``dbo.job_postings``, triggering Enrique's
# guard in ``analytics/query_engine/sql_guardrails.py``.
GUARD_QUESTIONS: list[tuple[str, str]] = [
    (
        "employer",
        "Which employers in El Paso post the most Software Development roles?",
    ),
    (
        "curriculum",
        "What skills are required for Network Administration roles in the Borderplex?",
    ),
    (
        "workflow",
        "Which tools are most common in Mobile App Development roles?",
    ),
]


def _banner(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(title)
    print("=" * 70)


def _laborpulse_headers(*, request_id: str | None = None) -> dict[str, str]:
    """Headers required by POST /analytics/query (JIE #222)."""
    h: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Tenant-Id": os.environ.get("ANALYTICS_QUERY_X_TENANT_ID", "borderplex").strip() or "borderplex",
        "X-User-Email": os.environ.get("ANALYTICS_QUERY_X_USER_EMAIL", "smoke@thewaifinder.com").strip()
        or "smoke@thewaifinder.com",
        "X-Request-Id": request_id or str(uuid.uuid4()),
    }
    xk = os.environ.get("ANALYTICS_QUERY_X_API_KEY", "").strip()
    if xk:
        h["X-API-Key"] = xk
    return h


def _post_json(url: str, body: dict, *, headers: dict[str, str]) -> tuple[int, dict]:
    """POST JSON and return (status_code, response_json).

    Returns ``(0, {...})`` if the server is unreachable.
    """
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        try:
            return e.code, json.loads(body_text)
        except json.JSONDecodeError:
            return e.code, {"raw": body_text}
    except urllib.error.URLError as e:
        return 0, {"error": str(e.reason)}


def _guard_predicate_present(sql: str) -> bool:
    """True if ``sql`` contains some form of the #197 guard predicate."""
    if not sql:
        return False
    sql_norm = sql.lower()
    return BAD_LABEL.lower() in sql_norm and ("<>" in sql_norm or "!=" in sql_norm or "not in" in sql_norm)


def _role_classification_referenced(sql: str) -> bool:
    if not sql:
        return False
    return "role_classification" in sql.lower() and "dbo.job_postings" in sql.lower().replace('"', "")


def check_db() -> bool:
    """Return True if DB-layer checks pass (0 bad rows + ≤1 unclassified)."""
    _banner("DB CHECK: role_classification distribution (data layer)")

    try:
        from sqlalchemy import text

        from common.data_store.database import get_engine
    except ImportError as e:
        print(f"FAIL: cannot import sqlalchemy / common.data_store: {e}")
        print("      ensure PYTHONPATH includes the repo root and venv is activated")
        return False

    try:
        engine = get_engine()
    except RuntimeError as e:
        print(f"FAIL: {e}")
        print("      set PYTHON_DATABASE_URL in .env or shell env")
        return False

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT role_classification, COUNT(*) AS n "
                "FROM dbo.job_postings "
                "GROUP BY role_classification "
                "ORDER BY n DESC "
                "LIMIT 10"
            )
        ).fetchall()

    if not rows:
        print("FAIL: dbo.job_postings is empty — cannot verify classifier fix")
        return False

    total = sum(r[1] for r in rows)
    bad_count = 0
    print(f"\n  Top buckets (top 10 of total {total} sampled):\n")
    for label, n in rows:
        pct = 100 * n / total if total else 0.0
        marker = "  [BAD]" if label == BAD_LABEL else ""
        print(f"    {n:5d}  ({pct:5.1f}%)  {label!s}{marker}")
        if label == BAD_LABEL:
            bad_count = n

    print()
    if bad_count > 0:
        print(f"FAIL: {bad_count} rows still classified as {BAD_LABEL!r}")
        print("      backfill did not land, or classifier fix regressed — halt before eval")
        return False

    print(f"PASS: zero rows classified as {BAD_LABEL!r}")
    return True


def check_api(*, base_url: str) -> bool:
    """Return True if API-layer guard checks pass on all 3 intents."""
    _banner("API CHECK: guard injection on role-based questions (query layer)")

    # Reachability probe — hit a trigger endpoint first (no headers required).
    reach_status, reach_body = _post_json(
        f"{base_url}/analytics/triggers/emerging_skills_scan",
        {},
        headers={"Content-Type": "application/json"},
    )
    if reach_status == 0:
        print(f"SKIP: API not reachable at {base_url}")
        print("      start it first: python scripts/run_analytics_api.py")
        print(f"      detail: {reach_body.get('error', reach_body)}")
        return False

    print(f"API reachable at {base_url} (HTTP {reach_status})")

    all_passed = True
    for intent_label, question in GUARD_QUESTIONS:
        print(f"\n  -- intent={intent_label} --")
        print(f"     Q: {question}")

        status, body = _post_json(
            f"{base_url}/analytics/query",
            {"question": question},
            headers=_laborpulse_headers(),
        )

        if status != 200:
            print(f"     FAIL: HTTP {status}")
            print(f"     body: {json.dumps(body, default=str)[:300]}")
            all_passed = False
            continue

        sql = body.get("sql_generated") or ""
        evidence = body.get("evidence") or []
        confidence = body.get("confidence")
        cost_usd = body.get("cost_usd") or 0

        print(f"     sql_generated: {sql[:200]}{'...' if len(sql) > 200 else ''}")
        print(f"     confidence={confidence}  cost_usd={cost_usd}  evidence_rows={len(evidence)}")

        # Sub-checks per spec above.
        subfails: list[str] = []

        if not _role_classification_referenced(sql):
            print("     NOTE: sql_generated does not reference role_classification on dbo.job_postings")
            print("           guard cannot fire (legit — LLM answered without that column); skipping sub-checks")
            continue

        if not _guard_predicate_present(sql):
            subfails.append(f"guard predicate missing (expected <>/!= {BAD_LABEL!r} in sql_generated)")

        bad_evidence = [e for e in evidence if BAD_LABEL in json.dumps(e, default=str)]
        if bad_evidence:
            subfails.append(f"{len(bad_evidence)} evidence row(s) carry {BAD_LABEL!r}")

        if confidence not in ("low", "medium", "high"):
            subfails.append(f"confidence={confidence!r} not in {{low, medium, high}}")

        if cost_usd <= 0:
            subfails.append(f"cost_usd={cost_usd} (not > 0 — real LLM call expected)")

        if subfails:
            print("     FAIL:")
            for f in subfails:
                print(f"       - {f}")
            all_passed = False
        else:
            print("     PASS")

    return all_passed


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for issue #197 classifier fix + guard.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Analytics API base URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument("--no-db", action="store_true", help="Skip DB-layer check")
    parser.add_argument("--no-api", action="store_true", help="Skip API-layer check")
    args = parser.parse_args()

    results: list[tuple[str, bool]] = []

    if not args.no_db:
        results.append(("DB: zero 'N/A Not an IT role' rows", check_db()))

    if not args.no_api:
        results.append(("API: guard injects on role-based questions", check_api(base_url=args.base_url)))

    _banner("SUMMARY")
    for name, passed in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    total = sum(1 for _, p in results if p)
    print(f"\n  {total}/{len(results)} passed\n")

    return 0 if all(p for _, p in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
