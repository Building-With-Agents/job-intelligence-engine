"""Enrichment Agent outbound event builders."""

from __future__ import annotations

from typing import Any

from common.event_envelope import EventEnvelope

# Increment when ``RecordEnriched`` **batch** payload gains backward-incompatible fields.
RECORD_ENRICHED_SCHEMA_VERSION = 3


def _dedup_config_from_env() -> tuple[float, int]:
    from enrichment._config import dedup_cosine_threshold, dedup_rolling_window_days

    return dedup_cosine_threshold(), dedup_rolling_window_days()


def build_dedup_block(
    *,
    stub_count: int = 0,
    rows_with_duplicate_cluster_id: int = 0,
    rows_with_matched_job_posting_id: int = 0,
) -> dict[str, Any]:
    """Default fuzzy-dedup integration block for batch ``RecordEnriched`` (schema v3+)."""
    thresh, days = _dedup_config_from_env()
    return {
        "cosine_threshold": thresh,
        "rolling_window_days": days,
        "stub_count": int(stub_count),
        "rows_with_duplicate_cluster_id": int(rows_with_duplicate_cluster_id),
        "rows_with_matched_job_posting_id": int(rows_with_matched_job_posting_id),
    }


def build_record_enriched_event(
    correlation_id: str,
    batch_id: str,
    *,
    enriched_count: int,
    spam_rejected_count: int,
    flagged_for_review_count: int,
    temporal_period_distribution: dict[str, int],
    borderplex_subregion_distribution: dict[str, int],
    duplicate_count: int,
    soc_classified_count: int,
    naics_classified_count: int,
    dedup_stub_count: int = 0,
    dedup_rows_with_duplicate_cluster_id: int = 0,
    dedup_rows_with_matched_job_posting_id: int = 0,
    freshness_records: list[dict[str, Any]] | None = None,
) -> EventEnvelope:
    """Build one ``RecordEnriched`` event for the whole batch (Week 5–6 + dedup integration)."""
    dedup = build_dedup_block(
        stub_count=dedup_stub_count,
        rows_with_duplicate_cluster_id=dedup_rows_with_duplicate_cluster_id,
        rows_with_matched_job_posting_id=dedup_rows_with_matched_job_posting_id,
    )
    fr = freshness_records if freshness_records is not None else []
    return EventEnvelope(
        correlation_id=correlation_id,
        agent_id="enrichment-agent",
        payload={
            "event_type": "RecordEnriched",
            "record_enriched_schema_version": RECORD_ENRICHED_SCHEMA_VERSION,
            "batch_id": batch_id,
            "enriched_count": enriched_count,
            "spam_rejected_count": spam_rejected_count,
            "flagged_for_review_count": flagged_for_review_count,
            "temporal_period_distribution": dict(temporal_period_distribution),
            "borderplex_subregion_distribution": dict(borderplex_subregion_distribution),
            "duplicate_count": duplicate_count,
            "soc_classified_count": soc_classified_count,
            "naics_classified_count": naics_classified_count,
            "dedup": dedup,
            "freshness_records": list(fr),
        },
    )
