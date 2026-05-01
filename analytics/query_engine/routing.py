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
from analytics.conversation_memory import append_conversation_turn, load_prior_context_for_llm
from analytics.query_engine import audit_log, qna
from analytics.query_engine.curriculum_path import CurriculumInputs, build_curriculum_inputs
from analytics.query_engine.curriculum_synthesis import no_resolved_role_message, synthesize_curriculum_outline
from analytics.query_engine.intent import classify_workforce_question
from analytics.query_engine.langfuse_utils import lf_context as _lf_ctx
from analytics.query_engine.langfuse_utils import lf_observe as _lf_observe
from analytics.query_engine.langfuse_utils import report_langfuse_usage
from analytics.query_engine.ledger_utils import append_leg_from_complete
from analytics.query_engine.router import QueryRouter
from analytics.query_engine.schemas import CostLedger, QueryResultPayload, SynthesisResponse
from analytics.query_engine.sql_guardrails import (
    extract_tables_referenced,
    inject_role_classification_issue197_guard,
    validate_ask_the_data_sql,
)
from analytics.tenant_scope import (
    RegionNotEntitledError,
    TenantAccess,
    check_region_entitled,
    get_tenant_access_for_pipeline,
)
from common.llm_adapter import complete
from common.types.query_request import QueryRequest

log = structlog.get_logger()


@_lf_observe(as_type="generation", name="sql_generation")
def _call_sql_generation_llm(
    prompt: str,
    *,
    query_fingerprint: str,
    correlation_id: str | None,
) -> dict[str, Any]:
    """SQL generation LLM call; wrapped as a named Langfuse generation (JIE #258).

    Each call appears as a separate generation observation inside the parent Q&A
    trace, enabling per-stage cost attribution and latency breakdown.
    """
    _lf_ctx.update_current_observation(
        input=prompt,
        metadata={"agent_name": AGENT_SQL, "role": "analytics", "query_fingerprint": query_fingerprint},
    )
    result = complete(
        prompt,
        agent_name=AGENT_SQL,
        role="analytics",
        max_tokens=500,
        correlation_id=correlation_id,
    )
    report_langfuse_usage(result)
    _lf_ctx.update_current_observation(output=result.get("content") or "")
    return result


_SQL_EXEC_ERR_DETAIL_MAX = 400

# Issue #197 — ORM / QueryRouter path parity with guardrailed ``inject_role_classification_issue197_guard``
_ISSUE197_INTENTS: frozenset[str] = frozenset({"employer", "curriculum", "workflow"})
_ISSUE197_SQL_GUARD_HINT = (
    "Always include WHERE role_classification != 'N/A Not an IT role' "
    "in any query that references the role_classification column. "
    "This guards against a known classifier bug (issue #197) where "
    "real IT roles are misbucketed."
)
_NA_IT_ROLE_PLACEHOLDER = "N/A Not an IT role"

# JIE #298 — intents that filter by canonical role; get live suggestions on 0 rows.
_ROLE_FILTERED_INTENTS: frozenset[str] = frozenset({"curriculum", "workflow", "role_evolution"})


def _get_role_classification_value(row: dict[str, Any]) -> str | None:
    if "role_classification" in row and row["role_classification"] is not None:
        return str(row["role_classification"]).strip()
    for key, val in row.items():
        if str(key).lower() == "role_classification" and val is not None:
            return str(val).strip()
    return None


