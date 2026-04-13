"""Voice layer: natural language answer from `EvidenceBundle`."""

from __future__ import annotations

from analytics.query_engine.schemas import CostLedger, EvidenceBundle, SynthesisResponse


def synthesize_answer(
    bundle: EvidenceBundle,
    *,
    user_query: str,
    intent_label: str,
    cost_ledger: CostLedger | None = None,
) -> SynthesisResponse:
    """Produce grounded answer text, follow-ups, and cost rollups.

    Implementers: use Sonnet-class for main text, Haiku-class for follow-ups per runbook.
    Only paraphrase facts present on ``bundle``; never invent statistics.
    """
    raise NotImplementedError(
        "synthesize_answer: implement LLM calls via common.llm_adapter and populate SynthesisResponse."
    )
