"""Analytics Q&A routing: guardrailed NL→SQL (Ask the Data) and HTTP ORM path (GitHub #117).

``run_guardrailed_analytics_query`` uses ``common.llm_adapter.complete`` only. User text is
never concatenated into executable SQL; model output is validated via
:func:`validate_ask_the_data_sql`.

``run_analytics_qna`` in this module is the **REST** entrypoint (session, question, correlation
id) that delegates evidence + synthesis to :func:`analytics.query_engine.qna.run_analytics_qna`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from analytics.api.schemas import AnalyticsQueryResponse, EvidenceItem
from analytics.query_engine import audit_log, qna
from analytics.query_engine.intent import classify_workforce_question
from analytics.query_engine.ledger_utils import append_leg_from_complete
from analytics.query_engine.router import QueryRouter
from analytics.query_engine.schemas import CostLedger, QueryResultPayload, SynthesisResponse
from analytics.query_engine.sql_guardrails import extract_tables_referenced, validate_ask_the_data_sql
from common.llm_adapter import complete
from common.types.query_request import QueryRequest

log = structlog.get_logger()

_SQL_EXEC_ERR_DETAIL_MAX = 400

AGENT_INTENT = "analytics-qna-intent"
AGENT_SQL = "analytics-qna-sql"


def _truncate_sql_execution_error(exc: BaseException, *, max_len: int = _SQL_EXEC_ERR_DETAIL_MAX) -> str:
    """Single-line, length-capped message for logs and UI (no user query text)."""
    parts: list[str] = []
    root = str(exc).strip()
    if root:
        parts.append(root)
    inner = getattr(exc, "__cause__", None)
    if inner is not None:
        s = str(inner).strip()
        if s and s not in parts:
            parts.append(s)
    if not parts:
        return ""
    raw = " | ".join(parts)
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) > max_len:
        return raw[: max_len - 3].rstrip() + "..."
    return raw


# Derived from docs/planning/QA_DATA_CONTRACT.md — update that doc first, then re-derive here.
# Tracks: GitHub #186 (hotfix), #171 (full contract fix — date_posted/seniority_level/is_remote
# columns promoted to job_postings by #170; CRITICAL block updated accordingly).
# Extended: #173 (role_classification promoted), #174 (structured salary columns promoted),
# #199 (JSONB unnest patterns for tasks/responsibilities/context — no-aggregate dimensions).
_SCHEMA_HINT = """\
Allowed tables (PostgreSQL dbo schema only; always reference as dbo.table_name):

PREFER aggregate tables for skill/tool/role/sector/geo count and trend questions:
  skill_demand_weekly(skill_label, week_start, posting_count, employer_count, computed_at)
  tool_demand_weekly(tool_label, week_start, posting_count, computed_at)
  role_snapshot_weekly(week_start, canonical_role_id, role_title, posting_count,
                       avg_salary, median_salary, salary_p25, salary_p50,
                       salary_p75, salary_p95, top_skills, top_tools, computed_at)
  sector_summary_weekly(week_start, sector, posting_count, employer_count, avg_salary, top_skills)
  geo_demand_weekly(week_start, borderplex_subregion, posting_count)
  skill_velocity(skill_label, week, demand_count, week_over_week_change, four_week_trend)
  skill_co_occurrence(skill_a, skill_b, co_occurrence_count, week_start)
  posting_freshness(posting_id, first_seen, last_seen, duration_days, is_repost, repost_count)
  canonical_roles(role_id, label, description, posting_count, top_skills, top_tools, computed_at)

Operational tables (use when aggregates cannot answer):
  job_postings(job_posting_id, company_id, job_title, employment_type, location,
               salary_range, salary_min, salary_max, salary_currency, salary_period,
               status, source, external_id, createdat, ingestion_run_id,
               date_posted, seniority_level, is_remote, role_classification,
               borderplex_subregion, temporal_period, spam_tier, quality_score, is_spam,
               soc_code, naics_code, canonical_role_id, employer_profile_id,
               is_duplicate, zip_code)
  normalized_jobs(id, source, external_id, title, company, date_posted,
                  salary_min, salary_max, salary_currency, salary_period,
                  city, state_province, country, is_remote, work_arrangement)
  companies(company_id, company_name, industry_sector_id, size, city, state, normalized_location)
  employer_profiles(id, company_id, company_size, ai_maturity_signal, sector, is_known_employer)
  industry_sectors(industry_sector_id, sector_title)

