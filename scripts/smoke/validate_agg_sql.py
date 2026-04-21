#!/usr/bin/env python3
"""Week 8 smoke test — validate a SQL string against the aggregate allowlist.

Calls :func:`analytics.query_engine.sql_guardrails.validate_sql` on
user-provided SQL. The aggregate allowlist covers **analytics** tables:
skill_demand_weekly, tool_demand_weekly, role_snapshot_weekly,
sector_summary_weekly, geo_demand_weekly, skill_velocity,
skill_co_occurrence, posting_freshness, trajectory_map,
analytics_pipeline_state, cohort_gap_cache, canonical_roles.

LIMIT values above MAX_ROWS (100) are silently capped, not rejected.

Usage (from any shell, any CWD):

    python scripts/smoke/validate_agg_sql.py "SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 10"
    python scripts/smoke/validate_agg_sql.py "SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 5000"
    python scripts/smoke/validate_agg_sql.py "DROP TABLE dbo.skill_demand_weekly"
    python scripts/smoke/validate_agg_sql.py "SELECT * FROM dbo.job_postings"
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

from analytics.query_engine.sql_guardrails import validate_sql  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a SQL string against the aggregate allowlist.",
    )
    parser.add_argument("sql", help="The SQL string to validate.")
    args = parser.parse_args()

    print(f"SQL:              {args.sql}")
    print("Guardrail:        validate_sql (aggregate tables)")
    print()

    res = validate_sql(args.sql)

    print(f"ok:               {res.ok}")
    print(f"reason:           {res.reason}")
    print(f"sql_for_execution: {res.sql_for_execution}")
    print()
    print("PASS" if res.ok else "REJECTED")

    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
