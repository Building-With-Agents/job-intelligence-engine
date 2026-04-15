"""Route intent → candidate SQL (LLM optional, always validated downstream)."""

from __future__ import annotations

import json
import os
import re

import structlog

from analytics.query_engine.intent import QueryIntent, QueryIntentKind
from common.llm_adapter import complete

log = structlog.get_logger()

_AGENT = "analytics-query-router"


def _extract_json_sql(content: str) -> str | None:
    text = (content or "").strip()
    m = re.search(r"\{[\s\S]*\"sql\"[\s\S]*\}", text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    sql = obj.get("sql")
    return str(sql).strip() if sql else None


def _template_sql(intent: QueryIntent) -> str:
    """Deterministic read-only queries over allowlisted aggregate tables."""
    k = intent.kind
    if k == QueryIntentKind.GEO_DEMAND:
        return (
            "SELECT week_start, borderplex_subregion, posting_count "
            "FROM dbo.geo_demand_weekly ORDER BY week_start DESC, posting_count DESC LIMIT 100"
        )
    if k == QueryIntentKind.ROLE_SNAPSHOT:
        return (
            "SELECT week_start, canonical_role_id, posting_count, role_title "
            "FROM dbo.role_snapshot_weekly ORDER BY week_start DESC LIMIT 100"
        )
    if k == QueryIntentKind.SECTOR:
        return (
            "SELECT week_start, sector, posting_count, employer_count, avg_salary "
            "FROM dbo.sector_summary_weekly ORDER BY week_start DESC LIMIT 100"
        )
    if k == QueryIntentKind.VELOCITY:
        return (
            "SELECT skill_label, week, demand_count, week_over_week_change, four_week_trend "
            "FROM dbo.skill_velocity ORDER BY week DESC LIMIT 100"
        )
    if k == QueryIntentKind.CO_OCCURRENCE:
        return (
            "SELECT skill_a, skill_b, co_occurrence_count, week_start "
            "FROM dbo.skill_co_occurrence ORDER BY week_start DESC, co_occurrence_count DESC LIMIT 100"
        )
    return (
        "SELECT week_start, skill_label, posting_count, employer_count "
        "FROM dbo.skill_demand_weekly ORDER BY week_start DESC, posting_count DESC LIMIT 100"
    )


def generate_sql(user_query: str, intent: QueryIntent) -> tuple[str, float]:
    """Return ``(sql, llm_cost_usd)`` — LLM path optional via env."""
    template = _template_sql(intent)
    if os.getenv("ANALYTICS_QUERY_USE_LLM_SQL", "").strip().lower() not in ("1", "true", "yes"):
        return template, 0.0

    system = (
        "You output a single JSON object only, no prose. Keys: \"sql\". "
        "The value must be one PostgreSQL SELECT statement using ONLY these tables (dbo schema allowed): "
        "skill_demand_weekly, tool_demand_weekly, role_snapshot_weekly, sector_summary_weekly, "
        "geo_demand_weekly, skill_velocity, skill_co_occurrence, posting_freshness, trajectory_map, "
        "analytics_pipeline_state, cohort_gap_cache, disruption_fingerprints, canonical_roles. "
        "Must include LIMIT 100 or less. No INSERT/UPDATE/DELETE/DROP."
    )
    prompt = f"User question:\n{user_query}\n\nReturn JSON: {{\"sql\": \"...\"}}"
    try:
        out = complete(
            prompt=prompt,
            agent_name=_AGENT,
            system=system,
            max_tokens=500,
            role="analytics",
        )
    except Exception as exc:
        log.warning("analytics_router_llm_failed", error=str(exc))
        return template, 0.0

    cost = float(out.get("cost_usd") or 0.0)
    if out.get("extraction_failed") or not out.get("content"):
        return template, cost

    extracted = _extract_json_sql(str(out.get("content", "")))
    if extracted:
        return extracted, cost
    return template, cost
