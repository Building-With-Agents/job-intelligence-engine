"""Persist Q&A and SQL-validation events to audit tables."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from common.data_store.models import OrchestrationAuditLog
from common.llm_adapter import log_extraction_event


def question_hash(question: str) -> str:
    return hashlib.sha256(question.strip().encode()).hexdigest()


def sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.strip().encode()).hexdigest()


def log_sql_validation_to_llm_audit(*, sql_text: str, is_valid: bool, reason: str | None) -> None:
    """Record SQL guardrail outcome in llm_audit_log (zero-token internal row)."""
    h = hashlib.sha256(sql_text.encode()).hexdigest()
    prompt = f"sql_attempt:{h}:valid={is_valid}"
    log_extraction_event(
        agent_name="analytics_query_api",
        prompt=prompt[:8000],
        model="sql-guardrail",
        provider="internal",
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        success=is_valid,
        error_reason=reason if not is_valid else None,
    )


def insert_orchestration_audit(
    session: Session,
    *,
    correlation_id: str | None,
    endpoint: str,
    question: str | None,
    sql_generated: str | None,
    confidence: float | None,
    success: bool,
    error_code: str | None,
    payload: dict[str, Any] | None,
) -> None:
    session.add(
        OrchestrationAuditLog(
            correlation_id=correlation_id,
            endpoint=endpoint,
            question_hash=question_hash(question) if question else None,
            sql_hash=sql_hash(sql_generated) if sql_generated else None,
            confidence=confidence,
            success=success,
            error_code=error_code,
            payload=payload,
        )
    )
