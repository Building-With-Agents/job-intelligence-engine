"""Unit tests for analytics.query_engine.intent (Workforce Q&A intent classification).

LLM calls are mocked — no real API or ``llm_audit_log`` writes. Intent tests are
purely functional (parse/validate/output shape); they do not require database
fixtures or ``ingestion_run_id`` teardown.
"""

from __future__ import annotations

import json
import os
import time
from unittest.mock import patch

import pytest

from analytics.query_engine.intent import (
    INTENT_CATEGORIES,
    classify_workforce_question,
)


def _ok_llm_response(payload: dict) -> dict:
    return {
        "content": json.dumps(payload),
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.0,
        "model_tier": "haiku",
        "success": True,
        "extraction_failed": False,
    }


# 20 diverse sample questions × expected primary intent (LLM output simulated per case).
_INTENT_SAMPLES: list[tuple[str, str]] = [
    ("How fast is demand for registered nurses growing this quarter?", "trend"),
    ("Which cloud skills show the strongest week-over-week posting velocity?", "trend"),
    ("How has the software engineer job family changed in the past five years?", "role_evolution"),
    ("Are data analyst titles shifting toward analytics engineer in our postings?", "role_evolution"),
    ("Which occupations face the highest automation risk from generative AI?", "disruption"),
    ("How is AI adoption affecting entry-level administrative roles?", "disruption"),
    ("What entirely new job titles appeared in cybersecurity last year?", "emergence"),
    ("Which novel combinations of skills signal an emerging hybrid role?", "emergence"),
    ("What micro-credentials should a community college add for semiconductor techs?", "curriculum"),
    ("How should we sequence modules for an AI literacy workforce program?", "curriculum"),
    ("Which employers are hiring the most welders in the border region?", "employer"),
    ("Do local hospitals signal stronger demand for LVNs than last year?", "employer"),
    ("What does a typical day look like for a field service technician?", "workflow"),
    ("Which tools dominate daily work for DevOps engineers in our sample?", "workflow"),
    ("How does job volume in El Paso compare to Las Cruces this month?", "geographic"),
    ("Where are remote software jobs concentrated versus on-site roles?", "geographic"),
    ("Compare median salary for Python versus Java developers in our data.", "comparison"),
    ("Is demand for cybersecurity analysts higher than for network admins?", "comparison"),
    ("What is the weather like today?", "other"),
    ("Hello — can you help?", "other"),
]


@pytest.mark.parametrize("question,expected_intent", _INTENT_SAMPLES)
@patch("analytics.query_engine.intent.complete")
def test_classify_covers_all_intent_categories(mock_complete, question, expected_intent):
    payload = {
        "intent": expected_intent,
        "confidence": 0.88,
        "extracted_entities": {
            "geographic_terms": [],
            "role_names": [],
            "skill_names": [],
            "time_references": [],
        },
    }
    mock_complete.return_value = _ok_llm_response(payload)
    out = classify_workforce_question(question)
    assert out["intent"] == expected_intent
    assert out["confidence"] == pytest.approx(0.88)
    assert out["needs_clarification"] is False
    assert set(out["extracted_entities"].keys()) == {
        "geographic_terms",
        "role_names",
        "skill_names",
        "time_references",
    }
    mock_complete.assert_called_once()
    call_kw = mock_complete.call_args.kwargs
    assert call_kw["agent_name"] == "analytics-intent-classification"
    assert call_kw["role"] == "classification"


def test_all_categories_represented_in_samples():
    sample_intents = {intent for _, intent in _INTENT_SAMPLES}
    assert sample_intents == set(INTENT_CATEGORIES)


@patch("analytics.query_engine.intent.complete")
def test_ambiguous_question_low_confidence(mock_complete):
    mock_complete.return_value = _ok_llm_response(
        {
            "intent": "trend",
            "confidence": 0.52,
            "extracted_entities": {
                "geographic_terms": ["Borderplex"],
                "role_names": ["Nurse"],
                "skill_names": [],
                "time_references": ["last year", "next quarter"],
            },
        }
    )
    out = classify_workforce_question("Are nurses trending up or is it just seasonal hiring noise near the border?")
    assert out["intent"] == "trend"
    assert out["confidence"] == pytest.approx(0.52)
    assert out["needs_clarification"] is True
    assert "Borderplex" in out["extracted_entities"]["geographic_terms"]
    assert len(out["extracted_entities"]["time_references"]) == 2


