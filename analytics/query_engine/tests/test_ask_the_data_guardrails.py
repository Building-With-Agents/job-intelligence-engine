"""Unit tests for validate_ask_the_data_sql — Ask the Data path (no database).

Covers:
- Aggregate tables added by #186 hotfix are accepted by the guardrail.
- Operational tables used in canonical join patterns are accepted.
- Excluded internal tables are rejected.
- DML and multi-statement injections are rejected.
- LIMIT normalisation is applied on the Ask the Data path.
"""

from __future__ import annotations

import pytest

from analytics.query_engine.sql_guardrails import (
    inject_role_classification_issue197_guard,
    validate_ask_the_data_sql,
)

# ---------------------------------------------------------------------------
# Aggregate table acceptance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        "skill_demand_weekly",
        "tool_demand_weekly",
        "role_snapshot_weekly",
        "sector_summary_weekly",
        "geo_demand_weekly",
        "skill_velocity",
        "skill_co_occurrence",
        "posting_freshness",
    ],
)
def test_aggregate_tables_are_accepted(table: str) -> None:
    sql = f"SELECT * FROM dbo.{table} LIMIT 10"
    ok, reason, normalized = validate_ask_the_data_sql(sql)
    assert ok, f"Expected {table!r} to be allowed; got reason={reason!r}"
    assert normalized is not None


def test_canonical_roles_accepted() -> None:
    sql = "SELECT role_id, label, posting_count FROM dbo.canonical_roles ORDER BY posting_count DESC LIMIT 10"
    ok, reason, normalized = validate_ask_the_data_sql(sql)
    assert ok, f"canonical_roles rejected: {reason}"
    assert normalized is not None


def test_employer_profiles_accepted() -> None:
    sql = "SELECT company_size, COUNT(*) AS cnt FROM dbo.employer_profiles GROUP BY company_size LIMIT 10"
    ok, reason, normalized = validate_ask_the_data_sql(sql)
    assert ok, f"employer_profiles rejected: {reason}"


def test_extracted_intelligence_accepted() -> None:
    sql = "SELECT COUNT(*) FROM dbo.extracted_intelligence WHERE extraction_failed = FALSE LIMIT 10"
    ok, reason, normalized = validate_ask_the_data_sql(sql)
    assert ok, f"extracted_intelligence rejected: {reason}"


# ---------------------------------------------------------------------------
# Canonical join patterns are accepted
# ---------------------------------------------------------------------------


def test_skill_demand_weekly_top_n_query() -> None:
    """Recipe 1 from QA_DATA_CONTRACT.md §5 — top skills via aggregate table."""
    sql = (
        "SELECT skill_label, posting_count "
        "FROM dbo.skill_demand_weekly "
        "WHERE week_start = (SELECT MAX(week_start) FROM dbo.skill_demand_weekly) "
        "ORDER BY posting_count DESC LIMIT 10"
    )
    ok, reason, normalized = validate_ask_the_data_sql(sql)
    assert ok, f"Top-skills recipe rejected: {reason}"
    assert normalized is not None


def test_skill_velocity_trending_query() -> None:
    """Recipe 2 from QA_DATA_CONTRACT.md §5 — trending skills via skill_velocity."""
    sql = (
        "SELECT skill_label, week_over_week_change, four_week_trend "
        "FROM dbo.skill_velocity "
        "WHERE week = (SELECT MAX(week) FROM dbo.skill_velocity) "
        "ORDER BY week_over_week_change DESC LIMIT 10"
    )
    ok, reason, normalized = validate_ask_the_data_sql(sql)
    assert ok, f"Trending-skills recipe rejected: {reason}"


def test_role_snapshot_with_canonical_roles_join() -> None:
    """Recipe 3 from QA_DATA_CONTRACT.md §5 — salary join."""
    sql = (
        "SELECT rsw.role_title, rsw.median_salary, rsw.week_start "
        "FROM dbo.role_snapshot_weekly rsw "
        "JOIN dbo.canonical_roles cr ON cr.role_id = rsw.canonical_role_id "
        "WHERE LOWER(cr.label) LIKE '%data analyst%' "
        "ORDER BY rsw.week_start DESC LIMIT 5"
    )
    ok, reason, normalized = validate_ask_the_data_sql(sql)
    assert ok, f"Role-salary recipe rejected: {reason}"


def test_job_postings_with_normalized_jobs_join() -> None:
    """Operational join pattern: job_postings → normalized_jobs for date_posted."""
    sql = (
        "SELECT jp.job_title, nj.date_posted "
        "FROM dbo.job_postings jp "
        "JOIN dbo.normalized_jobs nj "
        "ON jp.source = nj.source AND jp.external_id = nj.external_id "
        "WHERE jp.spam_tier = 'clean' LIMIT 10"
    )
    ok, reason, normalized = validate_ask_the_data_sql(sql)
    assert ok, f"job_postings→normalized_jobs join rejected: {reason}"


def test_cte_with_aggregate_table() -> None:
    """CTE-wrapped query over aggregate table is accepted."""
    sql = (
        "WITH latest AS ("
        "  SELECT MAX(week_start) AS wk FROM dbo.skill_demand_weekly"
        ") "
        "SELECT sdw.skill_label, sdw.posting_count "
        "FROM dbo.skill_demand_weekly sdw, latest "
        "WHERE sdw.week_start = latest.wk "
        "ORDER BY sdw.posting_count DESC LIMIT 10"
    )
    ok, reason, normalized = validate_ask_the_data_sql(sql)
    assert ok, f"CTE over aggregate table rejected: {reason}"


