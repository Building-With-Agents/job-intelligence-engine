"""Generate candidate read-only SQL from a natural-language question (LLM-backed)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import structlog

from common.llm_adapter import complete

log = structlog.get_logger()

_ROUTER_SYSTEM = """You are a Postgres analytics assistant. Schema is dbo.
Return a single JSON object ONLY, no markdown, with key "sql" whose value is ONE read-only
SELECT statement using only dbo tables. Use LIMIT 100 or less at the end.
Allowed tables include: job_postings, companies, skills, skill_demand_weekly, tool_demand_weekly,
geo_demand_weekly, sector_summary_weekly, role_snapshot_weekly, canonical_roles, extracted_intelligence.
Do not use INSERT, UPDATE, DELETE, DDL, or multiple statements."""


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}\s*$", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def generate_sql(question: str, correlation_id: str | None) -> tuple[str, float, str]:
    """Return (sql, cost_usd_so_far, raw_model_content_or_error)."""
    cid = correlation_id or str(uuid.uuid4())
    prompt = (
        "User question:\n"
        f"{question}\n\n"
        "Respond with JSON: {\"sql\": \"SELECT ...\"}"
    )
    out = complete(
        prompt,
        agent_name="analytics_query_router",
        system=_ROUTER_SYSTEM,
        max_tokens=800,
        correlation_id=cid,
        role="analytics",
    )
    cost = float(out.get("cost_usd") or 0.0)
    if out.get("extraction_failed"):
        log.warning("router_llm_failed", correlation_id=cid)
        return "", cost, out.get("error_reason") or "router_failed"

    content = (out.get("content") or "").strip()
    data = _extract_json_object(content)
    if not data or "sql" not in data:
        log.warning("router_json_parse_failed", correlation_id=cid)
        return "", cost, "invalid_router_json"

    sql = str(data["sql"]).strip()
    return sql, cost, content
