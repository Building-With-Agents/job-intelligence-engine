"""Analytics REST routes — Q&A via :func:`run_analytics_qna` and on-demand triggers (SQL via sql_guardrails)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
import structlog.contextvars as scv
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from analytics.api.analytics_api_keys import load_api_keys, validate_api_key_header
from analytics.api.laborpulse_wire import (
    resolve_laborpulse_conversation_id,
    to_laborpulse_query_response,
    validate_laborpulse_question,
)
from analytics.api.schemas import (
    CohortGapAnalysisRequest,
    CustomEmployerComparisonRequest,
    EmergingSkillsScanRequest,
    LaborPulseQueryRequest,
    LaborPulseQueryResponse,
    RoleBenchmarkRequest,
    TriggerEnvelope,
)
from analytics.query_engine import audit_log
from analytics.query_engine.execute_safe import execute_validated_query
from analytics.query_engine.routing import run_analytics_qna
from analytics.query_engine.sql_guardrails import validate_sql
from common.data_store.database import session_scope
from common.data_store.models import CohortGapCache

router = APIRouter(prefix="/analytics", tags=["analytics"])

log = structlog.get_logger()

_CACHE_TTL = timedelta(hours=24)
_SAFE_TOKEN = re.compile(r"^[a-zA-Z0-9_.\-]+$")


def _literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _request_hash(trigger_type: str, params: dict[str, Any]) -> str:
    blob = json.dumps({"trigger": trigger_type, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _require_safe_token(value: str, field: str) -> str:
    if not _SAFE_TOKEN.match(value):
        raise ValueError(f"invalid_{field}")
    return value


def _sql_cohort_gap(week_start: str | None) -> str:
    if week_start:
        ws = _literal(_require_safe_token(week_start, "week_start"))
        return (
            "SELECT skill_label, posting_count, employer_count, week_start "
            "FROM dbo.skill_demand_weekly "
            f"WHERE week_start = CAST({ws} AS DATE) "
            "ORDER BY posting_count DESC "
            "LIMIT 50"
        )
    return (
        "SELECT skill_label, posting_count, employer_count, week_start "
        "FROM dbo.skill_demand_weekly "
        "ORDER BY week_start DESC, posting_count DESC "
        "LIMIT 50"
    )


def _sql_role_benchmark(role_id: str, week_start: str | None) -> str:
    rid = _literal(_require_safe_token(role_id, "role_id"))
    q = (
        "SELECT canonical_role_id, posting_count, week_start, avg_salary, top_skills "
        "FROM dbo.role_snapshot_weekly "
        f"WHERE canonical_role_id = {rid} "
    )
    if week_start:
        ws = _literal(_require_safe_token(week_start, "week_start"))
        q += f"AND week_start = CAST({ws} AS DATE) "
    q += "ORDER BY week_start DESC LIMIT 20"
    return q


def _sql_emerging_skills() -> str:
    return (
        "SELECT skill_label, week_over_week_change, four_week_trend, demand_count, week AS week_start "
        "FROM dbo.skill_velocity "
        "ORDER BY week DESC, week_over_week_change DESC NULLS LAST "
        "LIMIT 50"
    )


def _sql_employer_market_context() -> str:
    return (
        "SELECT week_start, sector, posting_count, avg_salary, top_skills "
        "FROM dbo.sector_summary_weekly "
        "ORDER BY week_start DESC, posting_count DESC "
        "LIMIT 40"
    )


def _cache_get(session: Session, trigger_type: str, rhash: str) -> CohortGapCache | None:
    now = datetime.now(timezone.utc)
    row = session.execute(
        select(CohortGapCache).where(
            CohortGapCache.trigger_type == trigger_type,
            CohortGapCache.request_hash == rhash,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.expires_at is not None and row.expires_at <= now:
        return None
    return row


def _cache_put(
    session: Session,
    *,
    trigger_type: str,
    rhash: str,
    cohort_key: str | None,
    params: dict[str, Any],
    gap_data: dict[str, Any],
) -> datetime:
    now = datetime.now(timezone.utc)
    exp = now + _CACHE_TTL
    ins = pg_insert(CohortGapCache).values(
        trigger_type=trigger_type,
        request_hash=rhash,
        cohort_key=cohort_key,
        params=params,
        gap_data=gap_data,
        computed_at=now,
        expires_at=exp,
    )
    stmt = ins.on_conflict_do_update(
        index_elements=["trigger_type", "request_hash"],
        set_={
            "cohort_key": ins.excluded.cohort_key,
            "params": ins.excluded.params,
            "gap_data": ins.excluded.gap_data,
            "computed_at": ins.excluded.computed_at,
            "expires_at": ins.excluded.expires_at,
        },
    )
    session.execute(stmt)
    return now


def run_cohort_gap_analysis(
    session: Session,
    *,
    cohort_key: str,
    week_start: str | None,
) -> TriggerEnvelope:
    params = {"cohort_key": cohort_key, "week_start": week_start}
    ttype = "cohort_gap_analysis"
    rhash = _request_hash(ttype, params)
    cached = _cache_get(session, ttype, rhash)
    if cached is not None:
        return TriggerEnvelope(
            trigger=ttype,
            cached=True,
            computed_at=cached.computed_at.isoformat(),
            data=dict(cached.gap_data),
        )

    sql = _sql_cohort_gap(week_start)
    vr = validate_sql(sql)
    audit_log.log_sql_validation_to_llm_audit(sql_text=sql, is_valid=vr.ok, reason=vr.reason)
    if not vr.ok:
        raise ValueError(vr.reason or "sql_invalid")
    rows, _ = execute_validated_query(session, vr.sql_for_execution)
    gap_data: dict[str, Any] = {
        "cohort_key": cohort_key,
        "week_start": week_start,
        "market_skill_demand": rows,
    }
    now = _cache_put(
        session,
        trigger_type=ttype,
        rhash=rhash,
        cohort_key=cohort_key,
        params=params,
        gap_data=gap_data,
    )
    return TriggerEnvelope(trigger=ttype, cached=False, computed_at=now.isoformat(), data=gap_data)


def run_role_benchmark(
    session: Session,
    *,
    canonical_role_id: str,
    week_start: str | None,
) -> TriggerEnvelope:
    params = {"canonical_role_id": canonical_role_id, "week_start": week_start}
    ttype = "role_benchmark"
    rhash = _request_hash(ttype, params)
    cached = _cache_get(session, ttype, rhash)
    if cached is not None:
        return TriggerEnvelope(
            trigger=ttype,
            cached=True,
            computed_at=cached.computed_at.isoformat(),
            data=dict(cached.gap_data),
        )

    sql = _sql_role_benchmark(canonical_role_id, week_start)
    vr = validate_sql(sql)
    audit_log.log_sql_validation_to_llm_audit(sql_text=sql, is_valid=vr.ok, reason=vr.reason)
    if not vr.ok:
        raise ValueError(vr.reason or "sql_invalid")
    rows, _ = execute_validated_query(session, vr.sql_for_execution)
    gap_data = {"canonical_role_id": canonical_role_id, "week_start": week_start, "snapshots": rows}
    now = _cache_put(
        session,
        trigger_type=ttype,
        rhash=rhash,
        cohort_key=None,
        params=params,
        gap_data=gap_data,
    )
    return TriggerEnvelope(trigger=ttype, cached=False, computed_at=now.isoformat(), data=gap_data)


def run_emerging_skills_scan(
    session: Session,
    *,
    week_start: str | None,
    min_posting_count: int,
) -> TriggerEnvelope:
    params = {"week_start": week_start, "min_posting_count": min_posting_count}
    ttype = "emerging_skills_scan"
    rhash = _request_hash(ttype, params)
    cached = _cache_get(session, ttype, rhash)
    if cached is not None:
        return TriggerEnvelope(
            trigger=ttype,
            cached=True,
            computed_at=cached.computed_at.isoformat(),
            data=dict(cached.gap_data),
        )

    sql = _sql_emerging_skills()
    vr = validate_sql(sql)
    audit_log.log_sql_validation_to_llm_audit(sql_text=sql, is_valid=vr.ok, reason=vr.reason)
    if not vr.ok:
        raise ValueError(vr.reason or "sql_invalid")
    rows, _ = execute_validated_query(session, vr.sql_for_execution)
    filtered = [r for r in rows if int(r.get("demand_count") or 0) >= min_posting_count]
    gap_data = {"week_start": week_start, "skills": filtered}
    now = _cache_put(
        session,
        trigger_type=ttype,
        rhash=rhash,
        cohort_key=None,
        params=params,
        gap_data=gap_data,
    )
    return TriggerEnvelope(trigger=ttype, cached=False, computed_at=now.isoformat(), data=gap_data)


def run_custom_employer_comparison(
    session: Session,
    *,
    company_id: str,
    week_start: str | None,
) -> TriggerEnvelope:
    _require_safe_token(company_id, "company_id")
    params = {"company_id": company_id, "week_start": week_start}
    ttype = "custom_employer_comparison"
    rhash = _request_hash(ttype, params)
    cached = _cache_get(session, ttype, rhash)
    if cached is not None:
        return TriggerEnvelope(
            trigger=ttype,
            cached=True,
            computed_at=cached.computed_at.isoformat(),
            data=dict(cached.gap_data),
        )

    sql = _sql_employer_market_context()
    vr = validate_sql(sql)
    audit_log.log_sql_validation_to_llm_audit(sql_text=sql, is_valid=vr.ok, reason=vr.reason)
    if not vr.ok:
        raise ValueError(vr.reason or "sql_invalid")
    rows, _ = execute_validated_query(session, vr.sql_for_execution)
    gap_data = {
        "company_id": company_id,
        "week_start": week_start,
        "market_sector_context": rows,
        "note": "Employer-specific job rows require validated company scope in a future iteration.",
    }
    now = _cache_put(
        session,
        trigger_type=ttype,
        rhash=rhash,
        cohort_key=company_id,
        params=params,
        gap_data=gap_data,
    )
    return TriggerEnvelope(trigger=ttype, cached=False, computed_at=now.isoformat(), data=gap_data)


@router.post("/query", response_model=LaborPulseQueryResponse)
async def post_analytics_query(
    request: Request,
    *,
    content_type: str | None = Header(default=None, alias="Content-Type"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> LaborPulseQueryResponse:
    """LaborPulse ``POST /analytics/query`` — JSON body, required headers, Q&A pipeline (JIE #222).

    **Body:** ``{ "question": str, "conversation_id": str | null }`` (``LaborPulseQueryRequest``).

    **Headers:** ``Content-Type: application/json``, ``X-Tenant-Id``, ``X-User-Email``,
    ``X-Request-Id``, ``X-API-Key`` (#226). ``X-Request-Id`` is propagated as ``request_id`` in
    structured logs and Langfuse span metadata (``common/llm_adapter``).

    **Issue #197:** ``QueryRouter`` / synthesis path unchanged.

    **Issue #225:** Response matches ``LaborPulseQueryResponse`` (seven fields).

    **Issue #226:** API key allowlist before DB access.
    """
    try:
        ct = (content_type or "").lower().split(";", 1)[0].strip()
        if ct != "application/json":
            raise HTTPException(status_code=400, detail="invalid_content_type")

        raw = await request.body()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="invalid_json") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="invalid_json")

        try:
            body = LaborPulseQueryRequest.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail="invalid_request_body") from exc

        if not (x_tenant_id or "").strip():
            raise HTTPException(status_code=400, detail="missing_tenant_id")
        if not (x_user_email or "").strip():
            raise HTTPException(status_code=400, detail="missing_user_email")
        if not (x_request_id or "").strip():
            raise HTTPException(status_code=400, detail="missing_request_id")

        scv.bind_contextvars(
            request_id=(x_request_id or "").strip(),
            tenant_id=(x_tenant_id or "").strip(),
            user_email=(x_user_email or "").strip(),
        )

        try:
            validate_laborpulse_question(body.question)
        except ValueError as exc:
            code = str(exc)
            if code in {"empty_question", "question_too_short", "question_too_broad"}:
                raise HTTPException(status_code=400, detail=code) from exc
            raise

        allowed = load_api_keys()
        if not allowed:
            if os.getenv("LABORPULSE_ALLOW_NO_API_KEYS", "").strip() == "1":
                scv.bind_contextvars(key_id="none")
                log.info("analytics_query_request", key_id="none", dev_escape="allow_no_api_keys")
            else:
                raise HTTPException(status_code=500, detail="server_misconfigured_no_keys")
        else:
            try:
                rec = validate_api_key_header(provided=x_api_key or "", allowed=allowed)
            except ValueError as exc:
                code = str(exc)
                if code in {"missing_api_key", "invalid_api_key"}:
                    raise HTTPException(status_code=401, detail=code) from exc
                raise HTTPException(status_code=500, detail=code) from exc
            scv.bind_contextvars(key_id=rec.key_id)
            log.info("analytics_query_authenticated", key_id=rec.key_id)

        try:
            conversation_id = resolve_laborpulse_conversation_id(body.conversation_id)
        except ValueError as exc:
            if str(exc) == "invalid_conversation_id":
                raise HTTPException(status_code=400, detail="invalid_conversation_id") from exc
            raise

        correlation = (x_request_id or "").strip()
        with session_scope() as session:
            internal = run_analytics_qna(session, body.question, correlation)
        return to_laborpulse_query_response(internal, conversation_id=conversation_id)
    finally:
        scv.clear_contextvars()


@router.post("/triggers/cohort_gap_analysis", response_model=TriggerEnvelope)
def post_cohort_gap_analysis(body: CohortGapAnalysisRequest):
    try:
        with session_scope() as session:
            return run_cohort_gap_analysis(
                session,
                cohort_key=body.cohort_key,
                week_start=body.week_start,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "query_timeout":
            raise HTTPException(status_code=504, detail="query_timeout") from exc
        raise HTTPException(status_code=500, detail="query_execution_failed") from exc


@router.post("/triggers/role_benchmark", response_model=TriggerEnvelope)
def post_role_benchmark(body: RoleBenchmarkRequest):
    try:
        with session_scope() as session:
            return run_role_benchmark(
                session,
                canonical_role_id=body.canonical_role_id,
                week_start=body.week_start,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "query_timeout":
            raise HTTPException(status_code=504, detail="query_timeout") from exc
        raise HTTPException(status_code=500, detail="query_execution_failed") from exc


@router.post("/triggers/emerging_skills_scan", response_model=TriggerEnvelope)
def post_emerging_skills_scan(body: EmergingSkillsScanRequest):
    try:
        with session_scope() as session:
            return run_emerging_skills_scan(
                session,
                week_start=body.week_start,
                min_posting_count=body.min_posting_count,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "query_timeout":
            raise HTTPException(status_code=504, detail="query_timeout") from exc
        raise HTTPException(status_code=500, detail="query_execution_failed") from exc


@router.post("/triggers/custom_employer_comparison", response_model=TriggerEnvelope)
def post_custom_employer_comparison(body: CustomEmployerComparisonRequest):
    try:
        with session_scope() as session:
            return run_custom_employer_comparison(
                session,
                company_id=body.company_id,
                week_start=body.week_start,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "query_timeout":
            raise HTTPException(status_code=504, detail="query_timeout") from exc
        raise HTTPException(status_code=500, detail="query_execution_failed") from exc
