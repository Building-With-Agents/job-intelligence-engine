"""HTTP-facing analytics Q&A: intent → ORM router → evidence → synthesis → API mapping."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from analytics.api.schemas import AnalyticsQueryResponse, EvidenceItem
from analytics.query_engine import audit_log, qna
from analytics.query_engine.intent import classify_workforce_question
from analytics.query_engine.router import QueryRouter
from analytics.query_engine.schemas import CostLedger, QueryResultPayload, SynthesisResponse
from common.types.query_request import QueryRequest

log = structlog.get_logger()


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _json_safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({k: _json_safe_value(v) for k, v in row.items()})
    return out


def _router_error_message(route_result: Any) -> str | None:
    if route_result.routed and not route_result.error:
        return None
    if route_result.error:
        return str(route_result.error)
    return str(route_result.query_label or "query_not_routed")


def _sql_generated_line(route_result: Any) -> str:
    """ORM path has no raw SQL string; expose tables + label for clients and audit."""
    parts: list[str] = []
    if route_result.tables_used:
        parts.append("tables: " + ", ".join(route_result.tables_used))
    if route_result.query_label:
        parts.append(route_result.query_label)
    return " | ".join(parts) if parts else ""


def _synthesis_to_api(sr: SynthesisResponse, *, sql_generated: str) -> AnalyticsQueryResponse:
    answer = (sr.refusal_message or "").strip() if sr.refused else (sr.answer_text or "").strip()
    if not answer and sr.refusal_message:
        answer = sr.refusal_message
    evidence = [
        EvidenceItem(
            title=c.citation_id,
            source=(c.source_table or ""),
            snippet=c.summary,
        )
        for c in sr.citations
    ]
    return AnalyticsQueryResponse(
        answer=answer or "No answer could be generated for this question.",
        evidence=evidence,
        confidence=float(sr.confidence),
        follow_up_questions=list(sr.follow_up_questions or []),
        sql_generated=sql_generated,
        cost_usd=float(sr.total_cost_usd),
    )


def run_analytics_qna(
    session: Session,
    question: str,
    correlation_id: str | None,
) -> AnalyticsQueryResponse:
    """Run Q&A inside an open SQLAlchemy session (same transaction as caller)."""
    cid = correlation_id or str(uuid.uuid4())
    endpoint = "POST /analytics/query"
    q = (question or "").strip()
    payload_audit: dict[str, Any] = {}

    if not q:
        audit_log.insert_orchestration_audit(
            session,
            correlation_id=cid,
            endpoint=endpoint,
            question=question,
            sql_generated=None,
            confidence=0.0,
            success=False,
            error_code="empty_question",
            payload={},
        )
        return AnalyticsQueryResponse(
            answer="Please provide a non-empty question.",
            evidence=[],
            confidence=0.0,
            follow_up_questions=[],
            sql_generated="",
            cost_usd=0.0,
        )

    try:
        classification = classify_workforce_question(q, correlation_id=cid)
        intent_label = str(classification.get("intent") or "other")
        conf = float(classification.get("confidence") or 0.0)
        payload_audit = {
            "intent": intent_label,
            "classification_confidence": conf,
            "needs_clarification": classification.get("needs_clarification"),
        }

        router = QueryRouter()
        route_result = router.route(classification, session)

        rows = _json_safe_rows(route_result.rows)
        columns = list(rows[0].keys()) if rows else []
        router_error = _router_error_message(route_result)
        sql_line = _sql_generated_line(route_result)

        q_payload = QueryResultPayload(
            request=QueryRequest(query=q),
            intent_label=intent_label,
            classification_confidence=conf,
            executed_sql=None,
            columns=columns,
            rows=rows,
            row_count_returned=int(route_result.row_count),
            result_truncated=bool(route_result.is_partial),
            tables_referenced=list(route_result.tables_used),
            router_error=router_error,
            correlation_id=cid,
        )

        ledger = CostLedger()
        syn = qna.run_analytics_qna(q_payload, cost_ledger=ledger)

        audit_log.insert_orchestration_audit(
            session,
            correlation_id=cid,
            endpoint=endpoint,
            question=q,
            sql_generated=sql_line or None,
            confidence=float(syn.confidence),
            success=True,
            error_code=None,
            payload={
                **payload_audit,
                "row_count": len(rows),
                "citations": len(syn.citations),
                "refused": syn.refused,
            },
        )

        return _synthesis_to_api(syn, sql_generated=sql_line)

    except RuntimeError as exc:
        code = str(exc)
        log.warning("analytics_query_runtime_error", error=code, correlation_id=cid)
        audit_log.insert_orchestration_audit(
            session,
            correlation_id=cid,
            endpoint=endpoint,
            question=q,
            sql_generated=None,
            confidence=0.0,
            success=False,
            error_code=code,
            payload=payload_audit,
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
            sql_generated="",
            cost_usd=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("analytics_query_unhandled", correlation_id=cid)
        audit_log.insert_orchestration_audit(
            session,
            correlation_id=cid,
            endpoint=endpoint,
            question=q,
            sql_generated=None,
            confidence=0.0,
            success=False,
            error_code="internal_error",
            payload={**payload_audit, "error": type(exc).__name__},
        )
        return AnalyticsQueryResponse(
            answer="An unexpected error occurred while processing your question.",
            evidence=[],
            confidence=0.0,
            follow_up_questions=[],
            sql_generated="",
            cost_usd=0.0,
        )
