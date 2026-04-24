"""Unit tests for eval/qa_scoring.py."""

from __future__ import annotations

import io
from contextlib import redirect_stdout

from eval.qa_eval import print_console_summary
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


def test_intent_related_counts_as_mismatch() -> None:
    """JIE #261: no 0.5 partial credit; use confusion_rows for related-intent analysis."""
    s, c = score_intent_accuracy(
        expected_intent="geographic",
        classified_intent="comparison",
    )
    assert s == 0.0
    assert "mismatch" in c


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


def test_evidence_pipeline_error_excluded() -> None:
    """pipeline_error → None (excluded), not 0.0 (JIE #263)."""
    s, c = score_evidence_citation(
        answer="x",
        evidence=[{"title": "t", "snippet": "n"}],
        must_include=[],
        must_not_include=[],
        refused=False,
        sql_execution_error_detail=None,
        pipeline_error="boom",
    )
    assert s is None
    assert "excluded" in c


def test_evidence_sql_error_excluded() -> None:
    """sql_execution_error_detail → None (infrastructure failure, JIE #263)."""
    s, c = score_evidence_citation(
        answer="El Paso employers showed growth.",
        evidence=[{"title": "t", "source": "s", "snippet": "el paso"}],
        must_include=[],
        must_not_include=[],
        refused=False,
        sql_execution_error_detail="column foo does not exist",
        pipeline_error=None,
    )
    assert s is None
    assert "excluded" in c


def test_evidence_empty_answer_excluded() -> None:
    """Empty answer is an input-contract violation → None (JIE #263)."""
    s, c = score_evidence_citation(
        answer="",
        evidence=[{"title": "t", "snippet": "n"}],
        must_include=[],
        must_not_include=[],
        refused=False,
        sql_execution_error_detail=None,
        pipeline_error=None,
    )
    assert s is None
    assert "excluded" in c


def test_evidence_committed_no_evidence_stays_zero() -> None:
    """A non-empty, non-refused answer with no evidence rows is a real quality failure (0.0)."""
    s, c = score_evidence_citation(
        answer="El Paso employers showed growth.",
        evidence=[],
        must_include=[],
        must_not_include=[],
        refused=False,
        sql_execution_error_detail=None,
        pipeline_error=None,
    )
    assert s == 0.0
    assert "no evidence" in c


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


def test_compute_failure_content_excluded_latency_computed() -> None:
    """On pipeline failure, content metrics are None (excluded); latency is still computed (JIE #263)."""
    g = {
        "id": "gq-001",
        "question": "What is hiring like in El Paso?",
        "intent": "geographic",
        "must_include": [],
        "must_not_include": [],
    }
    out = compute_item_scores(
        golden=g,
        response=None,
        latency_seconds=5.0,
        pipeline_error="connection reset",
        sla_seconds=45.0,
    )
    assert out.intent_accuracy is None
    assert out.evidence_citation is None
    assert out.confidence_flags is None
    assert out.latency_sla > 0.0
    # geographic is data-backed; answerability is excluded on infra failure (JIE #269).
    assert out.answerability is None
    assert out.correct_refusal is None
    assert "excluded" in out.comments["intent_accuracy"].lower()
    assert "excluded" in out.comments["evidence_citation"].lower()
    assert "excluded" in out.comments.get("correct_refusal", "").lower()


def test_compute_sql_error_excludes_evidence_citation_only() -> None:
    """sql_execution_error_detail excludes evidence_citation but leaves intent_accuracy scorable."""
    g = {
        "id": "gq-sql",
        "question": "List employers in the region",
        "intent": "employer",
        "must_include": [],
        "must_not_include": [],
    }
    resp = {
        "answer": "Some answer here.",
        "evidence": [],
        "confidence": 0.8,
        "confidence_flagged_low": False,
        "confidence_explanation": None,
        "volume_flagged_low": False,
        "volume_warning": None,
        "refused": False,
        "sql_execution_error_detail": "relation does not exist",
        "classified_intent": "employer",
        "row_count_returned": 0,
    }
    out = compute_item_scores(
        golden=g,
        response=resp,
        latency_seconds=3.0,
        pipeline_error=None,
        sla_seconds=45.0,
    )
    assert out.intent_accuracy == 1.0, "intent classification is unaffected by SQL error"
    assert out.evidence_citation is None, "SQL error excludes evidence_citation"
    assert out.confidence_flags is not None, "confidence calibration is unaffected by SQL error"


