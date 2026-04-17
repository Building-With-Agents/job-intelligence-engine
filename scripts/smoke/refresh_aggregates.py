#!/usr/bin/env python3
"""Week 8 smoke test — populate Week 7 aggregate tables required by Streamlit pages.

Runs the analytics agent's ``process_aggregates`` (steps 2, 3, 8, 9) and
``run_pipeline`` (steps 6, 7) to populate the aggregate tables that the
Week 8 Streamlit dashboard reads from:

- ``skill_demand_weekly``   (step 2)
- ``tool_demand_weekly``    (step 3)
- ``sector_summary_weekly`` (step 6, via run_pipeline)
- ``geo_demand_weekly``     (step 7, via run_pipeline)
- ``skill_velocity``        (step 8)
- ``skill_co_occurrence``   (step 9)

Usage (from repo root with venv activated):

    python scripts/smoke/refresh_aggregates.py
    python scripts/smoke/refresh_aggregates.py --week 2026-04-14
    python scripts/smoke/refresh_aggregates.py --skip-pipeline   # steps 2,3,8,9 only

Requires ``PYTHON_DATABASE_URL`` in the repo-root ``.env`` and populated
``dbo.job_postings`` / ``dbo.extracted_intelligence``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from analytics.agent import AnalyticsAgent  # noqa: E402
from common.data_store.database import session_scope  # noqa: E402
from common.event_envelope import EventEnvelope  # noqa: E402


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Populate Week 7 analytics aggregate tables for the Streamlit dashboard.",
    )
    parser.add_argument(
        "--week",
        default=None,
        help="ISO date of the target Monday (e.g. 2026-04-14). Defaults to current week.",
    )
    parser.add_argument(
        "--skip-pipeline",
        action="store_true",
        help="Only run process_aggregates (steps 2,3,8,9); skip run_pipeline (steps 6,7).",
    )
    args = parser.parse_args()

    payload: dict = {}
    if args.week:
        payload["week_start"] = args.week

    def _envelope(p: dict | None = None) -> EventEnvelope:
        return EventEnvelope(
            correlation_id="smoke-refresh-aggregates",
            agent_id="analytics-agent",
            payload=p or payload,
        )

    agent = AnalyticsAgent()

    # --- Steps 2, 3, 8, 9: skill/tool demand, velocity, co-occurrence ---
    print("--- Step A: process_aggregates (steps 2, 3, 8, 9) ---")
    result = agent.process_aggregates(_envelope())
    refresh = result.payload.get("aggregate_refresh", {})

    for key in (
        "skill_demand_weekly_rows",
        "tool_demand_weekly_rows",
        "skill_velocity_rows",
        "skill_co_occurrence_rows",
    ):
        val = refresh.get(key, refresh.get(key.replace("_rows", "_error"), "skipped"))
        label = key.replace("_rows", "").replace("_", " ")
        print(f"  {label:30s} {val}")

    # --- Steps 6, 7: sector summary, geo demand (via full pipeline) ---
    if not args.skip_pipeline:
        print()
        print("--- Step B: run_pipeline (steps 6, 7 — sector + geo demand) ---")
        try:
            with session_scope() as session:
                pipeline_result = agent.run_pipeline(session, _envelope())
                if pipeline_result:
                    print("  pipeline completed")
                else:
                    print("  pipeline skipped (minimum data guard — not enough enriched postings)")
        except Exception as exc:
            print(f"  pipeline error: {type(exc).__name__}: {exc}")
    else:
        print()
        print("--- Step B: skipped (--skip-pipeline) ---")

    # --- Verify counts ---
    print()
    print("--- Verification ---")
    from dashboard.readonly_engine import get_dashboard_engine
    from dashboard.relation_safe import read_sql_relation_safe

    engine = get_dashboard_engine()
    tables = [
        "skill_demand_weekly",
        "tool_demand_weekly",
        "sector_summary_weekly",
        "geo_demand_weekly",
        "skill_velocity",
        "skill_co_occurrence",
        "posting_freshness",
    ]
    for t in tables:
        try:
            df, hint = read_sql_relation_safe(
                f"SELECT COUNT(*) AS n FROM dbo.{t}", engine
            )
            if hint:
                print(f"  {t:30s} WARN: {hint}")
            else:
                n = df.iloc[0]["n"]
                status = "OK" if n > 0 else "(empty)"
                print(f"  {t:30s} {n:>6}  {status}")
        except Exception as exc:
            print(f"  {t:30s} ERROR: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
