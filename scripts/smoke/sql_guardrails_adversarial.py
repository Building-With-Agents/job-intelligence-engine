#!/usr/bin/env python3
"""Week 8 smoke test — SQL guardrails adversarial verification.

Runs 9 known-bad SQL strings through
:func:`analytics.query_engine.sql_guardrails.validate_ask_the_data_sql` and 6
through :func:`analytics.query_engine.sql_guardrails.validate_sql`, confirming
that every malicious or out-of-scope statement is rejected at the guardrail
before execution.

Usage (from any shell, any CWD):

    python scripts/smoke/sql_guardrails_adversarial.py

No arguments — uses hardcoded adversarial test battery.  Does not require a
database connection (guardrails are pure SQL parsing).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path so analytics.* / common.* imports resolve
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from analytics.query_engine.sql_guardrails import validate_ask_the_data_sql, validate_sql  # noqa: E402


def main() -> int:
    passed = 0
    failed = 0

    # --- ask_the_data adversarial cases (all must return ok=False) ---
    cases_atd = [
        ("DROP TABLE", "DROP TABLE dbo.job_postings"),
        ("INSERT", "INSERT INTO dbo.job_postings (id) VALUES (1)"),
        ("UNION over disallowed", "SELECT 1 UNION SELECT * FROM pg_shadow"),
        ("multi-statement", "SELECT 1; SELECT 2"),
        ("comment bypass", "SELECT * FROM dbo.job_postings -- ; DROP TABLE x"),
        ("subquery forbidden table", "SELECT * FROM dbo.job_postings WHERE id IN (SELECT id FROM pg_authid)"),
        ("disallowed schema", "SELECT * FROM pg_catalog.pg_tables"),
        ("disallowed table", "SELECT * FROM dbo.llm_audit_log"),
        ("not SELECT", "TRUNCATE TABLE dbo.job_postings"),
    ]

    print("=== ask_the_data adversarial cases (expect ok=False) ===\n")
    for name, sql in cases_atd:
        ok, reason, norm = validate_ask_the_data_sql(sql)
        if ok is False:
            status = "PASS"
            passed += 1
        else:
            status = "FAIL"
            failed += 1
        print(f"  [{status}] {name!r:30} ok={ok!s:5} reason={reason}")

    # --- aggregate adversarial cases ---
    cases_agg = [
        ("DROP in aggregate", "DROP TABLE dbo.skill_demand_weekly"),
        ("INSERT in aggregate", "INSERT INTO dbo.skill_demand_weekly (id) VALUES (1)"),
        ("UPDATE forbidden", "UPDATE dbo.skill_demand_weekly SET posting_count=0"),
        ("multi-statement", "SELECT 1; SELECT 2"),
        ("non-allowlisted table", "SELECT * FROM dbo.job_postings"),
        ("LIMIT over cap", "SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 5000"),
    ]

    print("\n=== aggregate adversarial cases ===\n")
    for name, sql in cases_agg:
        res = validate_sql(sql)
        # All must be rejected EXCEPT "LIMIT over cap" which should be ok=True
        # with sql_for_execution rewritten to LIMIT 100.
        if name == "LIMIT over cap":
            if res.ok is True and "LIMIT 100" in (res.sql_for_execution or ""):
                status = "PASS"
                passed += 1
            else:
                status = "FAIL"
                failed += 1
        else:
            if res.ok is False:
                status = "PASS"
                passed += 1
            else:
                status = "FAIL"
                failed += 1
        exec_preview = (res.sql_for_execution or "")[:80] if res.sql_for_execution else ""
        print(f"  [{status}] {name!r:30} ok={res.ok!s:5} reason={str(res.reason):40} sql={exec_preview}")

    # --- summary ---
    total = passed + failed
    print(f"\n{'=' * 50}")
    print(f"PASSED: {passed}/{total}   FAILED: {failed}/{total}")
    if failed > 0:
        print("*** SOME GUARDRAIL CHECKS FAILED ***")
        return 1
    print("All guardrail checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
