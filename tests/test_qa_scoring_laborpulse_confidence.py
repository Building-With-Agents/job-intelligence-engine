"""LaborPulse ``confidence`` string mapping for eval scoring."""

from __future__ import annotations

import pytest

from eval.qa_scoring import coerce_eval_response_confidence, compute_item_scores


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("low", 0.0),
        ("LOW", 0.0),
        (" medium ", 0.5),
        ("high", 1.0),
        (0.75, 0.75),
        (2.0, 1.0),
        ("0.8", 0.8),
        ("", 0.0),
        (None, 0.0),
        ("nope", 0.0),
    ],
)
def test_coerce_eval_response_confidence(raw: object, expected: float) -> None:
    assert coerce_eval_response_confidence(raw) == pytest.approx(expected)


def test_compute_item_scores_accepts_laborpulse_confidence_string() -> None:
    sc = compute_item_scores(
        golden={
            "id": "gq-lp-1",
            "question": "x?",
            "intent": "curriculum",
            "must_include": [],
            "must_not_include": [],
        },
        response={
            "answer": "x",
            "evidence": [{"title": "t", "source": "s", "snippet": "n"}],
            "confidence": "low",
            "refused": False,
        },
        latency_seconds=1.0,
        pipeline_error=None,
    )
    assert isinstance(sc.confidence_self_consistency, float)
    assert 0.0 <= sc.confidence_self_consistency <= 1.0
