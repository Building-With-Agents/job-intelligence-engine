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


def test_system_prompt_geographic_describes_postings_as_subject() -> None:
    """geographic definition must frame postings as grammatical subject filtered by location."""
    assert "POSTINGS are the grammatical subject" in _SYSTEM_PROMPT


def test_system_prompt_geographic_rule_covers_secondary_qualifiers() -> None:
    """Prompt must state that domain keywords do NOT override a city/region."""
    assert "domain keywords" in _SYSTEM_PROMPT
    assert "secondary qualifiers" in _SYSTEM_PROMPT


def test_system_prompt_employer_clarified_employers_are_subject() -> None:
    """employer definition must frame employers as grammatical subject, not geographic filter."""
    assert "grammatical subject" in _SYSTEM_PROMPT
    assert "employer" in _SYSTEM_PROMPT.lower()


def test_system_prompt_employer_region_scoping_clause() -> None:
    """employer definition must state that a region scoping employers does not flip to geographic."""
    assert "scopes the employer set" in _SYSTEM_PROMPT or "scopes which employers" in _SYSTEM_PROMPT


def test_system_prompt_tiebreaker_asks_postings_or_employers() -> None:
    """TIE-BREAKER must frame the key diagnostic as postings-subject vs employers-subject."""
    assert "POSTINGS" in _SYSTEM_PROMPT
    assert "EMPLOYERS" in _SYSTEM_PROMPT


def test_system_prompt_contains_anchoring_examples() -> None:
    """Few-shot examples must include the gq-041 anchor (El Paso + AI agent developer)."""
    assert "El Paso" in _SYSTEM_PROMPT
    assert "AI agent developer" in _SYSTEM_PROMPT


def test_system_prompt_geographic_borderplex_example_is_posting_filter() -> None:
    """Borderplex geographic example must show postings-as-subject, not employers-as-subject."""
    assert "Show all cybersecurity postings in the Borderplex" in _SYSTEM_PROMPT


def test_system_prompt_employer_borderplex_example_present() -> None:
    """Prompt must include a Borderplex employer example to anchor the employer-in-region boundary."""
    assert "Which Borderplex employers" in _SYSTEM_PROMPT
    assert '"employer"' in _SYSTEM_PROMPT


def test_system_prompt_contains_pure_employer_counterexample() -> None:
    """Prompt must include a pure-employer counter-example (Dell)."""
    assert "Dell" in _SYSTEM_PROMPT


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


_EMPLOYER_QUESTIONS = [
    (
        "gq-061",
        "Show open AI-core roles (AI agent developer, prompt engineer, ML engineer) at UTEP, NMSU, or EPCC in the last 12 months.",
    ),
    (
        "gq-062",
        "Which Borderplex employers have the highest share of postings mentioning AI tools (Copilot, LangChain, LLM APIs) in the agentic_era period?",
    ),
    (
        "gq-063",
        "List all IT postings from Borderplex federal-contractor employers (defense, aerospace, government-tech) that require a security clearance.",
    ),
    (
        "gq-064",
        "Which Borderplex healthcare-IT employers are hiring for clinical-data-analyst or health-informatics roles with AI-adjacent skill requirements?",
    ),
    (
        "gq-065",
        "Show all Borderplex fintech and payments-technology employer postings from the post_gpt4 and agentic_era periods.",
    ),
    (
        "gq-066",
        "Which Borderplex employers posted the most data-engineer or data-scientist roles in the last 12 months, and what share of their postings require AI-adjacent skills?",
    ),
    (
        "gq-067",
        "Compare hiring activity between Borderplex academic employers (UTEP, NMSU, EPCC) and private-sector IT employers over the last 18 months.",
    ),
    (
        "gq-068",
        "Which Borderplex employers have introduced AI-native roles (AI agent developer, prompt engineer) for the first time in the agentic_era period, signaling a shift in hiring strategy?",
    ),
    (
        "gq-069",
        "Show all Borderplex legal-tech and e-discovery employer postings, grouped by employer, in the last 12 months.",
    ),
    (
        "gq-070",
        "Which Borderplex employers have the highest repeat-posting rate for the same role family — suggesting either high turnover or aggressive expansion?",
    ),
]


@pytest.mark.parametrize("gq_id,question", _EMPLOYER_QUESTIONS, ids=[q[0] for q in _EMPLOYER_QUESTIONS])
def test_employer_plumbing_returns_employer(gq_id: str, question: str) -> None:
    """Classifier returns employer when LLM emits employer JSON (regression for gq-061–070).

    These questions all involve Borderplex-scoped employer analysis. The fixed prompt
    must NOT teach the model to classify 'Which Borderplex employers…' as geographic.
    The old third few-shot example (geographic for that pattern) was the bug.
    """
    with patch(
        "analytics.query_engine.intent.complete",
        return_value=_make_complete_result("employer", 0.91),
    ):
        result = classify_workforce_question(question, correlation_id=gq_id)
    assert result["intent"] == "employer", f"{gq_id}: expected employer, got {result['intent']!r} — check plumbing"


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
