"""JIE #225 — map internal Q&A payload to LaborPulse / wfd-os ``QueryResponse`` JSON.

**Confidence:** ``LABORPULSE_CONF_LOW_BELOW`` (default 0.60) and
``LABORPULSE_CONF_HIGH_AT_OR_ABOVE`` (default 0.85) bucket the float from
``run_analytics_qna`` / synthesis into ``low`` | ``medium`` | ``high``.

**sql_generated:** taken from ``AnalyticsQueryResponse.sql_generated``, which is
filled from ``_sql_generated_line`` / router execution metadata in
``analytics/query_engine/routing.py`` (ORM path surfaces tables + label).

**cost_usd:** prefers ``total_cost_usd`` from the synthesis ledger when larger
than ``cost_usd`` (both originate from the same Langfuse-aware cost path in
``routing._synthesis_to_api``).

**conversation_id:** valid client UUID is echoed; otherwise a new UUID is issued
per request until multi-turn persistence (#223) is wired.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Literal

from analytics.api.schemas import (
    AnalyticsQueryResponse,
    LaborPulseEvidenceItem,
    LaborPulseQueryResponse,
)

_TOO_BROAD = re.compile(
    r"\b(everything|all data|all rows|dump the database|show me all)\b",
    re.I,
)


def validate_laborpulse_question(question: str) -> None:
    """Raise ``ValueError`` with stable codes for HTTP 400 mapping (JIE #222)."""
    q = (question or "").strip()
    if not q:
        raise ValueError("empty_question")
    if len(q) < 3:
        raise ValueError("question_too_short")
    if _TOO_BROAD.search(q):
        raise ValueError("question_too_broad")


def resolve_laborpulse_conversation_id(raw: str | None) -> str:
    s = (raw or "").strip()
    if s:
        try:
            return str(uuid.UUID(s))
        except ValueError as exc:
            raise ValueError("invalid_conversation_id") from exc
    return str(uuid.uuid4())


def confidence_bucket(score: float) -> Literal["low", "medium", "high"]:
    low = float(os.getenv("LABORPULSE_CONF_LOW_BELOW", "0.60"))
    high = float(os.getenv("LABORPULSE_CONF_HIGH_AT_OR_ABOVE", "0.85"))
    if score < low:
        return "low"
    if score < high:
        return "medium"
    return "high"


def _pad_followups(items: list[str]) -> list[str]:
    generics = (
        "Which time window should we focus on (last 30 or 90 days)?",
        "Should we narrow this to a specific geography or role family?",
    )
    out = [x.strip() for x in items if isinstance(x, str) and x.strip()]
    out = out[:4]
    i = 0
    while len(out) < 2:
        out.append(generics[i % len(generics)])
        i += 1
    return out[:4]


def to_laborpulse_query_response(
    internal: AnalyticsQueryResponse,
    *,
    conversation_id: str,
) -> LaborPulseQueryResponse:
    ev = [
        LaborPulseEvidenceItem(
            title=e.title,
            source=e.source,
            snippet=e.snippet,
            supporting_count=e.supporting_count,
            time_period=e.time_period,
        )
        for e in (internal.evidence or [])
    ]
    cost = float(internal.cost_usd)
    total = float(internal.total_cost_usd or 0.0)
    if total > cost:
        cost = total
    return LaborPulseQueryResponse(
        conversation_id=conversation_id,
        answer=internal.answer,
        evidence=ev,
        confidence=confidence_bucket(float(internal.confidence)),
        follow_up_questions=_pad_followups(list(internal.follow_up_questions or [])),
        cost_usd=cost,
        sql_generated=internal.sql_generated or "",
    )
