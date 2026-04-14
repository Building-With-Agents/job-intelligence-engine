"""Thin orchestration: router payload → evidence bundle → synthesis.

GitHub #117 — Ask the Data. HTTP/Streamlit wiring stays out of this module.
"""

from __future__ import annotations

from analytics.query_engine.evidence import build_evidence_bundle
from analytics.query_engine.schemas import CostLedger, QueryResultPayload, SynthesisResponse
from analytics.query_engine.synthesis import synthesize_answer


def run_analytics_qna(
    query_result: QueryResultPayload,
    *,
    cost_ledger: CostLedger | None = None,
) -> SynthesisResponse:
    """Run evidence construction then grounded synthesis.

    Ownership:
        - **Routing / SQL guardrails** produce and validate ``QueryResultPayload``.
        - **Evidence** (``build_evidence_bundle``) turns that payload into an
          ``EvidenceBundle`` (citations, sufficiency, refusal policy, etc.).
        - **Synthesis** (``synthesize_answer``) produces prose, follow-ups, and
          cost fields on ``SynthesisResponse``.

    Downstream HTTP or Streamlit layers should call this (or the pieces above)
    and map ``SynthesisResponse`` to API responses—that wiring is outside
    this PR unless already trivial elsewhere.

    References: GitHub #117, ``.cursor/rules/analytics-qna-synthesis.mdc``.
    """
    bundle = build_evidence_bundle(query_result)
    return synthesize_answer(
        bundle,
        user_query=query_result.request.query,
        intent_label=query_result.intent_label,
        cost_ledger=cost_ledger,
    )
