"""JIE #223 — durable multi-turn Q&A for LaborPulse (Postgres, tenant-scoped)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.data_store.models import LaborPulseAnalyticsConversation, LaborPulseAnalyticsTurn

log = structlog.get_logger()

_MAX_TURNS_IN_PROMPT = 5
_MAX_ANSWER_CHARS = 1200
_MAX_CONTEXT_CHARS = 8000


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def load_prior_context_for_llm(
    session: Session,
    *,
    conversation_id: str,
    tenant_id: str,
    user_email: str,
) -> str:
    """Build a compact string of prior Q/A for intent + synthesis, or empty if none / mismatch."""
    try:
        cid = uuid.UUID((conversation_id or "").strip())
    except ValueError:
        return ""
    tid = (tenant_id or "").strip()
    em = _norm_email(user_email)
    if not tid or not em:
        return ""
    row = session.execute(
        select(LaborPulseAnalyticsConversation).where(
            LaborPulseAnalyticsConversation.id == cid,
            LaborPulseAnalyticsConversation.tenant_id == tid,
            LaborPulseAnalyticsConversation.user_email == em,
        )
    ).scalar_one_or_none()
    if row is None:
        return ""
    turns = (
        session.execute(
            select(LaborPulseAnalyticsTurn)
            .where(LaborPulseAnalyticsTurn.conversation_id == cid)
            .order_by(LaborPulseAnalyticsTurn.turn_index.asc())
        )
        .scalars()
        .all()
    )
    if not turns:
        return ""
    parts: list[str] = []
    for t in turns[-_MAX_TURNS_IN_PROMPT:]:
        a = t.answer
        if len(a) > _MAX_ANSWER_CHARS:
            a = a[: _MAX_ANSWER_CHARS - 1] + "…"
        n = t.turn_index + 1
        parts.append(
            f"Turn {n} — prior question: {t.question}\n"
            f"Turn {n} — prior answer (may reference regions/employers; use for follow-ups like “same area” / “it”): {a}"
        )
    out = "\n\n".join(parts)
    if len(out) > _MAX_CONTEXT_CHARS:
        out = out[: _MAX_CONTEXT_CHARS - 1] + "…"
    return out


def append_conversation_turn(
    session: Session,
    *,
    conversation_id: str,
    tenant_id: str,
    user_email: str,
    question: str,
    answer: str,
    intent_label: str,
) -> None:
    """Append one successful Q&A turn; enforce tenant+email on existing rows; create header row on first turn."""
    try:
        cid = uuid.UUID((conversation_id or "").strip())
    except ValueError:
        return
    tid = (tenant_id or "").strip()
    em = _norm_email(user_email)
    if not tid or not em:
        return
    q = (question or "").strip()
    a = (answer or "").strip()
    if not q or not a:
        return
    ent = (intent_label or "other").strip() or "other"

    existing = session.get(LaborPulseAnalyticsConversation, cid)
    if existing is not None:
        if existing.tenant_id != tid or existing.user_email != em:
            log.warning(
                "laborpulse_conversation_tenant_mismatch",
                conversation_id=str(cid),
            )
            return
        existing.updated_at = datetime.now(timezone.utc)
    else:
        session.add(
            LaborPulseAnalyticsConversation(
                id=cid,
                tenant_id=tid,
                user_email=em,
            )
        )

    max_ix = session.execute(
        select(func.max(LaborPulseAnalyticsTurn.turn_index)).where(
            LaborPulseAnalyticsTurn.conversation_id == cid
        )
    ).scalar()
    next_ix = int(max_ix) + 1 if max_ix is not None else 0
    session.add(
        LaborPulseAnalyticsTurn(
            conversation_id=cid,
            turn_index=next_ix,
            question=q,
            answer=a,
            intent_label=ent,
        )
    )
    log.info(
        "laborpulse_conversation_turn_saved",
        conversation_id=str(cid),
        turn_index=next_ix,
    )
