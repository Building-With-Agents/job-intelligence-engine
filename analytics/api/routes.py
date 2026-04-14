"""Analytics REST routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from analytics.api.schemas import (
    AnalyticsQueryRequest,
    AnalyticsQueryResponse,
    CohortGapAnalysisRequest,
    CustomEmployerComparisonRequest,
    EmergingSkillsScanRequest,
    RoleBenchmarkRequest,
    TriggerEnvelope,
)
from analytics.query_engine.pipeline import run_analytics_query
from analytics.query_engine.trigger_handlers import (
    run_cohort_gap_analysis,
    run_custom_employer_comparison,
    run_emerging_skills_scan,
    run_role_benchmark,
)
from common.data_store.database import session_scope

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/query", response_model=AnalyticsQueryResponse)
def post_analytics_query(body: AnalyticsQueryRequest) -> AnalyticsQueryResponse:
    with session_scope() as session:
        return run_analytics_query(session, body.question, body.correlation_id)


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