Extracted intelligence (JSONB unnest — ONLY path for task / responsibility / context questions):
  extracted_intelligence(id, normalized_job_id, skills, tools, tasks, responsibilities, context)
    skills, tools, tasks, responsibilities, context are JSONB arrays of objects.
    skills + tools have aggregate tables above — PREFER those for counts.
    tasks, responsibilities, context have NO aggregate — JSONB unnest is the only path.

  Two-hop join from job_postings to extracted_intelligence:
    JOIN dbo.normalized_jobs nj ON jp.source = nj.source AND jp.external_id = nj.external_id
    JOIN dbo.extracted_intelligence ei ON ei.normalized_job_id = nj.id

  Unnest tasks (no aggregate exists). Element fields:
    task_description, task_category, seniority_signal, confidence, source_span
    Example:
      SELECT elem->>'task_description' AS task,
             elem->>'task_category'    AS category,
             elem->>'seniority_signal' AS seniority,
             COUNT(*) AS task_count
      FROM dbo.extracted_intelligence ei,
           jsonb_array_elements(ei.tasks) AS elem
      WHERE (elem->>'confidence')::float >= 0.75
      GROUP BY task, category, seniority
      ORDER BY task_count DESC LIMIT 20

  Unnest responsibilities. Element fields:
    responsibility_description, scope, requires_ai_competency, confidence, source_span
    Example:
      SELECT elem->>'scope' AS scope, COUNT(*) AS n
      FROM dbo.extracted_intelligence ei,
           jsonb_array_elements(ei.responsibilities) AS elem
      WHERE (elem->>'requires_ai_competency')::bool = TRUE
      GROUP BY scope ORDER BY n DESC

  Unnest context. Element fields:
    signal_type (remote_policy|team_size|reporting_structure|work_methodology|ai_adoption_signal),
    value, confidence, source_span
    Example:
      SELECT elem->>'value' AS work_methodology, COUNT(*) AS n
      FROM dbo.extracted_intelligence ei,
           jsonb_array_elements(ei.context) AS elem
      WHERE elem->>'signal_type' = 'work_methodology'
      GROUP BY work_methodology ORDER BY n DESC LIMIT 10

CRITICAL — columns that do NOT exist (never generate SQL referencing these):
  - job_postings has NO skill_id, skills, or posted_date column.
    Skills are pre-aggregated in dbo.skill_demand_weekly.
    For "top skills by posting count" use: SELECT skill_label, posting_count
    FROM dbo.skill_demand_weekly ORDER BY posting_count DESC LIMIT 10
  - job_postings has NO tasks, responsibilities, or context column.
    Those live as JSONB arrays on dbo.extracted_intelligence — see unnest patterns above.
  - Never reference: publish_date, employer_id, tech_area_id, start_date, end_date, location_id.
    These columns are deprecated (99-100% NULL) and must not appear in any query.\
