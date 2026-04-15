"""Tests for analytics SQL guardrails (GitHub #117)."""

from __future__ import annotations

from analytics.query_engine.sql_guardrails import (
    extract_tables_referenced,
    validate_sql,
)


def test_validate_accepts_simple_select() -> None:
    ok, reason, norm = validate_sql("SELECT job_posting_id FROM dbo.job_postings")
    assert ok and reason == "ok"
    assert norm is not None
    assert "LIMIT 100" in norm.upper()


def test_validate_rejects_drop() -> None:
    ok, reason, norm = validate_sql("DROP TABLE dbo.job_postings")
    assert not ok
    assert norm is None


def test_validate_rejects_stacked_statements() -> None:
    ok, reason, norm = validate_sql("SELECT 1 FROM dbo.job_postings; DELETE FROM dbo.job_postings")
    assert not ok
    assert reason == "multiple_statements"
    assert norm is None


def test_validate_rejects_disallowed_table() -> None:
    ok, reason, norm = validate_sql("SELECT * FROM dbo.pg_stat_activity")
    assert not ok
    assert "disallowed_table" in reason
    assert norm is None


def test_validate_rejects_insert() -> None:
    ok, reason, norm = validate_sql("INSERT INTO dbo.job_postings (job_title) VALUES ('x')")
    assert not ok
    assert norm is None


def test_validate_rejects_comment_hiding_delete() -> None:
    ok, reason, norm = validate_sql("SELECT 1 FROM dbo.job_postings WHERE 1=1/**/DELETE/**/AND 1=1")
    assert not ok


def test_validate_clamps_high_limit() -> None:
    ok, _, norm = validate_sql("SELECT * FROM job_postings LIMIT 500")
    assert ok and norm is not None
    assert "LIMIT 100" in norm.upper()


def test_validate_accepts_with_cte() -> None:
    sql = "WITH c AS (SELECT job_posting_id FROM dbo.job_postings LIMIT 10) SELECT * FROM c"
    ok, reason, norm = validate_sql(sql)
    assert ok and reason == "ok"
    assert norm is not None


def test_extract_tables_referenced() -> None:
    sql = "SELECT 1 FROM dbo.job_postings jp JOIN dbo.companies c ON true"
    assert extract_tables_referenced(sql) == ["companies", "job_postings"]


def test_validate_rejects_too_long() -> None:
    ok, reason, _ = validate_sql("SELECT 1 FROM job_postings " + ("x" * 30_000))
    assert not ok
    assert reason == "sql_too_long"


def test_validate_rejects_copy() -> None:
    ok, reason, _ = validate_sql("COPY dbo.job_postings TO '/tmp/x'")
    assert not ok
    assert reason == "forbidden_keyword"
