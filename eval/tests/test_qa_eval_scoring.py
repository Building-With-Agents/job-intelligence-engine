"""Unit tests for eval/qa_scoring.py."""

from __future__ import annotations

import io
from contextlib import redirect_stdout

from eval.qa_eval import _run_evaluators_average, print_console_summary
from eval.qa_scoring import (
    _CATASTROPHIC_LATENCY_MULTIPLIER,
    _DEFAULT_LATENCY_SLA_SECONDS,
    LAYER2_METRIC_NAMES,
    QAItemScores,
    _quantile,
    aggregate_human_scores,
    composite_score,
    compute_item_scores,
    compute_no_hallucination_rate,
    confusion_rows,
    human_correctness_composite,
    intent_classification_report,
    intent_eval_trace_metadata,
    run_ece_from_correctness,
    run_subcomposites_and_gates,
    score_confidence_correctness_alignment,
    score_confidence_self_consistency,
    score_evidence_citation,
    score_intent_accuracy,
    score_latency_sla,
    subcomposites_from_means,
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
    s, c, _mir, _eov = score_evidence_citation(
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
    s, c, _mir, _eov = score_evidence_citation(
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
    s, c, _mir, _eov = score_evidence_citation(
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
    s, c, _mir, _eov = score_evidence_citation(
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


def test_evidence_refusal_data_backed_strong_penalty() -> None:
    """JIE #260: wrongful refusal on data-backed items gets rubric scaled ~0.35×, not a length floor."""
    s, c, _mir, _eov = score_evidence_citation(
        answer="I cannot provide SQL results for this geographic query at this time.",
        evidence=[],
        must_include=["el_paso_subregion_filter_confirmed"],
        must_not_include=[],
        refused=True,
        sql_execution_error_detail=None,
        pipeline_error=None,
        intent_is_data_backed=True,
        expected_intent="geographic",
    )
    assert s is not None and s <= 0.35
    assert "0.35" in c or "rubric" in c.lower()


def test_evidence_refusal_intent_only_uses_rubric_not_length() -> None:
    s, c, _mir, _eov = score_evidence_citation(
        answer="Short",
        evidence=[],
        must_include=[],
        must_not_include=[],
        refused=True,
        sql_execution_error_detail=None,
        pipeline_error=None,
        intent_is_data_backed=False,
        expected_intent="trend",
    )
    assert s is not None
    assert s >= 0.7
    assert "intent-only refusal" in c


def test_evidence_committed_no_evidence_stays_zero() -> None:
    """A non-empty, non-refused answer with no evidence rows is a real quality failure (0.0)."""
    s, c, _mir, _eov = score_evidence_citation(
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
    s, _ = score_confidence_self_consistency(
        confidence=0.5,
        confidence_flagged_low=True,
        confidence_explanation="Low volume in cohort; interpret with caution.",
        volume_flagged_low=False,
        volume_warning=None,
    )
    assert s >= 0.9


def test_confidence_miscalibration_is_not_perfect() -> None:
    """JIE #267: wrong flag vs low numeric confidence (Case B) must not be 1.0."""
    s, _ = score_confidence_self_consistency(
        confidence=0.15,
        confidence_flagged_low=False,
        confidence_explanation=None,
        volume_flagged_low=False,
        volume_warning=None,
    )
    assert s < 0.6


def test_latency_at_sla() -> None:
    s, _ = score_latency_sla(latency_seconds=10.0, sla_seconds=45.0)
    assert s == 1.0


def test_latency_above_sla() -> None:
    s, _ = score_latency_sla(latency_seconds=90.0, sla_seconds=45.0)
    assert abs(s - 0.5) < 1e-6


def test_latency_catastrophic_exclusion_returns_none(monkeypatch) -> None:
    """Latency > SLA × _CATASTROPHIC_LATENCY_MULTIPLIER returns (None, reason) (JIE #270)."""
    sla = 15.0
    catastrophic = sla * _CATASTROPHIC_LATENCY_MULTIPLIER + 1.0  # just over threshold
    s, reason = score_latency_sla(latency_seconds=catastrophic, sla_seconds=sla)
    assert s is None, "catastrophic latency must return None, not a float score"
    assert "excluded" in reason.lower()
    assert "catastrophic" in reason.lower()


def test_latency_just_below_catastrophic_threshold_is_scorable() -> None:
    """Latency exactly at SLA×10 boundary is NOT catastrophic; it should return a float score."""
    sla = 15.0
    threshold = sla * _CATASTROPHIC_LATENCY_MULTIPLIER
    just_under = threshold - 0.001
    s, _ = score_latency_sla(latency_seconds=just_under, sla_seconds=sla)
    assert s is not None and isinstance(s, float)
    assert 0.0 <= s <= 1.0


def test_latency_default_sla_is_15s() -> None:
    """Default SLA tightened to 15s (was 45s); a 5.6s response scores 1.0 (JIE #270)."""
    assert _DEFAULT_LATENCY_SLA_SECONDS == 15.0
    s, _ = score_latency_sla(latency_seconds=5.6, sla_seconds=15.0)
    assert s == 1.0


def test_quantile_p95_computation() -> None:
    """_quantile(values, 0.95) returns a value close to the 95th percentile (JIE #270)."""
    # 100 values: 0.0 to 99.0; p95 should be near 94/95
    values = [float(i) for i in range(100)]
    p95 = _quantile(values, 0.95)
    # statistics.quantiles(n=100)[94] is the 95th percentile cut point
    assert 93.0 <= p95 <= 95.0


def test_quantile_single_value_returns_itself() -> None:
    assert _quantile([7.5], 0.95) == 7.5


def test_quantile_p50_is_median() -> None:
    """p50 from _quantile should match the true median for a uniform list."""
    values = [float(i) for i in range(1, 101)]
    p50 = _quantile(values, 0.50)
    import statistics

    assert abs(p50 - statistics.median(values)) < 5.0  # within reasonable range


def test_composite_all_none_when_latency_also_catastrophic() -> None:
    """When all four core metrics are None (incl. latency from catastrophic exclusion), composite is None (JIE #270)."""
    scores = QAItemScores(
        intent_accuracy=None,
        evidence_citation=None,
        confidence_self_consistency=None,
        confidence_in_expected_range=None,
        latency_sla=None,
        answerability=None,
        correct_refusal=None,
        must_include_recall=None,
        evidence_overlap=None,
        comments={},
    )
    assert composite_score(scores) is None


def test_print_console_summary_handles_catastrophic_latency_none() -> None:
    """print_console_summary must not crash when latency_sla=None (catastrophic JIE #270 case)."""
    catastrophic = QAItemScores(
        intent_accuracy=0.8,
        evidence_citation=0.7,
        confidence_self_consistency=0.9,
        confidence_in_expected_range=None,
        latency_sla=None,  # catastrophic exclusion
        answerability=None,
        correct_refusal=None,
        must_include_recall=None,
        evidence_overlap=None,
        comments={
            "intent_accuracy": "intent matches",
            "evidence_citation": "overlap ok",
            "confidence_self_consistency": "calibration ok",
            "confidence_in_expected_range": "no range",
            "latency_sla": "excluded: catastrophic latency 200.0s exceeds threshold 150s (SLA×10)",
            "answerability": "skipped",
            "correct_refusal": "N/A",
        },
    )
    rows = [("gq-cat", catastrophic, None)]
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_console_summary(rows=rows, worst_n=5)
    out = buf.getvalue()
    assert " — " in out  # None renders as dash, not crash


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
    assert out.confidence_self_consistency is None
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
    assert out.confidence_self_consistency is not None, "confidence calibration is unaffected by SQL error"


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
    assert out.confidence_self_consistency > 0.0
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
        confidence_self_consistency=0.5,
        confidence_in_expected_range=None,
        latency_sla=0.5,
        answerability=0.0,
        correct_refusal=None,
        must_include_recall=None,
        evidence_overlap=None,
        comments={},
    )
    assert composite_score(base) == 0.5
    hi = QAItemScores(
        intent_accuracy=0.0,
        evidence_citation=0.0,
        confidence_self_consistency=0.0,
        confidence_in_expected_range=None,
        latency_sla=0.0,
        answerability=1.0,
        correct_refusal=None,
        must_include_recall=None,
        evidence_overlap=None,
        comments={},
    )
    assert composite_score(hi) == 0.0


def test_composite_filters_none_uses_latency_anchor() -> None:
    """When all content metrics are None (infra failure), composite is anchored to latency_sla only (JIE #263)."""
    scores = QAItemScores(
        intent_accuracy=None,
        evidence_citation=None,
        confidence_self_consistency=None,
        confidence_in_expected_range=None,
        latency_sla=1.0,
        answerability=None,
        correct_refusal=None,
        must_include_recall=None,
        evidence_overlap=None,
        comments={},
    )
    assert composite_score(scores) == 1.0


def test_composite_filters_none_partial() -> None:
    """Partial None: composite averages over the scorable pool only, denominator shrinks (JIE #263)."""
    scores = QAItemScores(
        intent_accuracy=1.0,
        evidence_citation=None,
        confidence_self_consistency=None,
        confidence_in_expected_range=None,
        latency_sla=1.0,
        answerability=None,
        correct_refusal=None,
        must_include_recall=None,
        evidence_overlap=None,
        comments={},
    )
    # Only intent_accuracy and latency_sla are scorable → mean((1.0, 1.0)) = 1.0
    assert composite_score(scores) == 1.0

    mixed = QAItemScores(
        intent_accuracy=0.0,
        evidence_citation=None,
        confidence_self_consistency=None,
        confidence_in_expected_range=None,
        latency_sla=1.0,
        answerability=None,
        correct_refusal=None,
        must_include_recall=None,
        evidence_overlap=None,
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
        confidence_self_consistency=None,
        confidence_in_expected_range=None,
        latency_sla=0.8,
        answerability=None,
        correct_refusal=None,
        must_include_recall=None,
        evidence_overlap=None,
        comments={
            "intent_accuracy": "excluded: connection reset",
            "evidence_citation": "excluded: connection reset",
            "confidence_self_consistency": "excluded: connection reset",
            "confidence_in_expected_range": "excluded: connection reset",
            "latency_sla": "latency computed",
            "answerability": "skipped: intent not data-backed",
            "correct_refusal": "excluded",
        },
    )
    good = QAItemScores(
        intent_accuracy=1.0,
        evidence_citation=0.9,
        confidence_self_consistency=0.85,
        confidence_in_expected_range=None,
        latency_sla=1.0,
        answerability=None,
        correct_refusal=None,
        must_include_recall=None,
        evidence_overlap=None,
        comments={
            "intent_accuracy": "intent matches",
            "evidence_citation": "overlap heuristic",
            "confidence_self_consistency": "calibration ok",
            "confidence_in_expected_range": "no range",
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


def test_subcomposites_geometric_not_crashing_on_nones() -> None:
    """JIE #268: overall is None if any of the four sub-ingredients is None."""
    s = subcomposites_from_means(
        evidence_citation=1.0,
        intent_accuracy=1.0,
        latency_sla=1.0,
        answerability=None,
        correct_refusal=None,
        confidence_self_consistency=1.0,
        confidence_in_expected_range=None,
    )
    assert s["pipeline_health_composite"] == 1.0
    out = s["overall_geometric_composite"]
    assert out is not None
    assert abs(out - 1.0) < 1e-6


def test_subcomposites_empty_rows() -> None:
    assert run_subcomposites_and_gates([])["gate_message"] == "no items"


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


def test_intent_classification_report_macro_f1() -> None:
    rep = intent_classification_report(
        [
            ("geographic", "geographic"),
            ("geographic", "comparison"),
            ("trend", "trend"),
        ]
    )
    assert rep["macro_f1"] is not None
    assert rep["weighted_f1"] is not None
    geo = rep["per_class"].get("geographic")
    assert geo is not None
    assert geo["support"] == 2.0


def test_intent_eval_trace_metadata_mismatch() -> None:
    m = intent_eval_trace_metadata(
        expected_intent="geographic",
        classified_intent="comparison",
        intent_accuracy=0.0,
    )
    assert m["eval_intent_correct"] == 0.0
    assert m["eval_intent_false_negative_class"] == "geographic"
    assert m["eval_intent_false_positive_class"] == "comparison"


# ---------------------------------------------------------------------------
# _run_evaluators_average — catastrophic-exclusion count and SLA consistency
# ---------------------------------------------------------------------------


def _make_item_result(latency_raw: float, latency_sla: float | None) -> object:
    """Build a minimal item_result-like object for _run_evaluators_average.

    Simulates what combined_evaluator emits: always latency_seconds_raw,
    latency_sla only when not catastrophic.
    """
    from types import SimpleNamespace

    evals = [SimpleNamespace(name="latency_seconds_raw", value=latency_raw)]
    if latency_sla is not None:
        evals.append(SimpleNamespace(name="latency_sla", value=latency_sla))
    return SimpleNamespace(evaluations=evals)


def test_run_evaluators_n_cat_reflects_catastrophic_exclusions() -> None:
    """catastrophic_excluded in the run-level comment must equal the number of items
    where latency_sla was excluded (None), NOT the number of malformed items (JIE #270).

    Setup: 4 normal items (latency_sla emitted) + 1 catastrophic (latency_sla omitted).
    Expected: catastrophic_excluded=1 in the p95_latency_seconds comment.
    """
    items = [
        _make_item_result(3.0, 1.0),
        _make_item_result(4.0, 1.0),
        _make_item_result(5.0, 1.0),
        _make_item_result(6.0, 1.0),
        _make_item_result(200.0, None),  # catastrophic — latency_sla excluded
    ]
    run_mean = _run_evaluators_average()[0]
    out = run_mean(item_results=items)

    p95_ev = next((e for e in out if e.name == "p95_latency_seconds"), None)
    assert p95_ev is not None, "p95_latency_seconds must be emitted"
    # Comment must report 1 catastrophic exclusion, not 0
    assert "catastrophic_excluded=1" in p95_ev.comment, (
        f"Expected catastrophic_excluded=1 in comment, got: {p95_ev.comment!r}"
    )
    # n= counts raw latency entries; all 5 items have latency_seconds_raw
    assert "n=5" in p95_ev.comment
    assert "n_total=5" in p95_ev.comment


def test_run_evaluators_p95_sla_score_uses_config_sla(monkeypatch) -> None:
    """p95_latency_sla_score must use qa_latency_sla_seconds() (i.e., respect
    QA_EVAL_LATENCY_SLA_SECONDS env var) rather than the hardcoded default (JIE #270).

    If _DEFAULT_LATENCY_SLA_SECONDS (15s) were used instead of a custom 30s SLA,
    p95_latency_sla_score for a p95 of 20s would be 15/20 = 0.75.
    With sla=30s it is 30/20 = 1.0.  We assert the latter.
    """
    import eval._config as cfg

    monkeypatch.setattr(cfg, "qa_latency_sla_seconds", lambda: 30.0)
    # Invalidate the cached_accessor so the monkeypatched lambda takes effect
    if hasattr(cfg.qa_latency_sla_seconds, "cache_clear"):
        cfg.qa_latency_sla_seconds.cache_clear()

    # 100 values; p95 ≈ 20s, well under the custom 30s SLA → score must be 1.0
    items = [_make_item_result(float(i), 1.0) for i in range(1, 101)]
    run_mean = _run_evaluators_average()[0]
    out = run_mean(item_results=items)

    p95_sla_ev = next((e for e in out if e.name == "p95_latency_sla_score"), None)
    assert p95_sla_ev is not None, "p95_latency_sla_score must be emitted"
    # p95 of [1..100] is ~95s; with sla=30 that's 30/95 ≈ 0.315.
    # With the old hardcoded 15s sla it would be 15/95 ≈ 0.158.
    # Verify the comment references the custom SLA (30.0), not the default (15.0).
    assert "30.0s" in p95_sla_ev.comment, (
        f"p95_latency_sla_score comment must reference custom SLA=30.0s, got: {p95_sla_ev.comment!r}"
    )


# ---------------------------------------------------------------------------
# Layer 2 scoring tests (JIE #271)
# ---------------------------------------------------------------------------


class TestAggregateHumanScores:
    def test_empty_returns_none(self) -> None:
        assert aggregate_human_scores([]) is None

    def test_single_annotator_passthrough(self) -> None:
        assert aggregate_human_scores([0.75]) == 0.75

    def test_two_annotators_mean(self) -> None:
        result = aggregate_human_scores([0.8, 0.6])
        assert abs(result - 0.7) < 1e-9

    def test_three_annotators_mean(self) -> None:
        result = aggregate_human_scores([1.0, 0.5, 0.0])
        assert abs(result - 0.5) < 1e-9

    def test_high_spread_still_returns_mean(self) -> None:
        # Spread 0.9 > 0.3 threshold — logs warning but still returns mean
        result = aggregate_human_scores([0.9, 0.0])
        assert abs(result - 0.45) < 1e-9

    def test_tight_spread_no_warning(self) -> None:
        # Spread 0.1 < 0.3 — no warning, deterministic mean
        result = aggregate_human_scores([0.85, 0.75])
        assert result is not None
        assert 0.79 < result < 0.81


class TestRunECEFromCorrectness:
    def test_none_on_empty(self) -> None:
        assert run_ece_from_correctness([], []) is None

    def test_none_on_mismatched_lengths(self) -> None:
        assert run_ece_from_correctness([0.9], [True, False]) is None

    def test_none_when_all_correctness_none(self) -> None:
        assert run_ece_from_correctness([0.8, 0.5], [None, None]) is None

    def test_perfect_calibration_near_zero(self) -> None:
        # Single item: conf 1.0 → correctness True (1.0); within same bin → ECE = 0
        ece = run_ece_from_correctness([1.0], [True])
        assert ece is not None
        assert ece < 1e-9

    def test_systematic_overconfidence_positive_ece(self) -> None:
        # All conf=1.0 but all wrong → ECE should be 1.0
        ece = run_ece_from_correctness([1.0, 1.0, 1.0], [False, False, False])
        assert ece is not None
        assert ece > 0.5

    def test_excludes_none_correctness_items(self) -> None:
        # Third item has None — excluded; result computed from the other two
        ece = run_ece_from_correctness([0.9, 0.1, 0.5], [True, False, None])
        assert ece is not None

    def test_bool_and_float_correctness_compatible(self) -> None:
        ece_bool = run_ece_from_correctness([0.8], [True])
        ece_float = run_ece_from_correctness([0.8], [1.0])
        assert ece_bool is not None
        assert ece_float is not None
        assert abs(ece_bool - ece_float) < 1e-9

    def test_returns_float_between_zero_and_one(self) -> None:
        ece = run_ece_from_correctness([0.9, 0.1, 0.6, 0.4], [True, False, True, False])
        assert ece is not None
        assert 0.0 <= ece <= 1.0


class TestScoreConfidenceCorrectnessAlignment:
    def test_none_when_correctness_unavailable(self) -> None:
        score, comment = score_confidence_correctness_alignment(confidence=0.8, correctness=None)
        assert score is None
        assert "pending" in comment.lower() or "layer 2" in comment.lower()

    def test_perfect_alignment(self) -> None:
        score, comment = score_confidence_correctness_alignment(confidence=0.7, correctness=0.7)
        assert score is not None
        assert abs(score - 1.0) < 1e-9
        assert "distance=0.000" in comment

    def test_maximum_misalignment(self) -> None:
        score, comment = score_confidence_correctness_alignment(confidence=1.0, correctness=0.0)
        assert score is not None
        assert abs(score - 0.0) < 1e-9

    def test_partial_misalignment(self) -> None:
        score, comment = score_confidence_correctness_alignment(confidence=0.9, correctness=0.5)
        assert score is not None
        assert abs(score - 0.6) < 1e-6

    def test_clamps_inputs(self) -> None:
        score, _ = score_confidence_correctness_alignment(confidence=1.5, correctness=-0.1)
        # Clamped: conf=1.0, corr=0.0 → distance=1.0 → score=0.0
        assert score is not None
        assert score >= 0.0


class TestComputeNoHallucinationRate:
    def test_empty_returns_zero(self) -> None:
        assert compute_no_hallucination_rate([]) == 0.0

    def test_all_missing_correctness_returns_zero(self) -> None:
        items = [{"confidence": 0.9}, {"confidence": 0.5}]
        assert compute_no_hallucination_rate(items) == 0.0

    def test_high_conf_high_correctness_is_safe(self) -> None:
        items = [{"confidence": 0.9, "human_correctness": 0.8}]
        assert compute_no_hallucination_rate(items) == 1.0

    def test_low_conf_is_safe_regardless_of_correctness(self) -> None:
        # conf < 0.5: pipeline self-reports uncertainty → safe
        items = [{"confidence": 0.3, "human_correctness": 0.1}]
        assert compute_no_hallucination_rate(items) == 1.0

    def test_high_conf_low_correctness_is_unsafe(self) -> None:
        # confident hallucinator
        items = [{"confidence": 0.9, "human_correctness": 0.2}]
        assert compute_no_hallucination_rate(items) == 0.0

    def test_mixed_safe_unsafe(self) -> None:
        items = [
            {"confidence": 0.9, "human_correctness": 0.9},  # safe
            {"confidence": 0.9, "human_correctness": 0.2},  # unsafe
        ]
        rate = compute_no_hallucination_rate(items)
        assert abs(rate - 0.5) < 1e-9

    def test_ignores_items_without_correctness(self) -> None:
        # Only 1 of 2 items has correctness; only that one counts
        items = [
            {"confidence": 0.9, "human_correctness": 0.9},
            {"confidence": 0.9},
        ]
        assert compute_no_hallucination_rate(items) == 1.0


class TestHumanCorrectnessComposite:
    def test_all_none_returns_none(self) -> None:
        assert human_correctness_composite(correctness=None, decision_relevance=None, followup_quality=None) is None

    def test_all_present_weighted_mean(self) -> None:
        # correctness=1.0 * 0.5 + dr=1.0 * 0.3 + fq=1.0 * 0.2 = 1.0
        result = human_correctness_composite(correctness=1.0, decision_relevance=1.0, followup_quality=1.0)
        assert result is not None
        assert abs(result - 1.0) < 1e-9

    def test_weights_sum_correctly(self) -> None:
        # correctness=1.0, dr=0.0, fq=0.0 → 0.5 / (0.5+0.3+0.2) = 0.5
        result = human_correctness_composite(correctness=1.0, decision_relevance=0.0, followup_quality=0.0)
        assert result is not None
        assert abs(result - 0.5) < 1e-9

    def test_missing_dimension_renormalizes(self) -> None:
        # Only correctness present (weight 0.5): renorm → 0.5/0.5 = 1.0 weight
        result = human_correctness_composite(correctness=0.8, decision_relevance=None, followup_quality=None)
        assert result is not None
        assert abs(result - 0.8) < 1e-9

    def test_two_dimensions_renormalize(self) -> None:
        # correctness=1.0 (0.5) + dr=0.0 (0.3); fq missing → total_w=0.8
        # weighted_sum = 1.0*0.5 + 0.0*0.3 = 0.5; / 0.8 = 0.625
        result = human_correctness_composite(correctness=1.0, decision_relevance=0.0, followup_quality=None)
        assert result is not None
        assert abs(result - 0.625) < 1e-9

    def test_output_clamped_to_zero_one(self) -> None:
        result = human_correctness_composite(correctness=0.0, decision_relevance=0.0, followup_quality=0.0)
        assert result is not None
        assert 0.0 <= result <= 1.0


class TestSubcompositesNoHallucinationRate:
    def _base_args(self) -> dict:
        return dict(
            evidence_citation=0.8,
            intent_accuracy=0.9,
            latency_sla=1.0,
            answerability=None,
            correct_refusal=None,
            confidence_self_consistency=0.80,
            confidence_in_expected_range=None,
        )

    def test_no_hallucination_rate_absent_unchanged(self) -> None:
        s1 = subcomposites_from_means(**self._base_args())
        s2 = subcomposites_from_means(**self._base_args(), no_hallucination_rate=None)
        assert s1["safety_composite"] == s2["safety_composite"]

    def test_no_hallucination_rate_blends_into_safety(self) -> None:
        without = subcomposites_from_means(**self._base_args())
        with_nhr = subcomposites_from_means(**self._base_args(), no_hallucination_rate=1.0)
        # safety_composite should increase toward 1.0 when nhr=1.0
        sfty_base = without["safety_composite"]
        sfty_nhr = with_nhr["safety_composite"]
        assert sfty_nhr is not None
        assert sfty_base is not None
        assert sfty_nhr > sfty_base

    def test_no_hallucination_rate_fallback_when_no_csc(self) -> None:
        # When csc is None, safety_composite would normally be None
        args = self._base_args()
        args["confidence_self_consistency"] = None
        without = subcomposites_from_means(**args)
        assert without["safety_composite"] is None
        with_nhr = subcomposites_from_means(**args, no_hallucination_rate=0.9)
        # Now nhr fills in as safety_composite
        assert with_nhr["safety_composite"] is not None
        assert abs(with_nhr["safety_composite"] - 0.9) < 1e-9

    def test_no_hallucination_rate_weight(self) -> None:
        # With csc=0.5 and nhr=1.0: 0.80 * 0.5 + 0.20 * 1.0 = 0.60
        args = self._base_args()
        args["confidence_self_consistency"] = 0.5
        with_nhr = subcomposites_from_means(**args, no_hallucination_rate=1.0)
        assert with_nhr["safety_composite"] is not None
        assert abs(with_nhr["safety_composite"] - 0.6) < 1e-6


class TestLayer2Constants:
    def test_metric_names_set(self) -> None:
        assert "correctness" in LAYER2_METRIC_NAMES
        assert "decision_relevance" in LAYER2_METRIC_NAMES
        assert "followup_quality" in LAYER2_METRIC_NAMES
        assert len(LAYER2_METRIC_NAMES) == 3