"""


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
        return qna.run_analytics_qna(payload, cost_ledger=ledger)

    ok, reason, normalized_sql = validate_ask_the_data_sql(raw_sql)
    audit_sql = normalized_sql if ok and normalized_sql else raw_sql.strip()
    audit_log.log_sql_validation_to_llm_audit(
        sql_text=audit_sql,
        is_valid=bool(ok and normalized_sql),
        reason=None if ok and normalized_sql else reason,
    )
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
        return qna.run_analytics_qna(payload, cost_ledger=ledger)

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
        err_detail = _truncate_sql_execution_error(exc)
        log.warning(
            "analytics_qna_sql_execution_failed",
            query_fingerprint=fp,
            error_type=type(exc).__name__,
            error_detail=err_detail or None,
        )
        payload = QueryResultPayload(
            request=request,
            intent_label=intent_label,
            classification_confidence=classification_confidence,
            executed_sql=normalized_sql[:500],
            router_error=f"sql_execution_failed:{type(exc).__name__}",
            sql_execution_error_detail=err_detail or None,
            correlation_id=correlation_id,
        )

    return qna.run_analytics_qna(payload, cost_ledger=ledger)


# --- HTTP / ORM path (same transaction as caller) ---------------------------------


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


def _synthesis_to_api(
    sr: SynthesisResponse,
    *,
    sql_generated: str,
    intent_label: str,
    classification_confidence: float,
) -> AnalyticsQueryResponse:
    answer = (sr.refusal_message or "").strip() if sr.refused else (sr.answer_text or "").strip()
    if not answer and sr.refusal_message:
        answer = sr.refusal_message
    evidence = [
        EvidenceItem(
            title=c.citation_id,
            source=(c.source_table or ""),
            snippet=c.summary,
            supporting_count=c.supporting_count,
            time_period=c.time_period,
        )
        for c in sr.citations
    ]
    ic = max(0.0, min(1.0, float(classification_confidence)))
    return AnalyticsQueryResponse(
        answer=answer or "No answer could be generated for this question.",
        evidence=evidence,
        confidence=float(sr.confidence),
        classified_intent=str(intent_label or "other"),
        intent_classification_confidence=ic,
        periods_described=sr.periods_described,
        confidence_flagged_low=bool(sr.confidence_flagged_low),
        confidence_explanation=sr.confidence_explanation,
        volume_flagged_low=bool(sr.volume_flagged_low),
        volume_warning=sr.volume_warning,
        refused=bool(sr.refused),
        refusal_message=sr.refusal_message,
        sql_execution_error_detail=sr.sql_execution_error_detail,
        follow_up_questions=list(sr.follow_up_questions or []),
        sql_generated=sql_generated,
        cost_usd=float(sr.total_cost_usd),
        total_cost_usd=float(sr.total_cost_usd),
        cost_breakdown_usd={leg: float(cost) for leg, cost in sr.cost_breakdown_usd.items()},
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
            classified_intent="other",
            intent_classification_confidence=0.0,
            follow_up_questions=[],
            sql_generated="",
            cost_usd=0.0,
        )

    try:
        ledger = CostLedger()
        classification = classify_workforce_question(q, correlation_id=cid, cost_ledger=ledger)
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
        col_names = list(rows[0].keys()) if rows else []
        router_error = _router_error_message(route_result)
        sql_line = _sql_generated_line(route_result)

        q_payload = QueryResultPayload(
            request=QueryRequest(query=q),
            intent_label=intent_label,
            classification_confidence=conf,
            executed_sql=None,
            columns=col_names,
            rows=rows,
            row_count_returned=int(route_result.row_count),
            result_truncated=bool(route_result.is_partial),
            tables_referenced=list(route_result.tables_used),
            router_error=router_error,
            correlation_id=cid,
        )

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

        return _synthesis_to_api(
            syn,
            sql_generated=sql_line,
            intent_label=intent_label,
            classification_confidence=conf,
        )

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
        ei = str(payload_audit.get("intent") or "other")
        ec = float(payload_audit.get("classification_confidence") or 0.0)
        ec = max(0.0, min(1.0, ec))
        return AnalyticsQueryResponse(
            answer=msg,
            evidence=[],
            confidence=0.0,
            classified_intent=ei,
            intent_classification_confidence=ec,
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
        ei = str(payload_audit.get("intent") or "other")
        ec = float(payload_audit.get("classification_confidence") or 0.0)
        ec = max(0.0, min(1.0, ec))
        return AnalyticsQueryResponse(
            answer="An unexpected error occurred while processing your question.",
            evidence=[],
            confidence=0.0,
            classified_intent=ei,
            intent_classification_confidence=ec,
            follow_up_questions=[],
            sql_generated="",
            cost_usd=0.0,
        )