def _filter_issue197_misbucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exclude rows whose ``role_classification`` is the known non-IT mis-bucket (#197)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        rc = _get_role_classification_value(row)
        if rc == _NA_IT_ROLE_PLACEHOLDER:
            continue
        out.append(row)
    return out


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
# #197 (role_classification mis-bucket guard for employer / curriculum / workflow NL→SQL).
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
               date_posted, seniority_level, is_remote, role_classification
                 (enrichment classifier; ~657 IT postings mislabeled as 'N/A Not an IT role' — see issue #197),
               borderplex_subregion, temporal_period, spam_tier, quality_score, is_spam,
               soc_code, naics_code, canonical_role_id, employer_profile_id,
               is_duplicate, zip_code)
  normalized_jobs(id, source, external_id, title, company, date_posted,
                  salary_min, salary_max, salary_currency, salary_period,
                  city, state_province, country, is_remote, work_arrangement)
  companies(company_id, company_name, industry_sector_id, size, city, state, normalized_location)
  employer_profiles(id, company_id, company_size, ai_maturity_signal, sector, is_known_employer)
  industry_sectors(industry_sector_id, sector_title)
  postal_geo_data(zip, city, state_code, county, latitude, longitude)
    Geographic reference; join to job_postings via jp.zip_code = pgd.zip for sub-region filters.

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

INTENT geographic — list-style vs aggregate-style routing (issue #306):
  "How many postings in {region}?" / trend / share-by-region questions: use
    dbo.geo_demand_weekly (sub-region grain, weekly counts only).
  "Show / list / find / pull all postings in {region} for {role|skill}":
    use dbo.job_postings JOINed to dbo.postal_geo_data — geo_demand_weekly
    has no per-posting detail and will return refusal text.
  Canonical sub-region pattern (El Paso / Las Cruces / county-level filtering):
    SELECT jp.job_posting_id, jp.job_title, c.company_name,
           jp.date_posted, pgd.city, pgd.state_code, pgd.county
    FROM dbo.job_postings AS jp
    JOIN dbo.postal_geo_data AS pgd ON pgd.zip = jp.zip_code
    LEFT JOIN dbo.companies AS c ON c.company_id = jp.company_id
    WHERE pgd.county = 'El Paso'           -- or pgd.city ILIKE 'El Paso'
      AND jp.is_spam = FALSE
      AND jp.date_posted >= NOW() - INTERVAL '90 days'
    ORDER BY jp.date_posted DESC
    LIMIT 100
  When the user names a role or skill family, layer on the operational join:
    JOIN dbo.normalized_jobs nj ON jp.source = nj.source AND jp.external_id = nj.external_id
    JOIN dbo.extracted_intelligence ei ON ei.normalized_job_id = nj.id
  Then unnest ei.skills / ei.tools to filter by skill_label.

CRITICAL — columns that do NOT exist (never generate SQL referencing these):
  - job_postings has NO skill_id, skills, or posted_date column.
    Skills are pre-aggregated in dbo.skill_demand_weekly.
    For "top skills by posting count" use: SELECT skill_label, posting_count
    FROM dbo.skill_demand_weekly ORDER BY posting_count DESC LIMIT 10
  - job_postings has NO tasks, responsibilities, or context column.
    Those live as JSONB arrays on dbo.extracted_intelligence — see unnest patterns above.
  - Never reference: publish_date, employer_id, tech_area_id, start_date, end_date, location_id.
    These columns are deprecated (99-100% NULL) and must not appear in any query.

INTENT employer | curriculum | workflow — role_classification guard (issue #197):
  Whenever generated SQL references dbo.job_postings.role_classification (including JOINs and
  subqueries on job_postings), you MUST restrict out the known mis-bucketed placeholder:
    AND <alias>.role_classification <> 'N/A Not an IT role'
  Use the real alias for dbo.job_postings (e.g. jp.role_classification if FROM dbo.job_postings AS jp).
  Apply on every SELECT that reads role_classification from job_postings so results are not
  polluted by ~657 real IT roles misclassified as non-IT.\
"""

_SQL_INTENTS_ROLE_CLASS_GUARD = (
    "INTENT-SPECIFIC (employer | curriculum | workflow): If your SELECT references "
    "dbo.job_postings.role_classification, always add AND <jp>.role_classification <> "
    "'N/A Not an IT role' (use the correct job_postings alias). Issue #197 — classifier "
    "mislabels many IT postings; never return that bucket as if it were a reliable IT slice.\n"
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
    il = (intent_label or "").strip().lower()
    role_guard = _SQL_INTENTS_ROLE_CLASS_GUARD if il in ("employer", "curriculum", "workflow") else ""
    return (
        f"{_SCHEMA_HINT}\n"
        f"{role_guard}"
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

    sql_res = _call_sql_generation_llm(
        _sql_prompt(request.query, intent_label),
        query_fingerprint=fp,
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
    if ok and normalized_sql:
        guarded = inject_role_classification_issue197_guard(
            normalized_sql,
            intent_label=intent_label,
        )
        if guarded != normalized_sql:
            ok2, reason2, normalized_sql2 = validate_ask_the_data_sql(guarded)
            if ok2 and normalized_sql2:
                normalized_sql = normalized_sql2
            else:
                log.warning(
                    "issue197_role_class_guard_validation_failed",
                    reason=reason2,
                    intent_label=intent_label,
                )

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
    row_count_returned: int = 0,
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
        row_count_returned=max(0, int(row_count_returned)),
    )


def _extract_recommended_follow_up_section(markdown: str) -> str:
    text = markdown or ""
    m = re.search(
        r"(?msi)^\s*##\s+recommended follow-up\s*\r?\n(.*?)(?=^\s*##\s+)",
        text,
    )
    if not m:
        m = re.search(r"(?msi)^\s*##\s+recommended follow-up\s*\r?\n(.*)\Z", text)
    return m.group(1).strip() if m else ""


def _curriculum_to_analytics_response(
    *,
    ins: CurriculumInputs,
    synthesis: dict[str, Any],
    intent_label: str,
    classification_confidence: float,
) -> AnalyticsQueryResponse:
    """Shape curriculum synthesis for :class:`AnalyticsQueryResponse` (API parity with other intents)."""
    answer = (synthesis.get("answer") or "").strip()
    is_suff = bool(synthesis.get("is_sufficient"))
    modules: list[str] = list(synthesis.get("modules") or [])

    if is_suff and modules:
        out_conf = 0.9
    elif is_suff:
        out_conf = 0.4
    else:
        out_conf = 0.0

    follow_text = _extract_recommended_follow_up_section(answer)
    follow_up: list[str] = [follow_text] if follow_text else []

    evidence: list[EvidenceItem] = [
        EvidenceItem(
            title="skill_demand_weekly",
            source="dbo.skill_demand_weekly",
            snippet=(
                f"Top skills for curriculum inputs: {len(ins.top_skills)} skills "
                f"(demand week aligned in period; see data_flags.top_skills={ins.data_flags.get('top_skills', True)})."
            ),
            supporting_count=len(ins.top_skills) or None,
            time_period=ins.period,
        ),
        EvidenceItem(
            title="skill_velocity",
            source="dbo.skill_velocity",
            snippet=(
                f"Rising skills: {len(ins.rising_skills)} rows; "
                f"data_flags.rising_skills={ins.data_flags.get('rising_skills', True)}."
            ),
            supporting_count=len(ins.rising_skills) or None,
            time_period=ins.period,
        ),
        EvidenceItem(
            title="extracted_intelligence",
            source="dbo.extracted_intelligence",
            snippet=(
                f"Tool/responsibility co-occurrence pairs: {len(ins.co_occurring)}; "
                f"data_flags.co_occurring={ins.data_flags.get('co_occurring', True)}."
            ),
            supporting_count=len(ins.co_occurring) or None,
            time_period=ins.period,
        ),
        EvidenceItem(
            title="employer_profiles",
            source="dbo.employer_profiles",
            snippet=(
                f"Top employers: {len(ins.top_employers)}; "
                f"data_flags.top_employers={ins.data_flags.get('top_employers', True)}."
            ),
            supporting_count=len(ins.top_employers) or None,
            time_period=ins.period,
        ),
    ]
    ic = max(0.0, min(1.0, float(classification_confidence)))
    return AnalyticsQueryResponse(
        answer=answer or "No answer could be generated for this question.",
        evidence=evidence,
        confidence=float(out_conf),
        classified_intent=str(intent_label or "curriculum"),
        intent_classification_confidence=ic,
        periods_described=ins.period,
        confidence_flagged_low=bool(out_conf < 0.6),
        confidence_explanation=None,
        volume_flagged_low=False,
        volume_warning=None,
        refused=False,
        refusal_message=None,
        sql_execution_error_detail=None,
        follow_up_questions=follow_up,
        sql_generated="curriculum_path (multi-table ORM)",
        cost_usd=0.0,
        total_cost_usd=0.0,
        cost_breakdown_usd={},
        row_count_returned=max(0, int(len(ins.top_skills))),
    )


def run_analytics_qna(
    session: Session,
    question: str,
    correlation_id: str | None,
    *,
    laborpulse_conversation_id: str | None = None,
    tenant_id: str | None = None,
    user_email: str | None = None,
    tenant_access: TenantAccess | None = None,
) -> AnalyticsQueryResponse:
    """Run Q&A inside an open SQLAlchemy session (same transaction as caller).

    JIE #223: pass ``laborpulse_conversation_id`` + ``tenant_id`` + ``user_email`` to load/save
    multi-turn Q&A in ``dbo.laborpulse_analytics_*`` (LaborPulse /analytics/query).

    JIE #224: pass ``tenant_access`` from the LaborPulse API (or omit to default to Borderplex for
    local callers); enforces subregion SQL filters and region entitlement.
    """
    cid = correlation_id or str(uuid.uuid4())
    endpoint = "POST /analytics/query"
    q = (question or "").strip()
    payload_audit: dict[str, Any] = {}
    tid = (tenant_id or "").strip()
    uem = (user_email or "").strip()
    taccess = tenant_access or get_tenant_access_for_pipeline(tid or None)
    prior_for_llm = ""
    if laborpulse_conversation_id and laborpulse_conversation_id.strip() and tid and uem:
        prior_for_llm = load_prior_context_for_llm(
            session,
            conversation_id=laborpulse_conversation_id.strip(),
            tenant_id=tid,
            user_email=uem,
        )
        if prior_for_llm:
            log.info(
                "laborpulse_prior_context_loaded",
                request_id=cid,
                conversation_id=laborpulse_conversation_id.strip(),
            )

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
            tenant_id=taccess.tenant_id,
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
        pctx = (prior_for_llm or "").strip() or None
        classification = classify_workforce_question(
            q,
            correlation_id=cid,
            cost_ledger=ledger,
            conversation_context=pctx,
        )
        intent_label = str(classification.get("intent") or "other")
        conf = float(classification.get("confidence") or 0.0)
        payload_audit = {
            "intent": intent_label,
            "classification_confidence": conf,
            "needs_clarification": classification.get("needs_clarification"),
        }

        if intent_label in _ISSUE197_INTENTS:
            classification = {
                **classification,
                "issue197_sql_guard_hint": _ISSUE197_SQL_GUARD_HINT,
            }
            log.info(
                "issue197_orm_guard_hint_attached",
                intent=intent_label,
                correlation_id=cid,
            )

        ent = classification.get("extracted_entities")
        if isinstance(ent, dict):
            check_region_entitled(taccess, q, ent)
        else:
            check_region_entitled(taccess, q, {})

        if intent_label == "curriculum":
            role_names_for_ci: list[str] | None = None
            if isinstance(classification.get("extracted_entities"), dict):
                rn = classification["extracted_entities"].get("role_names")
                if isinstance(rn, list):
                    role_names_for_ci = [str(x) for x in rn if x]
            ins = build_curriculum_inputs(session, q, role_names=role_names_for_ci)
            syn_out = synthesize_curriculum_outline(ins, correlation_id=cid)
            api = _curriculum_to_analytics_response(
                ins=ins,
                synthesis=syn_out,
                intent_label=intent_label,
                classification_confidence=conf,
            )
            audit_log.insert_orchestration_audit(
                session,
                correlation_id=cid,
                endpoint=endpoint,
                question=q,
                sql_generated="curriculum_path (multi-table ORM)",
                confidence=float(api.confidence),
                success=True,
                error_code=None,
                payload={
                    **payload_audit,
                    "row_count": len(ins.top_skills),
                    "citations": len(api.evidence),
                    "refused": False,
                    "curriculum_is_sufficient": bool(syn_out.get("is_sufficient")),
                    "curriculum_modules": len(syn_out.get("modules") or []),
                },
                tenant_id=taccess.tenant_id,
            )
            if laborpulse_conversation_id and laborpulse_conversation_id.strip() and tid and uem:
                append_conversation_turn(
                    session,
                    conversation_id=laborpulse_conversation_id.strip(),
                    tenant_id=tid,
                    user_email=uem,
                    question=q,
                    answer=api.answer,
                    intent_label=intent_label,
                )
            return api

        router = QueryRouter()
        route_result = router.route(classification, session, tenant=taccess, question=q)

        route_conf = float(getattr(route_result, "confidence", conf))
        effective_classification_confidence = min(float(conf), route_conf)
        no_data_override = getattr(route_result, "empty_rows_refusal_reason", None)

        rows = _json_safe_rows(route_result.rows)
        # Capture the router's raw row count before any post-processing filter so
        # JIE #298's role-hint logic can distinguish "router found nothing" from
        # "issue-197 misbucket guard removed everything."
        router_row_count = len(rows)
        if intent_label in _ISSUE197_INTENTS:
            rows = _filter_issue197_misbucket_rows(rows)
        col_names = list(rows[0].keys()) if rows else []
        router_error = _router_error_message(route_result)
        sql_line = _sql_generated_line(route_result)

        # JIE #298 — when a role-filtered intent yields 0 rows, surface live
        # canonical role suggestions instead of the generic "No data in scope" message.
        # Guards:
        # - router_row_count == 0: the router itself found nothing (not the misbucket filter).
        # - not router_error: don't double-diagnose a hard execution failure.
        # - role_names from extracted_entities: use the structured role names the classifier
        #   already identified, not the raw question text which produces nonsensical output.
        role_hint: str | None = None
        if router_row_count == 0 and not router_error and intent_label in _ROLE_FILTERED_INTENTS:
            ent = classification.get("extracted_entities") or {}
            role_names: list[str] = ent.get("role_names") or [] if isinstance(ent, dict) else []
            role_query_str = ", ".join(role_names) if role_names else ""
            role_hint = no_resolved_role_message(
                session,
                role_query=role_query_str,
                intent=intent_label,
            )

        q_payload = QueryResultPayload(
            request=QueryRequest(query=q, prior_turns_context=pctx),
            intent_label=intent_label,
            classification_confidence=effective_classification_confidence,
            executed_sql=None,
            columns=col_names,
            rows=rows,
            row_count_returned=len(rows),
            result_truncated=bool(route_result.is_partial),
            tables_referenced=list(route_result.tables_used),
            router_error=router_error,
            correlation_id=cid,
            role_suggestion_hint=role_hint,
            no_data_refusal_override=no_data_override,
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
            tenant_id=taccess.tenant_id,
        )

        api = _synthesis_to_api(
            syn,
            sql_generated=sql_line,
            intent_label=intent_label,
            classification_confidence=effective_classification_confidence,
            row_count_returned=int(q_payload.row_count_returned),
        )
        if laborpulse_conversation_id and laborpulse_conversation_id.strip() and tid and uem:
            append_conversation_turn(
                session,
                conversation_id=laborpulse_conversation_id.strip(),
                tenant_id=tid,
                user_email=uem,
                question=q,
                answer=api.answer,
                intent_label=intent_label,
            )
        return api

    except RegionNotEntitledError as rne:
        audit_log.insert_orchestration_audit(
            session,
            correlation_id=cid,
            endpoint=endpoint,
            question=q,
            sql_generated=None,
            confidence=0.0,
            success=False,
            error_code="region_not_entitled",
            payload={
                **payload_audit,
                "requested_region": rne.requested_region,
            },
            tenant_id=taccess.tenant_id,
        )
        raise
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
            tenant_id=taccess.tenant_id,
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
            tenant_id=taccess.tenant_id,
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
