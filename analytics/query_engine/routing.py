"""Intent + SQL generation, guardrailed execution → ``QueryResultPayload`` → Q&A (GitHub #117).

Uses ``common.llm_adapter.complete`` only. User text is never concatenated into executable SQL;
only model output is validated via :mod:`analytics.query_engine.sql_guardrails`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from analytics.query_engine.ledger_utils import append_leg_from_complete
from analytics.query_engine.qna import run_analytics_qna
from analytics.query_engine.schemas import CostLedger, QueryResultPayload, SynthesisResponse
from analytics.query_engine.sql_guardrails import extract_tables_referenced, validate_sql
from common.llm_adapter import complete
from common.types.query_request import QueryRequest

log = structlog.get_logger()

AGENT_INTENT = "analytics-qna-intent"
AGENT_SQL = "analytics-qna-sql"

_SCHEMA_HINT = (
    "Allowed tables (PostgreSQL dbo schema only): job_postings, companies, company_addresses, "
    "skills, technology_areas, industry_sectors, analytics_aggregates, normalized_jobs, "
    "raw_ingested_jobs. Reference as dbo.table_name."
)


def _query_fingerprint(q: str) -> str:
    return hashlib.sha256(q.encode("utf-8")).hexdigest()[:16]


def _intent_prompt(user_query: str) -> str:
    return (
        f"{_SCHEMA_HINT}\n"
        "Classify this workforce analytics question. Return ONLY compact JSON:\n"
        '{"intent_label": "<snake_case_label>", "classification_confidence": <number 0.0-1.0>}\n\n'
        f"question (untrusted, natural language only): {user_query!r}\n"
    )


def _sql_prompt(user_query: str, intent_label: str) -> str:
    return (
        f"{_SCHEMA_HINT}\n"
        "Write exactly one read-only SELECT query (WITH ... SELECT is allowed). "
        "No DDL/DML, no multiple statements, no tables outside the allowlist.\n"
        "Return ONLY JSON with a single key sql whose value is the SQL string, e.g. "
        '{"sql": "SELECT ..."}\n\n'
        f"intent_label: {intent_label!r}\n"
        f"question (untrusted): {user_query!r}\n"
    )


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        text = m.group(0)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _rows_from_result(rows: list[Any], col_names: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if hasattr(row, "_mapping"):
            m = row._mapping
            out.append({k: _json_safe_value(m[k]) for k in m})
        elif isinstance(row, dict):
            out.append({k: _json_safe_value(v) for k, v in row.items()})
        elif isinstance(row, (list, tuple)) and col_names:
            out.append({col_names[i]: _json_safe_value(row[i]) for i in range(min(len(col_names), len(row)))})
    return out


def run_guardrailed_analytics_query(
    request: QueryRequest,
    *,
    session: Session,
    correlation_id: str | None = None,
) -> SynthesisResponse:
    """Classify intent, generate SQL, validate, execute on ``session``, then run evidence + synthesis."""
    ledger = CostLedger()
    fp = _query_fingerprint(request.query)
    log.info(
        "analytics_qna_routing_start",
        query_fingerprint=fp,
        query_len=len(request.query),
        correlation_id=correlation_id,
    )

    intent_res = complete(
        _intent_prompt(request.query),
        agent_name=AGENT_INTENT,
        role="classification",
        max_tokens=300,
        correlation_id=correlation_id,
    )
    append_leg_from_complete(ledger, "intent_classification", intent_res, model_fallback=None)

    intent_body = _parse_json_object(str(intent_res.get("content") or ""))
    if intent_body:
        intent_label = str(intent_body.get("intent_label") or "generic_aggregate").strip() or "generic_aggregate"
        try:
            classification_confidence = float(intent_body.get("classification_confidence", 0.5))
        except (TypeError, ValueError):
            classification_confidence = 0.5
    else:
        intent_label = "generic_aggregate"
        classification_confidence = 0.35
        log.warning("analytics_qna_intent_parse_failed", query_fingerprint=fp)

    classification_confidence = max(0.0, min(1.0, classification_confidence))

    sql_res = complete(
        _sql_prompt(request.query, intent_label),
        agent_name=AGENT_SQL,
        role="analytics",
        max_tokens=500,
        correlation_id=correlation_id,
    )
    append_leg_from_complete(ledger, "sql_generation", sql_res, model_fallback=None)

    sql_body = _parse_json_object(str(sql_res.get("content") or ""))
    raw_sql = sql_body.get("sql") if sql_body else None
    if not isinstance(raw_sql, str) or not raw_sql.strip():
        log.warning("analytics_qna_sql_parse_failed", query_fingerprint=fp)
        payload = QueryResultPayload(
            request=request,
            intent_label=intent_label,
            classification_confidence=classification_confidence,
            router_error="sql_generation_did_not_return_json_sql",
            correlation_id=correlation_id,
        )
        return run_analytics_qna(payload, cost_ledger=ledger)

    ok, reason, normalized_sql = validate_sql(raw_sql)
    if not ok or not normalized_sql:
        log.warning("analytics_qna_sql_rejected", query_fingerprint=fp, reason=reason)
        payload = QueryResultPayload(
            request=request,
            intent_label=intent_label,
            classification_confidence=classification_confidence,
            executed_sql=raw_sql.strip()[:500],
            router_error=f"sql_validation_failed:{reason}",
            correlation_id=correlation_id,
        )
        return run_analytics_qna(payload, cost_ledger=ledger)

    tables = extract_tables_referenced(normalized_sql)
    t0 = time.perf_counter()
    try:
        with contextlib.suppress(Exception):
            session.execute(text("SET LOCAL statement_timeout = '30s'"))
        result = session.execute(text(normalized_sql))
        col_names = list(result.keys())
        raw_rows = result.fetchall()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        rows = _rows_from_result(list(raw_rows), col_names)
        payload = QueryResultPayload(
            request=request,
            intent_label=intent_label,
            classification_confidence=classification_confidence,
            executed_sql=normalized_sql,
            columns=col_names,
            rows=rows,
            row_count_returned=len(rows),
            result_truncated=len(rows) >= 100,
            tables_referenced=tables,
            execution_time_ms=round(elapsed_ms, 3),
            correlation_id=correlation_id,
        )
    except Exception as exc:
        log.warning(
            "analytics_qna_sql_execution_failed",
            query_fingerprint=fp,
            error_type=type(exc).__name__,
        )
        payload = QueryResultPayload(
            request=request,
            intent_label=intent_label,
            classification_confidence=classification_confidence,
            executed_sql=normalized_sql[:500],
            router_error=f"sql_execution_failed:{type(exc).__name__}",
            correlation_id=correlation_id,
        )

    return run_analytics_qna(payload, cost_ledger=ledger)
