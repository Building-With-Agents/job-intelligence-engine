"""Unit tests for sql_validator (no database)."""

from __future__ import annotations

from analytics.query_engine.sql_validator import validate_sql


def test_accepts_simple_select_allowlisted() -> None:
    r = validate_sql("SELECT skill_label FROM dbo.skill_demand_weekly")
    assert r.ok
    assert "limit 100" in r.sql_for_execution.lower()


def test_rejects_insert() -> None:
    r = validate_sql("INSERT INTO dbo.job_postings (source) VALUES ('x')")
    assert not r.ok
    assert r.reason


def test_rejects_multi_statement() -> None:
    r = validate_sql("SELECT 1 FROM dbo.skills; DROP TABLE dbo.skills")
    assert not r.ok
    assert "multiple_statements" in (r.reason or "")


def test_rejects_forbidden_table() -> None:
    r = validate_sql("SELECT * FROM dbo.llm_audit_log")
    assert not r.ok
    assert "table_not_allowed" in (r.reason or "")


def test_rejects_union_smuggling_forbidden_table() -> None:
    r = validate_sql(
        "SELECT skill_label FROM dbo.skill_demand_weekly "
        "UNION ALL SELECT agent_name FROM dbo.llm_audit_log"
    )
    assert not r.ok


def test_rejects_nested_subquery_forbidden_table() -> None:
    r = validate_sql(
        "SELECT * FROM dbo.skill_demand_weekly WHERE skill_label IN "
        "(SELECT agent_name FROM dbo.llm_audit_log)"
    )
    assert not r.ok


def test_rejects_limit_over_100() -> None:
    r = validate_sql("SELECT 1 FROM dbo.skills LIMIT 200")
    assert not r.ok
    assert "limit_too_large" in (r.reason or "")


def test_accepts_limit_at_100() -> None:
    r = validate_sql("SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 100")
    assert r.ok


def test_comment_only_does_not_bypass() -> None:
    r = validate_sql("SELECT 1 FROM dbo.skills WHERE 1=1 -- AND 1=(SELECT 1)")
    assert r.ok  # still allowlisted single select
    r2 = validate_sql("/* hi */ SELECT * FROM dbo.llm_audit_log")
    assert not r2.ok


def test_case_insensitive_mutation_keyword() -> None:
    r = validate_sql("select 1 from dbo.skills; delete from dbo.skills")
    assert not r.ok
