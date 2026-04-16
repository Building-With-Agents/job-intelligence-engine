"""Tests for guardrailed analytics Q&A routing (GitHub #117)."""

from __future__ import annotations

import json
from types import SimpleNamespace
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

    with (
        patch("analytics.query_engine.routing.complete", side_effect=fake_complete),
        patch("analytics.query_engine.routing.audit_log.log_sql_validation_to_llm_audit") as audit_mock,
    ):
        out = run_guardrailed_analytics_query(
            QueryRequest(query="How many jobs?"),
            session=session,
            correlation_id="cid-1",
        )
    session.execute.assert_not_called()
    audit_mock.assert_called_once_with(
        sql_text="SELECT * FROM dbo.pg_stat_activity LIMIT 10",
        is_valid=False,
        reason="disallowed_table:pg_stat_activity",
    )
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
        patch("analytics.query_engine.routing.audit_log.log_sql_validation_to_llm_audit") as audit_mock,
        patch("analytics.query_engine.synthesis.complete", side_effect=synth_complete),
    ):
        out = run_guardrailed_analytics_query(
            QueryRequest(query="Count postings"),
            session=session,
        )

    assert session.execute.call_count >= 1
    audit_mock.assert_called_once_with(
        sql_text="SELECT COUNT(*)::int AS posting_count, '2025-Q1' AS time_period FROM dbo.job_postings LIMIT 100",
        is_valid=True,
        reason=None,
    )
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


def test_rest_qna_preserves_transparency_fields_and_classification_cost() -> None:
    session = MagicMock()

    def fake_intent_complete(prompt: str, agent_name: str, **kwargs):
        return {
            "content": json.dumps(
                {
                    "intent": "trend",
                    "confidence": 0.91,
                    "extracted_entities": {
                        "geographic_terms": [],
                        "role_names": [],
                        "skill_names": [],
                        "time_references": [],
                    },
                }
            ),
            "success": True,
            "extraction_failed": False,
            "cost_usd": 0.001,
            "input_tokens": 12,
            "output_tokens": 6,
            "model": "intent-model",
        }

    route_result = SimpleNamespace(
        rows=[{"week_start": "2025-04-21", "posting_count": 42}],
        row_count=1,
        is_partial=False,
        tables_used=["skill_demand_weekly"],
        routed=True,
        error=None,
        query_label="skill demand trend",
    )

    def fake_synthesis_complete(prompt: str, agent_name: str, **kwargs):
        if agent_name == "analytics-qna-synthesis":
            return {
                "content": "Demand reached 42 postings during 2025-04-21.",
                "success": True,
                "extraction_failed": False,
                "cost_usd": 0.01,
                "input_tokens": 50,
                "output_tokens": 20,
                "model": "synthesis-model",
            }
        if agent_name == "analytics-qna-followup":
            return {
                "content": '["Compare nearby weeks?"]',
                "success": True,
                "extraction_failed": False,
                "cost_usd": 0.002,
                "input_tokens": 10,
                "output_tokens": 5,
                "model": "followup-model",
            }
        raise AssertionError(agent_name)

    with (
        patch("analytics.query_engine.intent.complete", side_effect=fake_intent_complete),
        patch("analytics.query_engine.routing.QueryRouter.route", return_value=route_result),
        patch("analytics.query_engine.routing.audit_log.insert_orchestration_audit"),
        patch("analytics.query_engine.synthesis.complete", side_effect=fake_synthesis_complete),
    ):
        from analytics.query_engine.routing import run_analytics_qna

        out = run_analytics_qna(session, "What is the trend?", "cid-rest-1")

    assert out.periods_described == "2025-04-21"
    assert out.confidence_flagged_low is False
    assert out.volume_flagged_low is False
    assert out.evidence[0].time_period == "2025-04-21"
    assert out.evidence[0].supporting_count == 42
    assert out.total_cost_usd == out.cost_usd
    assert out.cost_usd == 0.013
    assert out.cost_breakdown_usd == {
        "intent_classification": 0.001,
        "synthesis": 0.01,
        "follow_up": 0.002,
    }
