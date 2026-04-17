#!/usr/bin/env python3
"""Week 8 smoke test — SQL guardrails happy-path check (Pairs C + D).

Tests both guardrail functions with known-good SQL against each allowlist
to verify valid queries pass and LIMIT caps are normalized correctly.

The JIE has two guardrail paths with different table allowlists:

  validate_ask_the_data_sql()  — operational tables (job_postings, companies, ...)
  validate_sql()               — aggregate tables (skill_demand_weekly, canonical_roles, ...)

Usage (from any shell, any CWD):

    python scripts/smoke/sql_guardrails_check.py

No arguments — uses hardcoded test queries. Does not require a database
connection (guardrails are pure SQL parsing via sqlglot).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from analytics.query_engine.sql_guardrails import validate_ask_the_data_sql, validate_sql  # noqa: E402


def main() -> int:
    print("=" * 70)
    print("SQL GUARDRAILS — HAPPY-PATH CHECK")
    print("=" * 70)

    # --- Test 1: Ask-the-Data path (operational tables) ---
    sql_atd = (
        "SELECT job_title, location, salary_range "
        "FROM dbo.job_postings "
        "WHERE borderplex_subregion = 'el_paso' "
        "ORDER BY publish_date DESC LIMIT 20"
    )
    print()
    print("Test 1: validate_ask_the_data_sql (operational path)")
    print(f"  SQL: {sql_atd}")
    ok, reason, normalized = validate_ask_the_data_sql(sql_atd)
    print(f"  ok:             {ok}")
    print(f"  reason:         {reason}")
    print(f"  normalized_sql: {normalized}")
    print(f"  -> {'PASS' if ok else 'FAIL'}: job_postings is in the Ask-the-Data allowlist")

    # --- Test 2: Ask-the-Data path — aggregate table should be REJECTED ---
    sql_atd_reject = (
        "SELECT skill_label, posting_count "
        "FROM dbo.skill_demand_weekly "
        "ORDER BY posting_count DESC LIMIT 50"
    )
    print()
    print("Test 2: validate_ask_the_data_sql (aggregate table — should REJECT)")
    print(f"  SQL: {sql_atd_reject}")
    ok2, reason2, _ = validate_ask_the_data_sql(sql_atd_reject)
    print(f"  ok:     {ok2}")
    print(f"  reason: {reason2}")
    print(f"  -> {'PASS' if not ok2 else 'FAIL'}: skill_demand_weekly is NOT in the Ask-the-Data allowlist (it's an aggregate table)")

    # --- Test 3: Aggregate path — valid query, LIMIT over cap ---
    sql_agg = "SELECT skill_label, posting_count FROM dbo.skill_demand_weekly LIMIT 500"
    print()
    print("Test 3: validate_sql (aggregate path — LIMIT 500 should be capped to 100)")
    print(f"  SQL: {sql_agg}")
    res = validate_sql(sql_agg)
    print(f"  ok:                {res.ok}")
    print(f"  reason:            {res.reason}")
    print(f"  sql_for_execution: {res.sql_for_execution}")
    capped = res.ok and "LIMIT 100" in (res.sql_for_execution or "")
    print(f"  -> {'PASS' if capped else 'FAIL'}: LIMIT was capped from 500 to 100 (MAX_ROWS)")

    # --- Test 4: Aggregate path — valid query within cap ---
    sql_agg_ok = "SELECT skill_label, posting_count FROM dbo.skill_demand_weekly ORDER BY posting_count DESC LIMIT 10"
    print()
    print("Test 4: validate_sql (aggregate path — LIMIT 10 within cap, should pass as-is)")
    print(f"  SQL: {sql_agg_ok}")
    res2 = validate_sql(sql_agg_ok)
    print(f"  ok:                {res2.ok}")
    print(f"  reason:            {res2.reason}")
    print(f"  sql_for_execution: {res2.sql_for_execution}")
    print(f"  -> {'PASS' if res2.ok else 'FAIL'}: valid aggregate query accepted")

    # --- Summary ---
    tests = [ok, not ok2, capped, res2.ok]
    passed = sum(tests)
    print()
    print(f"Summary: {passed}/{len(tests)} passed")

    return 0 if all(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
