# ruff: noqa: T201
"""Employer Drill-Down Audit — five structured tests against the JIE database.

Tests
-----
1. Join Path Integrity      : Trace row counts hop-by-hop for "Data Engineer"
                              (skill_demand_weekly → extracted_intelligence
                               → job_postings → employer_profiles).
2. Integrity Audit          : Success rate, orphan count, and fan-out ratio
                              for each adjacent pair in that chain.
3. Employer Profile Health  : Total profiles + Unknown/null name rate.
4. Sector Connection Test   : skill_demand_weekly ↔ sector_summary_weekly for
                              'Python' via week_start; detects empty-table block.
5. Gap Report               : SEVERITY 1 banners for any test that returns
                              < 5 rows or raises an exception.

Usage (from repo root, venv activated):

    python scripts/employer_drill_down_audit.py

Reads PYTHON_DATABASE_URL from .env automatically.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from common.data_store.database import get_engine  # noqa: E402
from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

_DIV = "=" * 64
_GAP_THRESHOLD = 5  # fewer than this many rows triggers SEVERITY 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _q(sql: str) -> list[Any]:
    """Execute a SELECT and return all rows."""
    with get_engine().connect() as conn:
        return conn.execute(text(sql)).fetchall()


def _scalar(sql: str) -> int:
    """Execute a COUNT query and return the integer result."""
    rows = _q(sql)
    return int(rows[0][0]) if rows else 0


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "N/A"
    return f"{100.0 * numerator / denominator:.1f}%"


def _drop_label(prev: int, curr: int) -> str:
    if prev == 0:
        return ""
    pct = 100.0 * (prev - curr) / prev
    return f"  (drop: -{pct:.0f}%)"


def _header(title: str) -> None:
    print(f"\n{_DIV}")
    print(title)
    print(_DIV)


def _footer() -> None:
    print(_DIV)


# ---------------------------------------------------------------------------
# Gap accumulator — filled by each test, printed by test 5
# ---------------------------------------------------------------------------

_gaps: list[dict[str, str]] = []


def _register_gap(test: str, table: str, issue: str, row_count: int | None = None) -> None:
    _gaps.append({"test": test, "table": table, "issue": issue, "row_count": row_count})


# ---------------------------------------------------------------------------
# TEST 1 — Join Path Integrity (row count trace)
# ---------------------------------------------------------------------------


def test1_join_path_integrity() -> None:
    _header("TEST 1 — Join Path Integrity (row count trace)")
    print("  Tracing 'Data Engineer' rows hop-by-hop through the chain.\n")

    # Step A: skill_demand_weekly — baseline rows matching 'data engineer'
    count_sdw = _scalar("SELECT COUNT(*) FROM dbo.skill_demand_weekly WHERE skill_label ILIKE '%data engineer%'")

    # Step B: extracted_intelligence rows where skills JSONB contains 'Data Engineer'
    count_ei = _scalar(
        """
        SELECT COUNT(DISTINCT ei.id)
        FROM dbo.extracted_intelligence ei,
             jsonb_array_elements(ei.skills) AS skill
        WHERE skill->>'skill_name' ILIKE '%data engineer%'
        """
    )

    # Step C: job_postings reachable from those extracted_intelligence rows
    #         (two-hop: ei → normalized_jobs → job_postings)
    count_jp = _scalar(
        """
        SELECT COUNT(DISTINCT jp.job_posting_id)
        FROM dbo.job_postings jp
        JOIN dbo.normalized_jobs nj
          ON jp.source = nj.source AND jp.external_id = nj.external_id
        JOIN dbo.extracted_intelligence ei ON ei.normalized_job_id = nj.id,
             jsonb_array_elements(ei.skills) AS skill
        WHERE skill->>'skill_name' ILIKE '%data engineer%'
        """
    )

    # Step D: employer_profiles reachable via job_postings.employer_profile_id
    count_ep = _scalar(
        """
        SELECT COUNT(DISTINCT ep.id)
        FROM dbo.employer_profiles ep
        JOIN dbo.job_postings jp ON jp.employer_profile_id = ep.id
        JOIN dbo.normalized_jobs nj
          ON jp.source = nj.source AND jp.external_id = nj.external_id
        JOIN dbo.extracted_intelligence ei ON ei.normalized_job_id = nj.id,
             jsonb_array_elements(ei.skills) AS skill
        WHERE skill->>'skill_name' ILIKE '%data engineer%'
        """
    )

    steps = [
        ("skill_demand_weekly (data engineer)", count_sdw),
        ("extracted_intelligence", count_ei),
        ("job_postings", count_jp),
        ("employer_profiles", count_ep),
    ]

    # Find largest absolute drop
    drops = []
    for i in range(1, len(steps)):
        prev_count = steps[i - 1][1]
        curr_count = steps[i][1]
        drops.append(prev_count - curr_count if prev_count > 0 else 0)
    largest_drop_idx = drops.index(max(drops)) + 1 if drops else -1

    col_w = max(len(s[0]) for s in steps) + 2
    prev = None
    for idx, (label, count) in enumerate(steps):
        drop = _drop_label(prev, count) if prev is not None else ""
        marker = "  ← LARGEST LEAK" if idx == largest_drop_idx else ""
        print(f"  {label:<{col_w}}: {count:>6} rows{drop}{marker}")
        prev = count

    _footer()

    # Register gaps
    if count_ep < _GAP_THRESHOLD:
        _register_gap(
            test="Test 1 — Join Path Integrity",
            table="employer_profiles",
            issue=(
                f"{count_ep} rows reached via job_postings.employer_profile_id "
                "(likely 100% NULL — FK never populated by enrichment agent)"
            ),
            row_count=count_ep,
        )
    if count_ei < _GAP_THRESHOLD:
        _register_gap(
            test="Test 1 — Join Path Integrity",
            table="extracted_intelligence",
            issue=f"Only {count_ei} rows contain 'data engineer' skill_name in skills JSONB",
            row_count=count_ei,
        )


# ---------------------------------------------------------------------------
# TEST 2 — Integrity Audit (success rate, orphan count, fan-out ratio)
# ---------------------------------------------------------------------------


def test2_integrity_audit() -> None:
    _header("TEST 2 — Integrity Audit (success rate / orphan count / fan-out ratio)")
    print(
        "  For each adjacent pair in the chain, calculates:\n"
        "    Success Rate  = % of source rows that join to the next table\n"
        "    Orphan Count  = source rows that fail to join\n"
        "    Fan-out Ratio = avg child rows per parent (> 1.0 = inflation risk)\n"
    )

    # ---- Hop 1: extracted_intelligence → normalized_jobs ----
    ei_total = _scalar("SELECT COUNT(*) FROM dbo.extracted_intelligence")
    ei_joined = _scalar(
        """
        SELECT COUNT(DISTINCT ei.id)
        FROM dbo.extracted_intelligence ei
        JOIN dbo.normalized_jobs nj ON nj.id = ei.normalized_job_id
        """
    )
    ei_orphans = ei_total - ei_joined
    # fan-out: avg nj rows per ei row (should be 1:1)
    ei_fanout_rows = _q(
        """
        SELECT COALESCE(AVG(child_count), 0)
        FROM (
            SELECT ei.id, COUNT(nj.id) AS child_count
            FROM dbo.extracted_intelligence ei
            LEFT JOIN dbo.normalized_jobs nj ON nj.id = ei.normalized_job_id
            GROUP BY ei.id
        ) sub
        """
    )
    ei_fanout = float(ei_fanout_rows[0][0]) if ei_fanout_rows else 0.0

    # ---- Hop 2: normalized_jobs → job_postings ----
    nj_total = _scalar("SELECT COUNT(*) FROM dbo.normalized_jobs")
    nj_joined = _scalar(
        """
        SELECT COUNT(DISTINCT nj.id)
        FROM dbo.normalized_jobs nj
        JOIN dbo.job_postings jp
          ON jp.source = nj.source AND jp.external_id = nj.external_id
        """
    )
    nj_orphans = nj_total - nj_joined
    nj_fanout_rows = _q(
        """
        SELECT COALESCE(AVG(child_count), 0)
        FROM (
            SELECT nj.id, COUNT(jp.job_posting_id) AS child_count
            FROM dbo.normalized_jobs nj
            LEFT JOIN dbo.job_postings jp
              ON jp.source = nj.source AND jp.external_id = nj.external_id
            GROUP BY nj.id
        ) sub
        """
    )
    nj_fanout = float(nj_fanout_rows[0][0]) if nj_fanout_rows else 0.0

    # ---- Hop 3: job_postings → employer_profiles ----
    jp_total = _scalar("SELECT COUNT(*) FROM dbo.job_postings")
    jp_joined = _scalar(
        """
        SELECT COUNT(DISTINCT jp.job_posting_id)
        FROM dbo.job_postings jp
        JOIN dbo.employer_profiles ep ON ep.id = jp.employer_profile_id
        """
    )
    jp_orphans = jp_total - jp_joined
    jp_fanout_rows = _q(
        """
        SELECT COALESCE(AVG(child_count), 0)
        FROM (
            SELECT jp.job_posting_id, COUNT(ep.id) AS child_count
            FROM dbo.job_postings jp
            LEFT JOIN dbo.employer_profiles ep ON ep.id = jp.employer_profile_id
            GROUP BY jp.job_posting_id
        ) sub
        """
    )
    jp_fanout = float(jp_fanout_rows[0][0]) if jp_fanout_rows else 0.0

    hops = [
        (
            "extracted_intelligence → normalized_jobs",
            ei_total,
            ei_joined,
            ei_orphans,
            ei_fanout,
        ),
        (
            "normalized_jobs → job_postings",
            nj_total,
            nj_joined,
            nj_orphans,
            nj_fanout,
        ),
        (
            "job_postings → employer_profiles",
            jp_total,
            jp_joined,
            jp_orphans,
            jp_fanout,
        ),
    ]

    for label, total, joined, orphans, fanout in hops:
        success = _pct(joined, total)
        print(f"  {label}")
        print(f"    Source rows   : {total:>6}")
        print(f"    Joined rows   : {joined:>6}  ({success} success rate)")
        print(f"    Orphan rows   : {orphans:>6}  ({_pct(orphans, total)} orphan rate)")
        print(f"    Fan-out ratio : {fanout:.2f}x avg child rows per parent")
        print()

    _footer()

    # Register gaps for hops with very low success
    if jp_joined < _GAP_THRESHOLD:
        _register_gap(
            test="Test 2 — Integrity Audit",
            table="job_postings.employer_profile_id → employer_profiles",
            issue=(
                f"Only {jp_joined} of {jp_total} job_postings rows join to employer_profiles. "
                "employer_profile_id is not populated by the enrichment agent."
            ),
            row_count=jp_joined,
        )
    if nj_joined < _GAP_THRESHOLD:
        _register_gap(
            test="Test 2 — Integrity Audit",
            table="normalized_jobs → job_postings",
            issue=(
                f"Only {nj_joined} of {nj_total} normalized_jobs rows join to job_postings. "
                "Check source/external_id population."
            ),
            row_count=nj_joined,
        )


# ---------------------------------------------------------------------------
# TEST 3 — Employer Profile Health Check
# ---------------------------------------------------------------------------


def test3_employer_profile_health() -> None:
    _header("TEST 3 — Employer Profile Health Check")

    total_ep = _scalar("SELECT COUNT(*) FROM dbo.employer_profiles")
    print(f"  Total employer_profiles rows : {total_ep}")

    if total_ep == 0:
        print("  employer_profiles is empty — no further health metrics available.")
        _footer()
        _register_gap(
            test="Test 3 — Employer Profile Health",
            table="employer_profiles",
            issue="Table is completely empty (0 rows).",
            row_count=0,
        )
        return

    # Unknown name rate — join to companies for the name
    health_rows = _q(
        """
        SELECT
            COUNT(*)                                                         AS total_profiles,
            COUNT(*) FILTER (
                WHERE c.company_name IS NULL
                   OR TRIM(c.company_name) = ''
                   OR LOWER(TRIM(c.company_name)) = 'unknown'
            )                                                                AS unknown_count
        FROM dbo.employer_profiles ep
        LEFT JOIN dbo.companies c ON ep.company_id = c.company_id
        """
    )

    if health_rows:
        total, unknown = int(health_rows[0][0]), int(health_rows[0][1])
        known = total - unknown
        unknown_rate = _pct(unknown, total)
        print(f"  Profiles with resolved company name : {known:>6}")
        print(f"  Profiles with null/empty/Unknown    : {unknown:>6}  ({unknown_rate} unknown rate)")
    else:
        print("  Could not compute health metrics (query returned no rows).")

    _footer()

    if total_ep < _GAP_THRESHOLD:
        _register_gap(
            test="Test 3 — Employer Profile Health",
            table="employer_profiles",
            issue=f"Only {total_ep} total profiles — FK enrichment may not have run.",
            row_count=total_ep,
        )


# ---------------------------------------------------------------------------
# TEST 4 — Sector Connection Test (skill_demand_weekly → sector_summary_weekly)
# ---------------------------------------------------------------------------


def test4_sector_connection() -> None:
    _header("TEST 4 — Sector Connection Test (Python: skill_demand_weekly → sector_summary_weekly)")

    # Pre-flight: check if either table is empty (analytics_minimum_data_guard block)
    sdw_count = _scalar("SELECT COUNT(*) FROM dbo.skill_demand_weekly")
    ssw_count = _scalar("SELECT COUNT(*) FROM dbo.sector_summary_weekly")

    print(f"  skill_demand_weekly rows   : {sdw_count}")
    print(f"  sector_summary_weekly rows : {ssw_count}\n")

    if sdw_count == 0:
        print(
            "  BLOCKED — skill_demand_weekly is empty.\n"
            "  This is the analytics_minimum_data_guard condition: the Analytics Agent\n"
            "  requires a minimum number of postings before computing aggregates.\n"
            "  Resolution: run scripts/smoke/refresh_aggregates.py after more data is ingested."
        )
        _footer()
        _register_gap(
            test="Test 4 — Sector Connection Test",
            table="skill_demand_weekly",
            issue="Table is empty — analytics aggregates have not been computed yet.",
            row_count=0,
        )
        return

    if ssw_count == 0:
        print(
            "  BLOCKED — sector_summary_weekly is empty.\n"
            "  This is the analytics_minimum_data_guard condition: sector aggregates\n"
            "  require sufficient postings with a resolved sector_id.\n"
            "  Resolution: run scripts/smoke/refresh_aggregates.py after enrichment completes."
        )
        _footer()
        _register_gap(
            test="Test 4 — Sector Connection Test",
            table="sector_summary_weekly",
            issue="Table is empty — sector aggregates have not been computed yet.",
            row_count=0,
        )
        return

    # Attempt week_start-based join for 'Python'
    rows = _q(
        """
        SELECT
            sdw.skill_label,
            ssw.sector,
            sdw.week_start,
            sdw.posting_count  AS skill_posting_count,
            ssw.posting_count  AS sector_posting_count
        FROM dbo.skill_demand_weekly sdw
        JOIN dbo.sector_summary_weekly ssw ON sdw.week_start = ssw.week_start
        WHERE sdw.skill_label ILIKE 'Python'
        ORDER BY sdw.week_start DESC
        LIMIT 10
        """
    )

    row_count = len(rows)
    print(f"  Rows returned (LIMIT 10) : {row_count}")

    if row_count == 0:
        print(
            "\n  No rows — skill_demand_weekly has rows for 'Python' but no week_start\n"
            "  overlap with sector_summary_weekly. The two aggregate tables were likely\n"
            "  computed in different pipeline runs without a matching week."
        )
        _register_gap(
            test="Test 4 — Sector Connection Test",
            table="skill_demand_weekly JOIN sector_summary_weekly ON week_start",
            issue=(
                "No matching week_start rows for 'Python'. "
                "skill_demand_weekly and sector_summary_weekly may span different time windows."
            ),
            row_count=0,
        )
    else:
        print(
            f"\n  Join succeeded. Sample (up to 5 rows):\n"
            f"  {'skill_label':<16} {'sector':<30} {'week_start':<12} "
            f"{'skill_count':>11} {'sector_count':>12}"
        )
        print("  " + "-" * 85)
        for row in rows[:5]:
            print(f"  {str(row[0]):<16} {str(row[1]):<30} {str(row[2]):<12} {str(row[3]):>11} {str(row[4]):>12}")
        if row_count < _GAP_THRESHOLD:
            _register_gap(
                test="Test 4 — Sector Connection Test",
                table="skill_demand_weekly JOIN sector_summary_weekly",
                issue=(
                    f"Only {row_count} rows returned — very limited overlap between "
                    "the two aggregate tables on week_start."
                ),
                row_count=row_count,
            )

    _footer()


# ---------------------------------------------------------------------------
# TEST 5 — Gap Report Generator
# ---------------------------------------------------------------------------


def test5_gap_report() -> None:
    _header("TEST 5 — Gap Report Generator")

    if not _gaps:
        print("  All tests passed — no SEVERITY 1 gaps detected.")
        _footer()
        return

    print(f"  {len(_gaps)} gap(s) detected:\n")
    _footer()

    for gap in _gaps:
        row_info = f"  Rows returned : {gap['row_count']}\n" if gap["row_count"] is not None else ""
        print(_DIV)
        print("  SEVERITY 1 — GAP DETECTED")
        print(_DIV)
        print(f"  Test  : {gap['test']}")
        print(f"  Table : {gap['table']}")
        print(f"  Issue : {gap['issue']}")
        if row_info:
            print(row_info, end="")
        print(_DIV)
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print(_DIV)
    print("  EMPLOYER DRILL-DOWN AUDIT")
    print("  JIE Database — local PostgreSQL (dbo schema)")
    print(_DIV)

    try:
        test1_join_path_integrity()
    except Exception as exc:
        print(f"  ERROR in Test 1: {exc}")
        _register_gap("Test 1 — Join Path Integrity", "unknown", str(exc))

    try:
        test2_integrity_audit()
    except Exception as exc:
        print(f"  ERROR in Test 2: {exc}")
        _register_gap("Test 2 — Integrity Audit", "unknown", str(exc))

    try:
        test3_employer_profile_health()
    except Exception as exc:
        print(f"  ERROR in Test 3: {exc}")
        _register_gap("Test 3 — Employer Profile Health", "employer_profiles", str(exc))

    try:
        test4_sector_connection()
    except Exception as exc:
        print(f"  ERROR in Test 4: {exc}")
        _register_gap("Test 4 — Sector Connection Test", "skill_demand_weekly / sector_summary_weekly", str(exc))

    test5_gap_report()


if __name__ == "__main__":
    main()
