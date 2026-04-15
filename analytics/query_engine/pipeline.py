"""End-to-end analytics Q&A: intent → SQL → validate → execute → synthesize → audit."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.orm import Session

from analytics.api.schemas import AnalyticsQueryResponse, EvidenceItem
from analytics.query_engine import audit
from analytics.query_engine.execute_safe import execute_validated_query
from analytics.query_engine.intent import classify_intent
from analytics.query_engine.router import generate_sql
from analytics.query_engine.sql_validator import validate_sql
from analytics.query_engine.synthesis import synthesize

log = structlog.get_logger()


def run_analytics_query(
    session: Session,
    question: str,
    correlation_id: str | None,
) -> AnalyticsQueryResponse:
    """Run pipeline inside an open SQLAlchemy session (same transaction as caller)."""
    cid = correlation_id or str(uuid.uuid4())
    endpoint = "POST /analytics/query"
    sql_generated = ""
    intent = classify_intent(question)
    payload_base: dict[str, Any] = {"intent": intent.kind.value}

    try:
        raw_sql, router_cost = generate_sql(question, intent)
        sql_generated = raw_sql
        if not raw_sql:
            audit.log_sql_validation_to_llm_audit(
                sql_text="(no_sql_generated)", is_valid=False, reason="empty_sql"
            )
            audit.insert_orchestration_audit(
                session,
                correlation_id=cid,
                endpoint=endpoint,
                question=question,
                sql_generated=None,
                confidence=0.0,
                success=False,
                error_code="router_empty",
                payload=payload_base,
            )
            return AnalyticsQueryResponse(
                answer="Could not generate a safe query for this question.",
                evidence=[],
                confidence=0.0,
                follow_up_questions=["Try rephrasing with a specific table metric (skills, roles, regions)."],
                sql_generated="",
                cost_usd=router_cost,
            )

        vr = validate_sql(raw_sql)
        audit.log_sql_validation_to_llm_audit(
            sql_text=raw_sql,
            is_valid=vr.ok,
            reason=vr.reason,
        )
        if not vr.ok:
            audit.insert_orchestration_audit(
                session,
                correlation_id=cid,
                endpoint=endpoint,
                question=question,
                sql_generated=raw_sql,
                confidence=0.0,
                success=False,
                error_code=vr.reason or "sql_invalid",
                payload={**payload_base, "validation_reason": vr.reason},
            )
            return AnalyticsQueryResponse(
                answer="That query cannot be executed under current data guardrails.",
                evidence=[],
                confidence=0.0,
                follow_up_questions=["Ask about aggregates only (weekly demand, sectors, skills)."],
                sql_generated=raw_sql,
                cost_usd=router_cost,
            )

        rows, _n = execute_validated_query(session, vr.sql_for_execution)
        answer, evidence_dicts, confidence, follow_ups, total_cost = synthesize(
            question,
            rows,
            cid,
            prior_cost=router_cost,
        )

        audit.insert_orchestration_audit(
            session,
            correlation_id=cid,
            endpoint=endpoint,
            question=question,
            sql_generated=vr.sql_for_execution,
            confidence=confidence,
            success=True,
            error_code=None,
            payload={
                **payload_base,
                "row_count": len(rows),
                "evidence_count": len(evidence_dicts),
            },
        )

        return AnalyticsQueryResponse(
            answer=answer,
            evidence=[EvidenceItem(**e) for e in evidence_dicts],
            confidence=confidence,
            follow_up_questions=follow_ups,
            sql_generated=vr.sql_for_execution,
            cost_usd=total_cost,
        )
    except RuntimeError as exc:
        code = str(exc)
        log.warning("analytics_query_runtime_error", error=code, correlation_id=cid)
        audit.insert_orchestration_audit(
            session,
            correlation_id=cid,
            endpoint=endpoint,
            question=question,
            sql_generated=sql_generated or None,
            confidence=0.0,
            success=False,
            error_code=code,
            payload=payload_base,
        )
        msg = (
            "The query took too long and was stopped."
            if code == "query_timeout"
            else "The query could not be completed."
        )
        return AnalyticsQueryResponse(
            answer=msg,
            evidence=[],
            confidence=0.0,
            follow_up_questions=[],
            sql_generated=sql_generated,
            cost_usd=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("analytics_query_unhandled", correlation_id=cid)
        audit.insert_orchestration_audit(
            session,
            correlation_id=cid,
            endpoint=endpoint,
            question=question,
            sql_generated=sql_generated or None,
            confidence=0.0,
            success=False,
            error_code="internal_error",
            payload={**payload_base, "error": type(exc).__name__},
        )
        return AnalyticsQueryResponse(
            answer="An unexpected error occurred while processing your question.",
            evidence=[],
            confidence=0.0,
            follow_up_questions=[],
            sql_generated=sql_generated,
            cost_usd=0.0,
        )