@patch("analytics.query_engine.intent.complete")
def test_multi_topic_question_primary_intent(mock_complete):
    mock_complete.return_value = _ok_llm_response(
        {
            "intent": "comparison",
            "confidence": 0.48,
            "extracted_entities": {
                "geographic_terms": ["Texas", "New Mexico"],
                "role_names": ["Electrician", "Plumber"],
                "skill_names": [],
                "time_references": [],
            },
        }
    )
    out = classify_workforce_question(
        "Compare electrician vs plumber demand in Texas and New Mexico while also mentioning AI disruption"
    )
    assert out["intent"] == "comparison"
    assert out["confidence"] < 0.6
    assert out["needs_clarification"] is True


@patch("analytics.query_engine.intent.complete")
def test_no_clear_intent_maps_to_other(mock_complete):
    mock_complete.return_value = _ok_llm_response({"intent": "other", "confidence": 0.25, "extracted_entities": {}})
    out = classify_workforce_question("asdf qqq ???")
    assert out["intent"] == "other"
    assert out["confidence"] == pytest.approx(0.25)
    assert out["needs_clarification"] is True


@patch("analytics.query_engine.intent.complete")
def test_json_markdown_fence_stripped(mock_complete):
    inner = {
        "intent": "geographic",
        "confidence": 0.9,
        "extracted_entities": {
            "geographic_terms": ["Ciudad Juárez"],
            "role_names": [],
            "skill_names": [],
            "time_references": [],
        },
    }
    mock_complete.return_value = {
        "content": "```json\n" + json.dumps(inner) + "\n```",
        "success": True,
        "extraction_failed": False,
    }
    out = classify_workforce_question("Job density in Ciudad Juárez vs El Paso?")
    assert out["intent"] == "geographic"


@patch("analytics.query_engine.intent.complete")
def test_invalid_json_returns_other(mock_complete):
    mock_complete.return_value = {"content": "not json {", "success": True, "extraction_failed": False}
    out = classify_workforce_question("Any question")
    assert out["intent"] == "other"
    assert out["confidence"] == 0.0
    assert out["needs_clarification"] is True


@patch("analytics.query_engine.intent.complete")
def test_llm_failure_returns_other(mock_complete):
    mock_complete.return_value = {
        "content": "",
        "success": False,
        "extraction_failed": True,
    }
    out = classify_workforce_question("Trend for baristas?")
    assert out["intent"] == "other"
    assert out["confidence"] == 0.0
    assert out["needs_clarification"] is True


@patch("analytics.query_engine.intent.complete")
def test_empty_question_no_llm_call(mock_complete):
    out = classify_workforce_question("   ")
    assert out["intent"] == "other"
    assert out["needs_clarification"] is True
    mock_complete.assert_not_called()


@patch("analytics.query_engine.intent.complete")
def test_unknown_intent_string_normalized_to_other(mock_complete):
    mock_complete.return_value = _ok_llm_response(
        {
            "intent": "salary_only",
            "confidence": 0.9,
            "extracted_entities": {},
        }
    )
    out = classify_workforce_question("What is the pay range?")
    assert out["intent"] == "other"


@patch("analytics.query_engine.intent.complete")
def test_complete_raises_returns_other(mock_complete):
    mock_complete.side_effect = RuntimeError("network")
    out = classify_workforce_question("Any workforce question")
    assert out["intent"] == "other"
    assert out["confidence"] == 0.0
    assert out["needs_clarification"] is True


@patch("analytics.query_engine.intent.complete")
def test_entity_coercion_string_to_list(mock_complete):
    mock_complete.return_value = _ok_llm_response(
        {
            "intent": "workflow",
            "confidence": 0.7,
            "extracted_entities": {
                "geographic_terms": "Austin",
                "role_names": ["Mechanic"],
                "skill_names": "hydraulics",
                "time_references": [],
            },
        }
    )
    out = classify_workforce_question("Daily workflow for a mechanic?")
    assert out["extracted_entities"]["geographic_terms"] == ["Austin"]
    assert out["extracted_entities"]["skill_names"] == ["hydraulics"]


@patch("analytics.query_engine.intent.complete")
def test_confidence_threshold_boundary(mock_complete):
    mock_complete.return_value = _ok_llm_response(
        {
            "intent": "trend",
            "confidence": 0.55,
            "extracted_entities": {
                "geographic_terms": [],
                "role_names": [],
                "skill_names": ["python"],
                "time_references": ["last year"],
            },
        }
    )
    out = classify_workforce_question("Is Python demand up this year?")
    assert out["intent"] == "trend"
    assert out["needs_clarification"] is False


# ---------------------------------------------------------------------------
# GQ-041..050 geographic gold-question regression guard (JIE #305)
# ---------------------------------------------------------------------------

