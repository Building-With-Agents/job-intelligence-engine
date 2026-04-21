"""Unit tests for sql_guardrails (no database)."""

from __future__ import annotations

from analytics.query_engine.sql_guardrails import validate_sql


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
    assert r.reason and ("single" in r.reason.lower() or "statement" in r.reason.lower())


def test_rejects_forbidden_table() -> None:
    r = validate_sql("SELECT * FROM dbo.llm_audit_log")
    assert not r.ok
    assert r.reason and "llm_audit_log" in (r.reason or "")


def test_rejects_union_smuggling_forbidden_table() -> None:
    r = validate_sql(
        "SELECT skill_label FROM dbo.skill_demand_weekly UNION ALL SELECT agent_name FROM dbo.llm_audit_log"
    )
    assert not r.ok


def test_rejects_nested_subquery_forbidden_table() -> None:
    r = validate_sql(
        "SELECT * FROM dbo.skill_demand_weekly WHERE skill_label IN (SELECT agent_name FROM dbo.llm_audit_log)"
    )
    assert not r.ok


def test_oversized_limit_is_capped_to_100() -> None:
    r = validate_sql("SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 200")
    assert r.ok
    assert r.sql_for_execution
    assert "LIMIT 100" in r.sql_for_execution.upper()


def test_accepts_limit_at_100() -> None:
    r = validate_sql("SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 100")
    assert r.ok


def test_comment_only_does_not_bypass() -> None:
    r = validate_sql("SELECT 1 FROM dbo.skill_demand_weekly WHERE 1=1 -- AND 1=(SELECT 1)")
    assert r.ok
    r2 = validate_sql("/* hi */ SELECT * FROM dbo.llm_audit_log")
    assert not r2.ok


def test_case_insensitive_mutation_keyword() -> None:
    r = validate_sql("select 1 from dbo.skill_demand_weekly; delete from dbo.skill_demand_weekly")
    assert not r.ok