# ---------------------------------------------------------------------------
# Excluded / internal tables are rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        "llm_audit_log",
        "orchestration_audit_log",
        "cohort_gap_cache",
        "analytics_pipeline_state",
        "normalization_quarantine",
        "trajectory_map",
        "postal_geo_data",
        "socc",
        "naics",
        "job_ingestion_runs",
    ],
)
def test_excluded_internal_tables_are_rejected(table: str) -> None:
    sql = f"SELECT * FROM dbo.{table} LIMIT 10"
    ok, reason, normalized = validate_ask_the_data_sql(sql)
    assert not ok, f"Expected {table!r} to be rejected; got ok=True"
    assert normalized is None


# ---------------------------------------------------------------------------
# Security: DML and injection patterns are rejected
# ---------------------------------------------------------------------------


def test_rejects_insert() -> None:
    ok, reason, _ = validate_ask_the_data_sql("INSERT INTO dbo.job_postings (job_title) VALUES ('x')")
    assert not ok
    assert reason == "forbidden_keyword"


def test_rejects_update() -> None:
    ok, reason, _ = validate_ask_the_data_sql("UPDATE dbo.job_postings SET status = 'closed' WHERE 1=1")
    assert not ok


def test_rejects_drop() -> None:
    ok, reason, _ = validate_ask_the_data_sql("DROP TABLE dbo.job_postings")
    assert not ok


def test_rejects_multi_statement() -> None:
    ok, reason, _ = validate_ask_the_data_sql(
        "SELECT 1 FROM dbo.skill_demand_weekly; DROP TABLE dbo.skill_demand_weekly"
    )
    assert not ok


def test_rejects_comment_smuggled_forbidden_table() -> None:
    ok, reason, _ = validate_ask_the_data_sql("/* bypass */ SELECT * FROM dbo.llm_audit_log LIMIT 10")
    assert not ok


# ---------------------------------------------------------------------------
# LIMIT normalisation on the Ask the Data path
# ---------------------------------------------------------------------------


def test_limit_added_when_missing() -> None:
    sql = "SELECT skill_label FROM dbo.skill_demand_weekly ORDER BY posting_count DESC"
    ok, _, normalized = validate_ask_the_data_sql(sql)
    assert ok
    assert normalized is not None
    assert "LIMIT 100" in normalized.upper()


def test_oversized_limit_is_capped() -> None:
    sql = "SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 9999"
    ok, _, normalized = validate_ask_the_data_sql(sql)
    assert ok
    assert normalized is not None
    assert "LIMIT 100" in normalized.upper()


def test_limit_within_bound_is_preserved() -> None:
    sql = "SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 25"
    ok, _, normalized = validate_ask_the_data_sql(sql)
    assert ok
    assert normalized is not None
    assert "LIMIT 25" in normalized.upper()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_sql_rejected() -> None:
    ok, reason, _ = validate_ask_the_data_sql("")
    assert not ok
    assert reason == "empty_sql"


def test_whitespace_only_rejected() -> None:
    ok, reason, _ = validate_ask_the_data_sql("   ")
    assert not ok


def test_non_select_rejected() -> None:
    ok, reason, _ = validate_ask_the_data_sql("EXPLAIN SELECT * FROM dbo.skill_demand_weekly")
    assert not ok


# ---------------------------------------------------------------------------
# Issue #197 — role_classification guard (employer / curriculum / workflow)
# ---------------------------------------------------------------------------


def test_issue197_injects_predicate_when_intent_employer_and_job_postings() -> None:
    sql = (
        "SELECT jp.role_classification, COUNT(*) AS n FROM dbo.job_postings jp "
        "WHERE jp.is_spam IS NOT TRUE GROUP BY jp.role_classification LIMIT 100"
    )
    ok, _, normalized = validate_ask_the_data_sql(sql)
    assert ok and normalized
    guarded = inject_role_classification_issue197_guard(
        normalized,
        intent_label="employer",
    )
    assert "N/A Not an IT role" in guarded
    assert "jp.role_classification" in guarded
    ok2, reason2, final = validate_ask_the_data_sql(guarded)
    assert ok2, reason2


def test_issue197_noop_for_non_guard_intent() -> None:
    sql = (
        "SELECT jp.role_classification FROM dbo.job_postings jp LIMIT 100"
    )
    ok, _, normalized = validate_ask_the_data_sql(sql)
    assert ok and normalized
    guarded = inject_role_classification_issue197_guard(
        normalized,
        intent_label="trend",
    )
    assert guarded == normalized


def test_issue197_noop_when_role_classification_absent() -> None:
    sql = "SELECT jp.job_title FROM dbo.job_postings jp LIMIT 100"
    ok, _, normalized = validate_ask_the_data_sql(sql)
    guarded = inject_role_classification_issue197_guard(
        normalized,
        intent_label="curriculum",
    )
    assert guarded == normalized


def test_issue197_skips_duplicate_if_na_label_already_present() -> None:
    sql = (
        "SELECT role_classification FROM dbo.job_postings "
        "WHERE role_classification != 'N/A Not an IT role' LIMIT 100"
    )
    ok, _, normalized = validate_ask_the_data_sql(sql)
    guarded = inject_role_classification_issue197_guard(
        normalized,
        intent_label="workflow",
    )
    assert guarded == normalized