# Exact question texts from eval/qa_golden_questions.json — gq-041 through gq-050.
# If any of these regress to "other" in a live run the per-intent floor test below
# will catch it before it silently pollutes the next scorecard.
_GQ_GEOGRAPHIC_GOLD: list[tuple[str, str]] = [
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
        "Pull all El Paso, TX healthcare-IT postings — including EHR analyst, health informatics specialist, and clinical data analyst roles.",
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
        "List every Las Cruces, NM cybersecurity posting from the last 12 months requiring a security clearance or a named industry certification.",
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
        "Show all El Paso, TX legal-tech and e-discovery analyst postings from the past 6 months, with any that mention AI workflows.",
    ),
    (
        "gq-050",
        "List all Las Cruces, NM AI/ML researcher and applied-scientist postings, highlighting any university-affiliated employers such as NMSU or UTEP.",
    ),
]


@pytest.mark.parametrize("gq_id,question", _GQ_GEOGRAPHIC_GOLD)
@patch("analytics.query_engine.intent.complete")
def test_geographic_gold_questions_parse_correctly(mock_complete, gq_id, question):
    """Regression guard (JIE #305): all 10 geographic gold questions must parse
    to 'geographic' when the LLM returns the expected JSON.  Tests the parsing
    and validation layers — not the LLM itself.
    """
    payload = {
        "intent": "geographic",
        "confidence": 0.95,
        "extracted_entities": {
            "geographic_terms": ["El Paso, TX"],
            "role_names": [],
            "skill_names": [],
            "time_references": [],
        },
    }
    mock_complete.return_value = _ok_llm_response(payload)
    out = classify_workforce_question(question)
    assert out["intent"] == "geographic", (
        f"{gq_id}: expected 'geographic', got {out['intent']!r}. "
        "Check _normalize_intent aliases and _INTENT_SET membership."
    )
    assert out["confidence"] >= 0.5, f"{gq_id}: confidence too low: {out['confidence']}"
    assert out["needs_clarification"] is False, f"{gq_id}: unexpected clarification flag"


def test_geographic_intent_in_prompt_has_borderplex_rule():
    """Smoke-test that the system prompt still contains the geographic tie-breaker rule.

    If someone refactors _SYSTEM_PROMPT and accidentally removes the GEOGRAPHIC vs
    EMPLOYER disambiguation, this test fails fast — before a scorecard run captures it.
    """
    from analytics.query_engine.intent import _SYSTEM_PROMPT

    required_phrases = [
        "geographic",
        "POSTINGS",
        "EMPLOYERS",
        "Borderplex",
        "El Paso",
        "Las Cruces",
    ]
    for phrase in required_phrases:
        assert phrase in _SYSTEM_PROMPT, (
            f"_SYSTEM_PROMPT is missing required phrase {phrase!r}. "
            "The geographic-primacy tie-breaker rule may have been removed."
        )


@pytest.mark.live_llm
def test_live_geographic_gold_per_intent_floor():
    """CI-gated live floor: per-intent accuracy on gq-041..050 must be ≥ 0.8.

    Run with: pytest -m live_llm analytics/tests/test_intent_classification.py

    Per JIE #305: geographic regressions must be caught here (at PR time) not at
    the next scorecard.  Floor of 0.8 allows for 2 stochastic misses out of 10
    before the gate trips.
    """
    correct = 0
    misses: list[str] = []
    for gq_id, question in _GQ_GEOGRAPHIC_GOLD:
        out = classify_workforce_question(question)
        if out["intent"] == "geographic":
            correct += 1
        else:
            misses.append(f"{gq_id}: got {out['intent']!r} (conf={out['confidence']:.2f})")

    accuracy = correct / len(_GQ_GEOGRAPHIC_GOLD)
    floor = float(os.getenv("GEOGRAPHIC_INTENT_MIN_ACCURACY", "0.8"))
    assert accuracy >= floor, (
        f"Geographic intent accuracy {accuracy:.2f} < floor {floor:.2f}. Misses ({len(misses)}): {misses}"
    )


@pytest.mark.live_llm
def test_live_intent_classification_mini_benchmark():
    # Optional: run with --live and configured LLM credentials.
    samples: list[tuple[str, str]] = _INTENT_SAMPLES
    start = time.perf_counter()
    correct = 0
    for question, expected_intent in samples:
        out = classify_workforce_question(question)
        if out["intent"] == expected_intent:
            correct += 1
    elapsed_ms = (time.perf_counter() - start) * 1000
    accuracy = correct / len(samples)
    per_call_ms = elapsed_ms / len(samples)

    # Keep targets realistic for CI-adjacent local runs; strict enough for Week 8 signal.
    assert accuracy >= float(os.getenv("WEEK8_INTENT_MIN_ACCURACY", "0.7"))
    assert per_call_ms <= float(os.getenv("WEEK8_INTENT_MAX_LATENCY_MS", "5000"))
