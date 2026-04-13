"""Truth layer: structured evidence from router output."""

from __future__ import annotations

from analytics.query_engine.schemas import EvidenceBundle, QueryResultPayload


def build_evidence_bundle(payload: QueryResultPayload) -> EvidenceBundle:
    """Derive citations, sufficiency, blended confidence, and refusal flags.

    Run after **`QueryResultPayload`** is available. Do not call the synthesis LLM here.
    """
    raise NotImplementedError(
        "build_evidence_bundle: implement citation extraction, volume/confidence policy, and refusal."
    )
