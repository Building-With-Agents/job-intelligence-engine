"""Unit tests for eval/qa_scoring.py."""

from __future__ import annotations

from eval.qa_scoring import (
    QAItemScores,
    composite_score,
    compute_item_scores,
    confusion_rows,
    score_confidence_flags,
    score_evidence_citation,
    score_intent_accuracy,
    score_latency_sla,
)


def test_intent_exact_match() -> None:
    s, _ = score_intent_accuracy(
        expected_intent="geographic",
        classified_intent="geographic",
    )
    assert s == 1.0


def test_intent_related() -> None:
    s, c = score_intent_accuracy(
        expected_intent="geographic",
        classified_intent="comparison",
    )
    assert s == 0.5
    assert "related" in c


def test_intent_mismatch() -> None:
    s, c = score_intent_accuracy(
        expected_intent="geographic",
        classified_intent="curriculum",
    )
    assert s == 0.0
    assert "mismatch" in c


def test_evidence_empty_not_refused() -> None:
    s, c = score_evidence_citation(
        answer="Some answer text here with enough length.",
        evidence=[],
        must_include=[],
        must_not_include=[],
        refused=False,
        sql_execution_error_detail=None,
        pipeline_error=None,
    )
    assert s == 0.0
    assert "no evidence" in c


def test_evidence_pipeline_error_zero() -> None:
    s, _ = score_evidence_citation(
        answer="x",
        evidence=[{"title": "t", "snippet": "n"}],
        must_include=[],
        must_not_include=[],
        refused=False,
        sql_execution_error_detail=None,
        pipeline_error="boom",
    )
    assert s == 0.0


def test_confidence_calibration() -> None:
    s, _ = score_confidence_flags(
        confidence=0.5,
        confidence_flagged_low=True,
        confidence_explanation="Low volume in cohort; interpret with caution.",
        volume_flagged_low=False,
        volume_warning=None,
    )
    assert s >= 0.8


def test_latency_at_sla() -> None:
    s, _ = score_latency_sla(latency_seconds=10.0, sla_seconds=45.0)
    assert s == 1.0


def test_latency_above_sla() -> None:
    s, _ = score_latency_sla(latency_seconds=90.0, sla_seconds=45.0)
    assert abs(s - 0.5) < 1e-6


def test_compute_failure_all_zeros_except_latency() -> None:
    g = {"id": "gq-001", "intent": "geographic", "must_include": [], "must_not_include": []}
    out = compute_item_scores(
        golden=g,
        response=None,
        latency_seconds=5.0,
        pipeline_error="connection reset",
        sla_seconds=45.0,
    )
    assert out.intent_accuracy == 0.0
    assert out.evidence_citation == 0.0
    assert out.confidence_flags == 0.0
    assert out.latency_sla > 0.0
    assert out.answerability == 0.0


def test_pipeline_error_skips_answerability_for_intent_only() -> None:
    g = {"id": "gq-001b", "intent": "trend", "must_include": [], "must_not_include": []}
    out = compute_item_scores(
        golden=g,
        response=None,
        latency_seconds=5.0,
        pipeline_error="connection reset",
        sla_seconds=45.0,
    )
    assert out.answerability is None


def test_compute_happy_path() -> None:
    g = {
        "id": "gq-002",
        "intent": "geographic",
        "must_include": ["el_paso_subregion_filter_confirmed"],
        "must_not_include": [],
    }
    resp = {
        "answer": "El Paso subregion shows posting counts for the agentic era period.",
        "evidence": [{"title": "x", "source": "dbo.job_postings", "snippet": "el paso borderplex subregion"}],
        "confidence": 0.75,
        "confidence_flagged_low": False,
        "confidence_explanation": None,
        "volume_flagged_low": True,
        "volume_warning": "Primary posting count is below 30; treat as directional.",
        "refused": False,
        "sql_execution_error_detail": None,
        "classified_intent": "geographic",
        "row_count_returned": 3,
    }
    out = compute_item_scores(
        golden=g,
        response=resp,
        latency_seconds=12.0,
        pipeline_error=None,
        sla_seconds=45.0,
    )
    assert out.intent_accuracy == 1.0
    assert out.evidence_citation > 0.0
    assert out.confidence_flags > 0.0
    assert out.latency_sla == 1.0
    assert out.answerability == 1.0


def test_answerability_data_backed_zero_rows() -> None:
    g = {
        "id": "gq-00z",
        "intent": "employer",
        "must_include": [],
        "must_not_include": [],
    }
    resp = {
        "answer": "No employers matched.",
        "evidence": [],
        "confidence": 0.2,
        "confidence_flagged_low": True,
        "confidence_explanation": "x",
        "volume_flagged_low": False,
        "volume_warning": None,
        "refused": False,
        "sql_execution_error_detail": None,
        "classified_intent": "employer",
        "row_count_returned": 0,
    }
    out = compute_item_scores(
        golden=g,
        response=resp,
        latency_seconds=1.0,
        pipeline_error=None,
        sla_seconds=45.0,
    )
    assert out.answerability == 0.0


def test_answerability_intent_only_skipped() -> None:
    g = {
        "id": "gq-tr",
        "intent": "trend",
        "must_include": [],
        "must_not_include": [],
    }
    resp = {
        "answer": "Narrative only",
        "evidence": [],
        "confidence": 0.5,
        "confidence_flagged_low": False,
        "confidence_explanation": None,
        "volume_flagged_low": False,
        "volume_warning": None,
        "refused": True,
        "refusal_message": "x",
        "sql_execution_error_detail": None,
        "classified_intent": "trend",
        "row_count_returned": 0,
    }
    out = compute_item_scores(
        golden=g,
        response=resp,
        latency_seconds=1.0,
        pipeline_error=None,
        sla_seconds=45.0,
    )
    assert out.answerability is None
    assert "skipped" in out.comments.get("answerability", "").lower()


def test_composite_excludes_answerability() -> None:
    base = QAItemScores(
        intent_accuracy=0.5,
        evidence_citation=0.5,
        confidence_flags=0.5,
        latency_sla=0.5,
        answerability=0.0,
        comments={},
    )
    assert composite_score(base) == 0.5
    hi = QAItemScores(
        intent_accuracy=0.0,
        evidence_citation=0.0,
        confidence_flags=0.0,
        latency_sla=0.0,
        answerability=1.0,
        comments={},
    )
    assert composite_score(hi) == 0.0


def test_confusion_rows() -> None:
    c = confusion_rows(
        [
            ("geographic", "geographic"),
            ("geographic", "comparison"),
            ("geographic", "geographic"),
        ]
    )
    assert c[("geographic", "geographic")] == 2
    assert c[("geographic", "comparison")] == 1
