#!/usr/bin/env python3
"""Week 8 smoke test — validate a SQL string against the Ask-the-Data allowlist.

Calls :func:`analytics.query_engine.sql_guardrails.validate_ask_the_data_sql`
on user-provided SQL. The Ask-the-Data allowlist covers **operational** tables:
job_postings, companies, company_addresses, skills, technology_areas,
industry_sectors, normalized_jobs, raw_ingested_jobs.

Usage (from any shell, any CWD):

    python scripts/smoke/validate_atd_sql.py "SELECT job_title FROM dbo.job_postings LIMIT 10"
    python scripts/smoke/validate_atd_sql.py "DROP TABLE dbo.job_postings"
    python scripts/smoke/validate_atd_sql.py "SELECT * FROM dbo.skill_demand_weekly"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from analytics.query_engine.sql_guardrails import validate_ask_the_data_sql  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a SQL string against the Ask-the-Data (operational) allowlist.",
    )
    parser.add_argument("sql", help="The SQL string to validate.")
    args = parser.parse_args()

    print(f"SQL:            {args.sql}")
    print(f"Guardrail:      validate_ask_the_data_sql (operational tables)")
    print()

    ok, reason, normalized = validate_ask_the_data_sql(args.sql)

    print(f"ok:             {ok}")
    print(f"reason:         {reason}")
    print(f"normalized_sql: {normalized}")
    print()
    print("PASS" if ok else "REJECTED")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
