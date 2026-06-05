"""Unit tests for analytics.query_engine.synthesis (prompt construction, JIE #361)."""

from __future__ import annotations

from analytics.query_engine.schemas import DataSufficiency, EvidenceBundle, EvidenceCitation
from analytics.query_engine.synthesis import _build_main_prompt


def test_comparison_prompt_requires_total_supporting_counts_across_facts() -> None:
    """JIE #361 — comparison synthesis must sum supporting_count per term before concluding.

    Facts: Python 38 + 6 = 44 vs ML 49 + 4 = 53 (ML higher overall). The main prompt must
    instruct the model to total supporting_count first; a draft claiming Python is higher
    would contradict item 3 (conclusion must match totals).
    """
    bundle = EvidenceBundle(
        facts=[
            EvidenceCitation(
                citation_id="py_w1",
                summary="Python posting_count 38 in week 1.",
                source_table="skill_demand_weekly",
                supporting_count=38,
                time_period="2025-W01",
            ),
            EvidenceCitation(
                citation_id="py_w2",
                summary="Python posting_count 6 in week 2.",
                source_table="skill_demand_weekly",
                supporting_count=6,
                time_period="2025-W02",
            ),
            EvidenceCitation(
                citation_id="ml_w1",
                summary="ML posting_count 49 in week 1 (single-week peak).",
                source_table="skill_demand_weekly",
                supporting_count=49,
                time_period="2025-W01",
            ),
            EvidenceCitation(
                citation_id="ml_w2",
                summary="ML posting_count 4 in week 2.",
                source_table="skill_demand_weekly",
                supporting_count=4,
                time_period="2025-W02",
            ),
        ],
        period_coverage="2025-W01 to 2025-W02",
        volume_posting_count=97,
        sufficiency=DataSufficiency.ADEQUATE,
        blended_confidence=0.82,
        confidence_explanation="Adequate evidence for comparison.",
        refuse_synthesis=False,
    )
    prompt = _build_main_prompt(
        "How does Python demand compare to ML?",
        "comparison",
        bundle,
    )
    assert "total supporting_count" in prompt
    assert "COMPARISON INSTRUCTIONS" in prompt
    assert "MUST match the totals" in prompt
    assert "Never conclude Term A is higher" in prompt
    assert "higher total" in prompt
    assert "magnitude of the difference" in prompt
    assert "never confidence: high" in prompt.lower()
    # Facts sum to Python 44 vs ML 53; claiming "Python is higher overall" would violate
    # the clause requiring the directional conclusion to match summed supporting_count.
