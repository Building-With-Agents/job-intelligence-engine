"""Boundary validation for :class:`enrichment.types.RecordEnrichedPayload`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from enrichment.types import RecordEnrichedPayload


def test_record_enriched_payload_accepts_extra_keys() -> None:
    """Unknown keys are ignored (``extra="ignore"``)."""
    p = RecordEnrichedPayload.model_validate(
        {
            "spam_tier": "clean",
            "spam_score": 0.1,
            "quality_score": 0.9,
            "future_unknown_field": {"nested": True},
        }
    )
    assert p.spam_tier == "clean"
    assert "future_unknown_field" not in p.model_dump()


def test_record_enriched_payload_rejects_bad_score_type() -> None:
    with pytest.raises(ValidationError):
        RecordEnrichedPayload.model_validate({"spam_tier": "clean", "spam_score": "high", "quality_score": 0.9})


def test_record_enriched_payload_rejects_bool_as_score() -> None:
    with pytest.raises(ValidationError):
        RecordEnrichedPayload.model_validate({"spam_tier": "clean", "spam_score": True, "quality_score": 0.9})


def test_record_enriched_payload_rejects_non_dict_field_confidence() -> None:
    with pytest.raises(ValidationError):
        RecordEnrichedPayload.model_validate(
            {"spam_tier": "clean", "spam_score": 0.1, "quality_score": 0.9, "field_confidence": []}
        )


def test_record_enriched_payload_rejects_non_dict_employer_metadata() -> None:
    with pytest.raises(ValidationError):
        RecordEnrichedPayload.model_validate(
            {
                "spam_tier": "clean",
                "spam_score": 0.1,
                "quality_score": 0.9,
                "employer_metadata": ["not", "a", "dict"],
            }
        )


def test_record_enriched_payload_coerces_int_scores_to_float() -> None:
    p = RecordEnrichedPayload.model_validate(
        {"spam_tier": "clean", "spam_score": 1, "quality_score": 84, "overall_confidence": 0}
    )
    assert p.spam_score == 1.0
    assert p.quality_score == 84.0
    assert p.overall_confidence == 0.0