def test_pipeline_error_skips_answerability_for_intent_only() -> None:
    g = {
        "id": "gq-001b",
        "question": "Hiring trend question",
        "intent": "trend",
        "must_include": [],
        "must_not_include": [],
    }
    out = compute_item_scores(
        golden=g,
        response=None,
        latency_seconds=5.0,
        pipeline_error="connection reset",
        sla_seconds=45.0,
    )
    assert out.answerability is None
    assert out.correct_refusal is None
    assert "excluded" in out.comments.get("correct_refusal", "").lower()


def test_compute_happy_path() -> None:
    g = {
        "id": "gq-002",
        "question": "El Paso subregional data",
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
    assert out.correct_refusal is None


def test_answerability_data_backed_zero_rows() -> None:
    g = {
        "id": "gq-00z",
        "question": "Show employers",
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
        "question": "Narrative trend in roles",
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
    assert out.correct_refusal == 0.0
    assert "default" in out.comments.get("correct_refusal", "").lower()


def test_composite_excludes_answerability() -> None:
    base = QAItemScores(
        intent_accuracy=0.5,
        evidence_citation=0.5,
        confidence_flags=0.5,
        latency_sla=0.5,
        answerability=0.0,
        correct_refusal=None,
        comments={},
    )
    assert composite_score(base) == 0.5
    hi = QAItemScores(
        intent_accuracy=0.0,
        evidence_citation=0.0,
        confidence_flags=0.0,
        latency_sla=0.0,
        answerability=1.0,
        correct_refusal=None,
        comments={},
    )
    assert composite_score(hi) == 0.0


def test_composite_filters_none_uses_latency_anchor() -> None:
    """When all content metrics are None (infra failure), composite is anchored to latency_sla only (JIE #263)."""
    scores = QAItemScores(
        intent_accuracy=None,
        evidence_citation=None,
        confidence_flags=None,
        latency_sla=1.0,
        answerability=None,
        correct_refusal=None,
        comments={},
    )
    assert composite_score(scores) == 1.0


def test_composite_filters_none_partial() -> None:
    """Partial None: composite averages over the scorable pool only, denominator shrinks (JIE #263)."""
    scores = QAItemScores(
        intent_accuracy=1.0,
        evidence_citation=None,
        confidence_flags=None,
        latency_sla=1.0,
        answerability=None,
        correct_refusal=None,
        comments={},
    )
    # Only intent_accuracy and latency_sla are scorable → mean((1.0, 1.0)) = 1.0
    assert composite_score(scores) == 1.0

    mixed = QAItemScores(
        intent_accuracy=0.0,
        evidence_citation=None,
        confidence_flags=None,
        latency_sla=1.0,
        answerability=None,
        correct_refusal=None,
        comments={},
    )
    # mean((0.0, 1.0)) = 0.5
    assert abs(composite_score(mixed) - 0.5) < 1e-9


def test_print_console_summary_does_not_crash_with_none_content_metrics() -> None:
    """Regression: print_console_summary must not raise TypeError when content metrics are None.

    Before the fix, f"{None:.2f}" in the worst-N block crashed the summary for any
    eval run that included at least one pipeline-failed item (JIE #263 exclusion design).
    """
    infra_fail = QAItemScores(
        intent_accuracy=None,
        evidence_citation=None,
        confidence_flags=None,
        latency_sla=0.8,
        answerability=None,
        correct_refusal=None,
        comments={
            "intent_accuracy": "excluded: connection reset",
            "evidence_citation": "excluded: connection reset",
            "confidence_flags": "excluded: connection reset",
            "latency_sla": "latency computed",
            "answerability": "skipped: intent not data-backed",
            "correct_refusal": "excluded",
        },
    )
    good = QAItemScores(
        intent_accuracy=1.0,
        evidence_citation=0.9,
        confidence_flags=0.85,
        latency_sla=1.0,
        answerability=None,
        correct_refusal=None,
        comments={
            "intent_accuracy": "intent matches",
            "evidence_citation": "overlap heuristic",
            "confidence_flags": "calibration ok",
            "latency_sla": "within SLA",
            "answerability": "skipped",
            "correct_refusal": "N/A",
        },
    )
    rows = [("gq-001", infra_fail, "connection reset"), ("gq-002", good, None)]
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_console_summary(rows=rows, worst_n=5)
    out = buf.getvalue()
    # None content metrics render as " — " not as a numeric format
    assert " — " in out
    # Non-None metrics still render as float strings
    assert "1.00" in out or "0.90" in out


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
