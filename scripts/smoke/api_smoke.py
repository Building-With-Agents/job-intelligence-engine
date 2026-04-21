#!/usr/bin/env python3
"""Week 8 smoke test — hit all FastAPI endpoints (Pair D).

Sends HTTP requests to the running Analytics API and prints structured
results. Covers: POST /analytics/query, the 4 trigger endpoints, and
the 24-hour cache verification (run cohort_gap_analysis twice).

Requires the API to be running first:

    python scripts/run_analytics_api.py

Usage (from any shell, any CWD):

    python scripts/smoke/api_smoke.py
    python scripts/smoke/api_smoke.py --base-url http://127.0.0.1:8000
    python scripts/smoke/api_smoke.py --question "Which skills are trending in El Paso?"
    python scripts/smoke/api_smoke.py --skip-query       # skip the Q&A endpoint (blocked by #186)
    python scripts/smoke/api_smoke.py --role-id <uuid>   # use a real canonical_role_id for role_benchmark
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _post(url: str, body: dict) -> tuple[int, dict]:
    """POST JSON and return (status_code, response_json)."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
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


def _print_result(label: str, status: int, body: dict, *, preview_keys: list[str] | None = None) -> None:
    print(f"\n{'=' * 70}")
    print(f"{label}")
    print(f"{'=' * 70}")
    print(f"HTTP {status}")
    if preview_keys:
        for k in preview_keys:
            val = body.get(k)
            if isinstance(val, str) and len(val) > 200:
                val = val[:200] + "..."
            print(f"  {k}: {val}")
    else:
        print(json.dumps(body, indent=2, default=str)[:600])


def main() -> int:
    parser = argparse.ArgumentParser(description="Hit all FastAPI endpoints and print results.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base URL.")
    parser.add_argument("--question", default="Which 5 skills have the highest posting counts?")
    parser.add_argument("--correlation-id", default="api-smoke-1")
    parser.add_argument("--role-id", default="role_1", help="canonical_role_id for role_benchmark trigger.")
    parser.add_argument("--skip-query", action="store_true", help="Skip POST /analytics/query (blocked by #186).")
    parser.add_argument(
        "--adversarial-role-id",
        default="bobby; DROP TABLE students--",
        help="Adversarial string to send as canonical_role_id to role_benchmark (default: SQL injection attempt).",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    results: list[tuple[str, bool]] = []

    # --- Step 1: Check API is up ---
    print(f"API base: {base}")
    status, body = _post(f"{base}/analytics/triggers/emerging_skills_scan", {"scan_key": "ping"})
    if status == 0:
        print(f"\nERROR: API not reachable at {base}")
        print("  Start it first: python scripts/run_analytics_api.py")
        print(f"  Detail: {body}")
        return 1
    print(f"API reachable (HTTP {status})")

    # --- Step 2: POST /analytics/query ---
    if not args.skip_query:
        label = f'POST /analytics/query  --  "{args.question}"'
        s, b = _post(
            f"{base}/analytics/query",
            {
                "question": args.question,
                "correlation_id": args.correlation_id,
            },
        )
        _print_result(
            label,
            s,
            b,
            preview_keys=[
                "answer",
                "confidence",
                "refused",
                "refusal_message",
                "cost_usd",
                "total_cost_usd",
                "cost_breakdown_usd",
            ],
        )
        results.append(("POST /analytics/query", s == 200))
    else:
        print("\n-- Skipping POST /analytics/query (--skip-query) --")

    # --- Step 3: 4 trigger endpoints ---
    triggers = [
        ("cohort_gap_analysis", {"cohort_key": "demo-cohort", "week_start": None}),
        ("role_benchmark", {"canonical_role_id": args.role_id, "week_start": None}),
        ("emerging_skills_scan", {"scan_key": "smoke-test"}),
        ("custom_employer_comparison", {"company_id": "company_42", "week_start": None}),
    ]
    for name, payload in triggers:
        label = f"POST /analytics/triggers/{name}"
        s, b = _post(f"{base}/analytics/triggers/{name}", payload)
        _print_result(label, s, b, preview_keys=["trigger", "cached", "computed_at"])
        results.append((label, s == 200))

    # --- Step 4: Cache verification (re-run cohort_gap_analysis) ---
    label = "Cache check: re-run cohort_gap_analysis (expect cached=true)"
    s, b = _post(
        f"{base}/analytics/triggers/cohort_gap_analysis",
        {
            "cohort_key": "demo-cohort",
            "week_start": None,
        },
    )
    _print_result(label, s, b, preview_keys=["trigger", "cached", "computed_at"])
    cached = b.get("cached", False)
    results.append(("Cache hit on 2nd call", cached is True))

    # --- Step 5: Adversarial SQL injection via trigger ---
    label = f"Adversarial: role_benchmark with role_id={args.adversarial_role_id!r}"
    s, b = _post(
        f"{base}/analytics/triggers/role_benchmark",
        {
            "canonical_role_id": args.adversarial_role_id,
        },
    )
    _print_result(label, s, b)
    results.append(("Adversarial rejection (expect 400)", s == 400))

    # --- Summary ---
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    for name, passed in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    total = sum(1 for _, p in results if p)
    print(f"\n{total}/{len(results)} passed")

    return 0 if all(p for _, p in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
