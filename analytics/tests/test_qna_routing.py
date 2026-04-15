"""Tests for guardrailed analytics Q&A routing (GitHub #117)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from analytics.query_engine.routing import run_guardrailed_analytics_query
from common.types.query_request import QueryRequest


def _fake_session_ok() -> MagicMock:
    row = MagicMock()
    row._mapping = {"posting_count": 42, "time_period": "2025-Q1"}
    result = MagicMock()
    result.keys.return_value = ["posting_count", "time_period"]
    result.fetchall.return_value = [row]

    session = MagicMock()
    session.execute.return_value = result
    return session


def test_routing_rejects_invalid_sql_without_execute() -> None:
    session = MagicMock()

    def fake_complete(prompt: str, agent_name: str, **kwargs):
        if agent_name == "analytics-qna-intent":
            return {
                "content": '{"intent_label": "posting_counts", "classification_confidence": 0.9}',
                "success": True,
                "extraction_failed": False,
                "cost_usd": 0.001,
                "input_tokens": 10,
                "output_tokens": 5,
                "model": "m",
            }
        return {
            "content": '{"sql": "SELECT * FROM dbo.pg_stat_activity LIMIT 10"}',
            "success": True,
            "extraction_failed": False,
            "cost_usd": 0.002,
            "input_tokens": 20,
            "output_tokens": 10,
            "model": "m",
        }

    with patch("analytics.query_engine.routing.complete", side_effect=fake_complete):
        out = run_guardrailed_analytics_query(
            QueryRequest(query="How many jobs?"),
            session=session,
            correlation_id="cid-1",
        )
    session.execute.assert_not_called()
    assert out.refused is True or not out.answer_text.strip()


def test_routing_executes_valid_sql_and_returns_synthesis() -> None:
    session = _fake_session_ok()

    def fake_complete(prompt: str, agent_name: str, **kwargs):
        if agent_name == "analytics-qna-intent":
            return {
                "content": '{"intent_label": "aggregate_skill_demand", "classification_confidence": 0.88}',
                "success": True,
                "extraction_failed": False,
                "cost_usd": 0.001,
                "input_tokens": 10,
                "output_tokens": 5,
                "model": "m",
            }
        return {
            "content": json.dumps(
                {
                    "sql": (
                        "SELECT COUNT(*)::int AS posting_count, "
                        "'2025-Q1' AS time_period FROM dbo.job_postings LIMIT 100"
                    )
                }
            ),
            "success": True,
            "extraction_failed": False,
            "cost_usd": 0.002,
            "input_tokens": 20,
            "output_tokens": 10,
            "model": "m",
        }

    def synth_complete(prompt: str, agent_name: str, **kwargs):
        if agent_name == "analytics-qna-synthesis":
            return {
                "content": "There are 42 postings in 2025-Q1.",
                "success": True,
                "extraction_failed": False,
                "cost_usd": 0.01,
                "input_tokens": 50,
                "output_tokens": 20,
                "model": "m",
            }
        if agent_name == "analytics-qna-followup":
            return {
                "content": '["By sector?", "Trend over quarters?"]',
                "success": True,
                "extraction_failed": False,
                "cost_usd": 0.001,
                "input_tokens": 10,
                "output_tokens": 5,
                "model": "m",
            }
        raise AssertionError(agent_name)

    with (
        patch("analytics.query_engine.routing.complete", side_effect=fake_complete),
        patch("analytics.query_engine.synthesis.complete", side_effect=synth_complete),
    ):
        out = run_guardrailed_analytics_query(
            QueryRequest(query="Count postings"),
            session=session,
        )

    assert session.execute.call_count >= 1
    assert "42" in out.answer_text or "2025-Q1" in out.answer_text
    assert out.cost_breakdown_usd.get("intent_classification") is not None
    assert out.cost_breakdown_usd.get("sql_generation") is not None


def test_routing_sql_execution_failure_surfaces_truncated_db_message() -> None:
    session = MagicMock()
    session.execute.side_effect = Exception(
        '(psycopg2.errors.UndefinedColumn) column "bad_col" does not exist\nLINE 1: SELECT bad_col FROM dbo.job_postings'
    )

    def fake_complete(prompt: str, agent_name: str, **kwargs):
        if agent_name == "analytics-qna-intent":
            return {
                "content": '{"intent_label": "posting_counts", "classification_confidence": 0.9}',
                "success": True,
                "extraction_failed": False,
                "cost_usd": 0.001,
                "input_tokens": 10,
                "output_tokens": 5,
                "model": "m",
            }
        return {
            "content": json.dumps(
                {
                    "sql": (
                        "SELECT bad_col FROM dbo.job_postings "
                        "WHERE 1=1 LIMIT 100"
                    )
                }
            ),
            "success": True,
            "extraction_failed": False,
            "cost_usd": 0.002,
            "input_tokens": 20,
            "output_tokens": 10,
            "model": "m",
        }

    with patch("analytics.query_engine.routing.complete", side_effect=fake_complete):
        out = run_guardrailed_analytics_query(
            QueryRequest(query="Broken column?"),
            session=session,
            correlation_id="cid-sql-err",
        )

    assert out.refused is True
    assert out.sql_execution_error_detail
    assert "bad_col" in out.sql_execution_error_detail
    assert out.refusal_message
    assert "PostgreSQL:" in out.refusal_message
    assert "bad_col" in out.refusal_message
