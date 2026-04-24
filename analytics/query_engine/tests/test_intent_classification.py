"""Unit tests for analytics/query_engine/intent.py — geographic tie-breaker (JIE #257).

Regression: v1-baseline showed 10/10 geographic questions (gq-041–050) classified as
"employer" because the original prompt lacked an explicit city-primacy rule.  The fix
adds a GEOGRAPHIC vs EMPLOYER TIE-BREAKER section and five few-shot examples.

Coverage:
- _SYSTEM_PROMPT structural guards — tie-breaker text and few-shot examples present.
- IntentClassification validator — geographic aliases normalise correctly.
- classify_workforce_question — JSON parsing and entity extraction plumbing.
- Parametric regression — all 10 failing geographic questions return "geographic" when
  the LLM response is correctly formed (mocked to isolate from real API).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from analytics.query_engine.intent import (
    _SYSTEM_PROMPT,
    IntentClassification,
    classify_workforce_question,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_complete_result(
    intent: str, confidence: float = 0.9, geo_terms: list[str] | None = None, role_names: list[str] | None = None
) -> dict:
    """Build a mock ``complete()`` return value emitting the given intent."""
    return {
        "success": True,
        "extraction_failed": False,
        "content": json.dumps(
            {
                "intent": intent,
                "confidence": confidence,
                "extracted_entities": {
                    "geographic_terms": geo_terms or [],
                    "role_names": role_names or [],
                    "skill_names": [],
                    "time_references": [],
                },
            }
        ),
    }


# ---------------------------------------------------------------------------
# Prompt content guards (structural — no LLM call)
# ---------------------------------------------------------------------------


def test_system_prompt_contains_tiebreaker_section() -> None:
    """TIE-BREAKER section must be explicit in the prompt."""
    assert "TIE-BREAKER" in _SYSTEM_PROMPT


def test_system_prompt_geographic_describes_primary_location_axis() -> None:
    """geographic definition must call out 'PRIMARY axis is a LOCATION'."""
    assert "PRIMARY axis is a LOCATION" in _SYSTEM_PROMPT


def test_system_prompt_geographic_rule_covers_secondary_qualifiers() -> None:
    """Prompt must state that domain keywords do NOT override a city/region."""
    assert "domain keywords" in _SYSTEM_PROMPT
    assert "secondary qualifiers" in _SYSTEM_PROMPT


def test_system_prompt_employer_clarified_no_geo_override() -> None:
    """employer definition must explicitly say it applies only without a geographic primary filter."""
    assert "NO geographic location is used as the primary filter" in _SYSTEM_PROMPT


def test_system_prompt_contains_anchoring_examples() -> None:
    """Few-shot examples must include the gq-041 anchor (El Paso + AI agent developer)."""
    assert "El Paso" in _SYSTEM_PROMPT
    assert "AI agent developer" in _SYSTEM_PROMPT


def test_system_prompt_contains_pure_employer_counterexample() -> None:
    """Prompt must include a pure-employer counter-example (Dell)."""
    assert "Dell" in _SYSTEM_PROMPT
    assert '"employer"' in _SYSTEM_PROMPT


def test_system_prompt_contains_comparison_counterexample() -> None:
    """Prompt must include a comparison counter-example to anchor the boundary."""
    assert "comparison" in _SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# IntentClassification validator
# ---------------------------------------------------------------------------


def test_intent_classification_validates_geographic() -> None:
    ic = IntentClassification.model_validate({"intent": "geographic", "confidence": 0.9})
    assert ic.intent == "geographic"
    assert ic.confidence == pytest.approx(0.9)


def test_intent_classification_normalizes_geo_alias() -> None:
    """'geo' alias maps to 'geographic'."""
    ic = IntentClassification.model_validate({"intent": "geo", "confidence": 0.8})
    assert ic.intent == "geographic"


def test_intent_classification_normalizes_location_alias() -> None:
    """'location' alias maps to 'geographic'."""
    ic = IntentClassification.model_validate({"intent": "location", "confidence": 0.7})
    assert ic.intent == "geographic"


def test_intent_classification_normalizes_regional_alias() -> None:
    """'regional' alias maps to 'geographic'."""
    ic = IntentClassification.model_validate({"intent": "regional", "confidence": 0.75})
    assert ic.intent == "geographic"


def test_intent_classification_unknown_maps_to_other() -> None:
    ic = IntentClassification.model_validate({"intent": "nonsense", "confidence": 0.3})
    assert ic.intent == "other"


# ---------------------------------------------------------------------------
# classify_workforce_question — plumbing (mocked LLM)
# ---------------------------------------------------------------------------


def test_classify_returns_geographic_for_geographic_llm_response() -> None:
    """Parser correctly extracts geographic intent from LLM JSON."""
    with patch(
        "analytics.query_engine.intent.complete",
        return_value=_make_complete_result("geographic", 0.95, geo_terms=["El Paso"]),
    ):
        result = classify_workforce_question(
            "Show all El Paso postings for AI agent developer roles.",
            correlation_id="test-gq-041",
        )
    assert result["intent"] == "geographic"
    assert result["confidence"] == pytest.approx(0.95)
    assert not result["needs_clarification"]


def test_classify_populates_geographic_entities() -> None:
    """geographic_terms and role_names are propagated from LLM response."""
    with patch(
        "analytics.query_engine.intent.complete",
        return_value=_make_complete_result(
            "geographic",
            0.92,
            geo_terms=["El Paso", "TX"],
            role_names=["prompt engineer"],
        ),
    ):
        result = classify_workforce_question(
            "Show all El Paso, TX postings for prompt engineer roles.",
            correlation_id="test-gq-041b",
        )
    entities = result["extracted_entities"]
    assert "El Paso" in entities["geographic_terms"]
    assert "TX" in entities["geographic_terms"]
    assert "prompt engineer" in entities["role_names"]


def test_classify_pure_employer_returns_employer() -> None:
    """Pure employer question (no geographic constraint) returns employer."""
    with patch(
        "analytics.query_engine.intent.complete",
        return_value=_make_complete_result("employer", 0.93),
    ):
        result = classify_workforce_question(
            "What is Dell's hiring strategy for data scientists?",
            correlation_id="test-employer-only",
        )
    assert result["intent"] == "employer"


def test_classify_empty_question_returns_other() -> None:
    """Empty question short-circuits to other/0.0 without calling LLM."""
    with patch("analytics.query_engine.intent.complete") as mock_c:
        result = classify_workforce_question("", correlation_id="test-empty")
    assert result["intent"] == "other"
    assert result["confidence"] == pytest.approx(0.0)
    mock_c.assert_not_called()


def test_classify_llm_exception_returns_other() -> None:
    """LLM exception is caught and returns other/0.0."""
    with patch(
        "analytics.query_engine.intent.complete",
        side_effect=RuntimeError("connection refused"),
    ):
        result = classify_workforce_question("Any question", correlation_id="test-exc")
    assert result["intent"] == "other"


def test_classify_invalid_json_returns_other() -> None:
    """Malformed LLM JSON returns other/0.0 gracefully."""
    with patch(
        "analytics.query_engine.intent.complete",
        return_value={"success": True, "extraction_failed": False, "content": "not json {{"},
    ):
        result = classify_workforce_question("Any question", correlation_id="test-badjson")
    assert result["intent"] == "other"


def test_classify_low_confidence_sets_needs_clarification() -> None:
    """Confidence below threshold sets needs_clarification=True."""
    with patch(
        "analytics.query_engine.intent.complete",
        return_value=_make_complete_result("geographic", 0.45),
    ):
        result = classify_workforce_question("Where are jobs?", correlation_id="test-low-conf")
    assert result["needs_clarification"] is True


# ---------------------------------------------------------------------------
# Regression: all 10 gq-041–050 geographic questions (mocked LLM)
#
# These verify the full plumbing — when the improved prompt causes the LLM to
# return "geographic", the classifier parses and forwards it correctly.
# The smoke script (scripts/smoke/qa_geographic_intent_smoke.py) verifies the
# actual LLM behaviour against the live API.
# ---------------------------------------------------------------------------

_GEO_QUESTIONS = [
    (
        "gq-041",
        "Show all El Paso, TX postings for AI agent developer, prompt engineer, or LLM engineer roles in the agentic_era period.",
    ),
    (
        "gq-042",
        "List every Las Cruces, NM data engineer posting from the last 90 days with required skills including Python, SQL, and a cloud platform.",
    ),
    (
        "gq-043",
        "Pull all El Paso, TX healthcare-IT postings — including EHR analyst, health informatics specialist, and clinical data analyst roles — that require AI or ML skills.",
    ),
    (
        "gq-044",
        "Show all Las Cruces, NM DevOps and site-reliability engineer postings requiring Kubernetes or Terraform experience.",
    ),
    (
        "gq-045",
        "Find all El Paso, TX frontend developer postings mentioning React or Next.js, posted in the post_gpt4 or agentic_era periods.",
    ),
    (
        "gq-046",
        "List every Las Cruces, NM cybersecurity posting from the last 12 months requiring a security clearance or a named industry certification such as CISSP, CISA, or CompTIA Security+.",
    ),
    (
        "gq-047",
        "Retrieve all El Paso, TX entry-level IT postings that have a published salary range, grouped by job family.",
    ),
    (
        "gq-048",
        "Find all Borderplex (El Paso and Las Cruces combined) fintech or regtech developer postings from the agentic_era period.",
    ),
    (
        "gq-049",
        "Show all El Paso, TX legal-tech and e-discovery analyst postings from the past 6 months, with any that mention AI workflows flagged.",
    ),
    (
        "gq-050",
        "List all Las Cruces, NM AI/ML researcher and applied-scientist postings, highlighting any university-affiliated employers such as NMSU, UTEP, or EPCC.",
    ),
]


@pytest.mark.parametrize("gq_id,question", _GEO_QUESTIONS, ids=[q[0] for q in _GEO_QUESTIONS])
def test_geographic_plumbing_returns_geographic(gq_id: str, question: str) -> None:
    """Classifier returns geographic when LLM emits geographic JSON (full pipeline smoke)."""
    # Check Borderplex first — gq-048 contains "El Paso" in a parenthetical but its
    # primary region token is "Borderplex", so priority order matters here.
    geo_term = "Borderplex" if "Borderplex" in question else "El Paso" if "El Paso" in question else "Las Cruces"
    with patch(
        "analytics.query_engine.intent.complete",
        return_value=_make_complete_result("geographic", 0.9, geo_terms=[geo_term]),
    ):
        result = classify_workforce_question(question, correlation_id=gq_id)
    assert result["intent"] == "geographic", f"{gq_id}: expected geographic, got {result['intent']!r} — check plumbing"
    geo_terms = result["extracted_entities"]["geographic_terms"]
    assert geo_terms, f"{gq_id}: geographic_terms should be non-empty"
    assert geo_term in geo_terms, f"{gq_id}: expected primary geo_term {geo_term!r} in {geo_terms!r}"
