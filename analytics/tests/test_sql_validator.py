"""Adversarial tests for ``sql_guardrails`` (sqlglot AST)."""

from __future__ import annotations

from analytics.query_engine.sql_guardrails import (
    ALLOWED_TABLES,
    MAX_ROWS,
    validate_analytics_sql,
)


def test_allowed_select_passes() -> None:
    sql = (
        "SELECT skill_label, posting_count FROM dbo.skill_demand_weekly "
        "WHERE week_start >= '2025-01-01' ORDER BY posting_count DESC LIMIT 50"
    )
    r = validate_analytics_sql(sql)
    assert r.ok
    assert r.sql
    assert "LIMIT" in (r.sql or "").upper()


def test_unions_must_not_reference_disallowed_table() -> None:
    sql = (
        "SELECT id FROM dbo.skill_demand_weekly "
        "UNION ALL SELECT job_posting_id::text FROM dbo.job_postings"
    )
    r = validate_analytics_sql(sql)
    assert not r.ok
    assert r.error


def test_subquery_smuggling_from_restricted_table() -> None:
    sql = "SELECT * FROM (SELECT title FROM dbo.job_postings AS j) AS t"
    r = validate_analytics_sql(sql)
    assert not r.ok


def test_comment_hiding_second_statement_rejected() -> None:
    sql = "SELECT 1 FROM dbo.geo_demand_weekly; DROP TABLE dbo.skill_demand_weekly"
    r = validate_analytics_sql(sql)
    assert not r.ok


def test_insert_rejected() -> None:
    sql = (
        "INSERT INTO dbo.skill_demand_weekly (skill_label, week_start, posting_count, employer_count, computed_at) "
        "VALUES ('x', DATE '2025-01-01', 1, 1, NOW())"
    )
    r = validate_analytics_sql(sql)
    assert not r.ok


def test_delete_rejected() -> None:
    r = validate_analytics_sql("DELETE FROM dbo.skill_demand_weekly WHERE id = 1")
    assert not r.ok


def test_update_rejected() -> None:
    r = validate_analytics_sql("UPDATE dbo.skill_demand_weekly SET posting_count = 0")
    assert not r.ok


def test_truncate_rejected() -> None:
    r = validate_analytics_sql("TRUNCATE TABLE dbo.skill_demand_weekly")
    assert not r.ok


def test_drop_rejected() -> None:
    r = validate_analytics_sql("DROP TABLE dbo.skill_demand_weekly")
    assert not r.ok


def test_create_rejected() -> None:
    r = validate_analytics_sql("CREATE TABLE dbo.foo (id INT)")
    assert not r.ok


def test_limit_enforced_when_missing() -> None:
    sql = "SELECT * FROM dbo.skill_velocity"
    r = validate_analytics_sql(sql)
    assert r.ok
    assert r.sql
    assert f"LIMIT {MAX_ROWS}" in (r.sql or "").upper()


def test_limit_capped_when_too_high() -> None:
    sql = "SELECT * FROM dbo.tool_demand_weekly LIMIT 9999"
    r = validate_analytics_sql(sql)
    assert r.ok
    assert r.sql
    assert f"LIMIT {MAX_ROWS}" in (r.sql or "").upper()


def test_unicode_identifier_still_resolves_table() -> None:
    sql = 'SELECT * FROM dbo."skill_demand_weekly"'
    r = validate_analytics_sql(sql)
    assert r.ok


def test_allowlist_contains_aggregate_tables() -> None:
    assert "skill_demand_weekly" in ALLOWED_TABLES
    assert "job_postings" not in ALLOWED_TABLES
