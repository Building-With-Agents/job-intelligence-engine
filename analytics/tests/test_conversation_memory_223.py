"""JIE #223 — LaborPulse multi-turn: load/append and routing wiring (mocked / unit)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from analytics.conversation_memory import append_conversation_turn, load_prior_context_for_llm
from common.data_store.models import LaborPulseAnalyticsConversation


def test_load_prior_empty_on_unknown_conversation() -> None:
    session = MagicMock(spec=Session)
    session.execute = MagicMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
        )
    )
    out = load_prior_context_for_llm(
        session,
        conversation_id=str(uuid.uuid4()),
        tenant_id="t1",
        user_email="a@b.com",
    )
    assert out == ""


def test_append_rejects_tenant_mismatch() -> None:
    cid = uuid.uuid4()
    session = MagicMock(spec=Session)
    existing = LaborPulseAnalyticsConversation(
        id=cid,
        tenant_id="other",
        user_email="a@b.com",
    )
    session.get = MagicMock(return_value=existing)
    append_conversation_turn(
        session,
        conversation_id=str(cid),
        tenant_id="t1",
        user_email="a@b.com",
        question="q",
        answer="a",
        intent_label="trend",
    )
    session.add.assert_not_called()


@patch("analytics.query_engine.routing.audit_log.insert_orchestration_audit")
@patch("analytics.query_engine.routing.load_prior_context_for_llm", return_value="Turn 1 — prior")
@patch("analytics.query_engine.routing.append_conversation_turn")
@patch("analytics.query_engine.routing.qna.run_analytics_qna")
@patch("analytics.query_engine.routing.QueryRouter")
@patch("analytics.query_engine.routing.classify_workforce_question", return_value={"intent": "trend", "confidence": 0.9, "needs_clarification": False, "extracted_entities": {}})
def test_routing_passes_conversation_context_to_classify(
    mock_classify: MagicMock,
    _router_cls: MagicMock,
    _qna_run: MagicMock,
    _append: MagicMock,
    _load_prior: MagicMock,
    _audit: MagicMock,
) -> None:
    from analytics.query_engine.routing import run_analytics_qna
    from analytics.query_engine.schemas import SynthesisResponse

    mock_classify.reset_mock()
    _qna_run.return_value = SynthesisResponse(
        answer_text="ok",
        confidence=0.8,
    )
    route_inst = _router_cls.return_value
    route_inst.route = MagicMock(
        return_value=MagicMock(
            rows=[{"a": 1}],
            is_partial=False,
            tables_used=["t"],
            query_label="L",
            error=None,
            routed=True,
        )
    )

    session = MagicMock(spec=Session)
    run_analytics_qna(
        session,
        "What about the same place?",
        "req-1",
        laborpulse_conversation_id=str(uuid.uuid4()),
        tenant_id="borderplex",
        user_email="p@b.com",
    )
    call_kw = mock_classify.call_args[1]
    assert "Turn 1 — prior" in (call_kw.get("conversation_context") or "")
