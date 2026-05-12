"""
Enrichment Agent — Phase 1 lite (Pair C: classification + quality + spam; Pair D: resolvers).

When ``SkillsExtracted`` includes a non-empty ``records`` list, applies Pair C gating and
Pair D resolution per row, then emits one batch-level ``RecordEnriched`` (Week 5 counts +
Week 6 distributions) via :func:`build_record_enriched_event`.

Otherwise (flat single-record payloads), loads reference labels from ``technology_areas`` and
``industry_sectors`` when ``PYTHON_DATABASE_URL`` is set; uses
``classification.FALLBACK_TECH_AREA_LABELS`` for offline runs; emits a per-record
``RecordEnriched`` with role, seniority, quality, and spam preview fields.

When ``check_db_connection()`` is true, batch mode opens one ``session_scope()`` for the
whole batch so ``resolve_company`` / ``resolve_location`` use the real dbo session.

Agent ID (canonical): enrichment-agent
Emits:    RecordEnriched
          EnrichmentDegraded (alert bus, when registered)
Consumes: SkillsExtracted

When the inbound payload includes ``normalized_job_id`` (int) and ``PYTHON_DATABASE_URL``
is set, spam scoring loads the latest ``dbo.extracted_intelligence`` row and calls
``score_spam_preview``. Otherwise ``spam_score`` / ``is_spam`` come from the
walking-skeleton fixture keyed by ``posting_id``.

When the normalized job resolves to a ``job_postings`` row, the emitted
``RecordEnriched`` payload also includes ``temporal_period`` and
``borderplex_subregion`` derived from normalized-job context so the live
event output matches the promotion/write path.

With ``normalized_job_id`` and a resolvable ``job_postings`` row (join on
``source``/``external_id``), Phase 1 enrichment columns are persisted via
:mod:`enrichment.job_postings_promotion`. **Rejected** spam tier skips
``UPDATE`` entirely. **Uncertain** (degraded classifier) updates quality fields
only and leaves ``is_spam``/``spam_score`` unchanged.

Fixture: data/fixtures/fixture_enriched.json — supplies ``company`` /
``company_id`` / ``sector_id`` when not on the event; role, seniority, and
``quality_score`` are always computed (not taken from the fixture). Spam
scores use the fixture only when ``normalized_job_id`` is absent or DB is
unconfigured.

CLI: ``python -m enrichment.agent --limit 50`` (loads repo-root ``.env`` via
python-dotenv, then requires ``PYTHON_DATABASE_URL``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import structlog
from dotenv import load_dotenv
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from common.base_agent import BaseAgent
from common.data_store.database import check_db_connection, session_scope
from common.data_store.models import IndustrySector, NormalizedJob, TechnologyArea
from common.event_envelope import EventEnvelope
from common.llm_client import ainvoke_skills_llm, invoke_skills_llm
from common.types.job_profile import EmployerProfile
from enrichment.adapters.facade import ExternalEnrichmentFacade
from enrichment.async_bridge import run_coroutine
from enrichment.classification import (
    FALLBACK_TECH_AREA_LABELS,
    classify_job,
)
from enrichment.classifiers.employer_classifier import (
    build_employer_profile,
    build_employer_profile_async,
    persist_employer_metadata,
)
from enrichment.classifiers.naics_classifier import classify_naics, classify_naics_async
from enrichment.classifiers.quality import score_quality
from enrichment.classifiers.soc_classifier import classify_soc
from enrichment.classifiers.spam_preview import (
    SpamPreviewResult,
    apply_spam_tiers,
    score_spam_preview,
)
from enrichment.job_postings_promotion import (
    apply_enrichment_to_job_postings,
    derive_enrichment_output_fields,
    resolve_job_posting_row,
)
from enrichment.resolvers.company_resolver import resolve_company
from enrichment.resolvers.confidence import (
    compute_field_confidence,
    compute_overall_confidence,
)
from enrichment.resolvers.events import build_record_enriched_event
from enrichment.resolvers.freshness_slice import build_freshness_record_for_analytics
from enrichment.resolvers.location_resolver import resolve_location
from enrichment.resolvers.sector_resolver import resolve_sector
from enrichment.schemas import EnrichedJobProfile
from scripts.jsearch_enrichment_preview_lib import build_extraction_dict

log = structlog.get_logger()


def _enrichment_soc_llm() -> Callable[[str], str]:
    """Sync callable for :func:`classify_soc`; Azure OpenAI via :func:`invoke_skills_llm`."""

    def llm(prompt: str) -> str:
        try:
            text, meta = invoke_skills_llm(
                prompt,
                agent_name="enrichment-soc-classifier",
                role="classification",
            )
        except TypeError as exc:
            if "api_key" in str(exc).lower() or "auth" in str(exc).lower():
                log.warning("soc_llm_auth_failed", error=str(exc))
                return "unclassified"
            raise
        if not meta.get("success") or meta.get("extraction_failed"):
            log.warning(
                "enrichment_soc_llm_call_failed",
                success=meta.get("success"),
                extraction_failed=meta.get("extraction_failed"),
                error_reason=meta.get("error_reason"),
            )
            return "unclassified"
        return (text or "").strip()

    return llm


def _enrichment_soc_llm_async() -> Callable:
    """Async callable for :func:`classify_soc` ``async_llm`` parameter."""

    async def async_llm(prompt: str) -> str:
        try:
            text, meta = await ainvoke_skills_llm(
                prompt,
                agent_name="enrichment-soc-classifier",
                role="classification",
            )
        except TypeError as exc:
            if "api_key" in str(exc).lower() or "auth" in str(exc).lower():
                log.warning("soc_llm_auth_failed", error=str(exc))
                return "unclassified"
            raise
        if not meta.get("success") or meta.get("extraction_failed"):
            log.warning(
                "enrichment_soc_llm_call_failed",
                success=meta.get("success"),
                extraction_failed=meta.get("extraction_failed"),
                error_reason=meta.get("error_reason"),
            )
            return "unclassified"
        return (text or "").strip()

    return async_llm


_alert_bus: Any = None

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATH = _REPO_ROOT / ".env"


def _load_repo_dotenv() -> None:
    """Load repo-root ``.env`` once. Does not override variables already set in the OS env."""
    load_dotenv(_ENV_PATH, override=False)


_FIXTURE_PATH = Path(__file__).parent.parent / "data" / "fixtures" / "fixture_enriched.json"

_LATEST_EI_BY_NJ_ID_SQL = text(
    """
    SELECT
        skills,
        tools,
        tasks,
        responsibilities,
        context,
        COALESCE(extraction_failed, false) AS extraction_failed
    FROM dbo.extracted_intelligence
    WHERE normalized_job_id = :nj_id
    ORDER BY extracted_at DESC NULLS LAST, id DESC
    LIMIT 1
    """
)


_LATEST_EI_SQL = text(
    """
    WITH latest_ei AS (
        SELECT DISTINCT ON (normalized_job_id)
            normalized_job_id,
            skills,
            tools,
            tasks,
            responsibilities,
            context
        FROM dbo.extracted_intelligence
        WHERE normalized_job_id IS NOT NULL
        ORDER BY normalized_job_id, extracted_at DESC NULLS LAST, id DESC
    )
    SELECT
        jp.job_posting_id::text AS job_posting_id,
        jp.job_title,
        jp.job_description,
        jp.source,
        jp.external_id,
        COALESCE(jp.is_internship, false) AS is_internship,
        le.skills,
        le.tools,
        le.tasks,
        le.responsibilities,
        le.context
    FROM dbo.job_postings jp
    LEFT JOIN dbo.normalized_jobs nj
        ON jp.source IS NOT NULL
        AND jp.external_id IS NOT NULL
        AND nj.source = jp.source
        AND nj.external_id = jp.external_id
    LEFT JOIN latest_ei le ON le.normalized_job_id = nj.id
    ORDER BY jp.job_posting_id
    LIMIT :lim
    """
)


def _db_url_configured() -> bool:
    return bool(os.getenv("PYTHON_DATABASE_URL"))


def register_alert_bus(bus: Any | None) -> None:
    """Register the event bus so EnrichmentDegraded can be published."""
    global _alert_bus
    _alert_bus = bus


def _coerce_normalized_job_id(raw: Any) -> int | None:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def _spam_from_preview_result(result: SpamPreviewResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "spam_score": result.spam_score,
        "is_spam": result.is_spam,
        "spam_tier": result.tier,
        "field_confidence": dict(result.field_confidence),
        "overall_confidence": result.overall_confidence,
        "spam_rationale": result.rationale,
        "spam_extraction_note": result.extraction_note,
        "spam_degraded": result.degraded,
        "spam_used_heuristic": result.used_heuristic,
    }
    return out


def _degraded_spam_result(*, extraction_note: str | None) -> SpamPreviewResult:
    return SpamPreviewResult(
        spam_score=None,
        is_spam=None,
        tier="uncertain",
        field_confidence={},
        overall_confidence=None,
        rationale=None,
        degraded=True,
        extraction_note=extraction_note,
        used_heuristic=False,
    )


def _emit_enrichment_degraded(
    *,
    correlation_id: str,
    posting_id: Any,
    normalized_job_id: int | None,
    triggered_by_event_type: Any,
    reason: str,
    extraction_note: str | None,
) -> None:
    message = "Spam classification degraded; record continued with null spam fields."
    log.warning(
        "EnrichmentDegraded",
        posting_id=posting_id,
        normalized_job_id=normalized_job_id,
        reason=reason,
        extraction_note=extraction_note,
        message=message,
    )
    if _alert_bus is None:
        return
    try:
        event = EventEnvelope(
            correlation_id=correlation_id,
            agent_id="enrichment-agent",
            payload={
                "event_type": "EnrichmentDegraded",
                "posting_id": posting_id,
                "normalized_job_id": normalized_job_id,
                "triggered_by_event_type": triggered_by_event_type,
                "classifier": "spam_preview",
                "reason": reason,
                "degraded_fields": [
                    "spam_score",
                    "is_spam",
                    "field_confidence",
                    "overall_confidence",
                ],
                "extraction_note": extraction_note,
                "message": message,
            },
        )
        _alert_bus.publish(event)
    except Exception as exc:
        log.warning(
            "EnrichmentDegraded_publish_failed",
            posting_id=posting_id,
            normalized_job_id=normalized_job_id,
            error=str(exc),
        )


def _check_soc_unclassified_rate(
    *,
    soc_classified_count: int,
    enriched_count: int,
    correlation_id: str,
    batch_id: str,
    triggered_by_event_type: Any,
) -> None:
    """Emit a warning and fire EnrichmentDegraded when too many records are SOC-unclassified.

    The threshold is read from ``config/enrichment.yaml``
    (``enrichment.soc.unclassified_rate_threshold``) or env override
    ``SOC_UNCLASSIFIED_RATE_THRESHOLD``; default is 0.10 (10 %).

    This is the batch-level fail-safe: individual unclassified records already
    emit ``log.warning("soc_classifier_llm_resolution")`` from the classifier
    and ``log.warning("enrich_record_soc_unclassified")`` from the agent.  This
    function adds aggregate visibility and routes the alert to the Orchestration
    Agent via the event bus when the rate is abnormal.

    Safe to call with ``enriched_count == 0`` — no check is performed.
    """
    if enriched_count <= 0:
        return

    from enrichment._config import soc_unclassified_rate_threshold

    threshold = soc_unclassified_rate_threshold()
    unclassified_count = enriched_count - soc_classified_count
    unclassified_rate = unclassified_count / enriched_count

    if unclassified_rate <= threshold:
        return

    log.warning(
        "soc_unclassified_rate_exceeded",
        batch_id=batch_id,
        enriched_count=enriched_count,
        soc_classified_count=soc_classified_count,
        unclassified_count=unclassified_count,
        unclassified_rate=round(unclassified_rate, 4),
        threshold=threshold,
        message=(
            f"SOC unclassified rate {unclassified_rate:.1%} exceeds threshold "
            f"{threshold:.1%} for batch {batch_id}. "
            "Check dbo.socc population and LLM classification quality."
        ),
    )

    if _alert_bus is None:
        return
    try:
        event = EventEnvelope(
            correlation_id=correlation_id,
            agent_id="enrichment-agent",
            payload={
                "event_type": "EnrichmentDegraded",
                "batch_id": batch_id,
                "triggered_by_event_type": triggered_by_event_type,
                "classifier": "soc",
                "reason": "unclassified_rate_exceeded",
                "unclassified_rate": round(unclassified_rate, 4),
                "threshold": threshold,
                "enriched_count": enriched_count,
                "soc_classified_count": soc_classified_count,
                "unclassified_count": unclassified_count,
                "degraded_fields": ["soc_code"],
                "message": (f"SOC unclassified rate {unclassified_rate:.1%} exceeds threshold {threshold:.1%}."),
            },
        )
        _alert_bus.publish(event)
    except Exception as exc:
        log.warning(
            "EnrichmentDegraded_publish_failed",
            batch_id=batch_id,
            classifier="soc",
            error=str(exc),
        )


SpamBucket = Literal["rejected", "flagged", "proceed"]


def _records_from_skills_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows from ``records`` only (batch path caller ensures non-empty list)."""
    raw = payload.get("records")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        out.append(dict(item) if isinstance(item, dict) else {})
    return out


def _spam_bucket(record: dict[str, Any]) -> SpamBucket:
    if "is_spam" in record:
        if record["is_spam"] is True:
            return "rejected"
        if record["is_spam"] is None:
            return "flagged"
    spam_score = record.get("spam_score")
    if spam_score is not None:
        try:
            s = float(spam_score)
        except (TypeError, ValueError):
            return "proceed"
        if s > 0.9:
            return "rejected"
        if s >= 0.7:
            return "flagged"
    return "proceed"


def _distribution_bucket(value: Any) -> str:
    if value is None:
        return "unknown"
    s = str(value).strip()
    return s if s else "unknown"


_EXTRA_POSTING_KEYS = frozenset(
    {
        "soc_code",
        "naics_code",
        "employer_metadata",
        "temporal_period",
        "borderplex_subregion",
        "is_duplicate",
        "duplicate_cluster_id",
        "matched_job_posting_id",
        "survivor_job_posting_id",
        "stub",
        "date_posted",
    }
)


def _rollup_fuzzy_dedup_signals(
    enriched: dict[str, Any],
    posting: dict[str, Any],
) -> tuple[int, int, int]:
    """Return (stub_inc, cluster_row_inc, matched_inc) per row, each 0 or 1."""
    stub_inc = int(
        enriched.get("stub") is True or enriched.get("fuzzy_dedup_stub") is True or posting.get("stub") is True
    )

    def _has_cluster(d: dict[str, Any]) -> bool:
        v = d.get("duplicate_cluster_id")
        return v is not None and bool(str(v).strip())

    cluster_inc = int(_has_cluster(enriched) or _has_cluster(posting))
    mid = enriched.get("matched_job_posting_id") or posting.get("matched_job_posting_id")
    matched_inc = int(mid is not None and bool(str(mid).strip()))
    return stub_inc, cluster_inc, matched_inc


def _posting_for_enrichment(
    record: dict[str, Any],
    batch_payload: dict[str, Any],
) -> dict[str, Any]:
    def pick(key: str, default: Any = None) -> Any:
        if key in record and record[key] is not None:
            return record[key]
        return batch_payload.get(key, default)

    base: dict[str, Any] = {
        "posting_id": pick("posting_id"),
        "title": pick("title"),
        "company": pick("company"),
        "description": pick("description", None),
        "location": pick("location", "") or "",
        "normalized_job_id": pick("normalized_job_id"),
        "source": pick("source"),
        "external_id": pick("external_id"),
        "quality_score": pick("quality_score"),
        "spam_score": pick("spam_score"),
        "is_spam": pick("is_spam"),
        "seniority": pick("seniority"),
        "role_classification": pick("role_classification"),
        "skills": pick("skills", []) or [],
        "seniority_confidence": 0.90 if pick("seniority") is not None else 0.0,
        "extraction_confidence": pick("extraction_confidence"),
        "taxonomy_coverage": pick("taxonomy_coverage"),
    }
    for k in _EXTRA_POSTING_KEYS:
        v = pick(k, None)
        if v is not None:
            base[k] = v
    return base


def _job_postings_promotion_payload(
    enriched: dict[str, Any],
    posting: dict[str, Any],
) -> dict[str, Any]:
    """Build a payload for :func:`apply_enrichment_to_job_postings` from batch enrichment output."""
    spam_score = enriched.get("spam_score")
    if spam_score is None:
        spam_score = posting.get("spam_score")
    spam_tier = enriched.get("spam_tier")
    if spam_tier is None:
        spam_tier = posting.get("spam_tier")
    if not spam_tier and isinstance(spam_score, (int, float)):
        _, spam_tier = apply_spam_tiers(float(spam_score))
    quality_score = enriched.get("quality_score")
    if quality_score is None:
        quality_score = posting.get("quality_score")
    return {
        "spam_tier": spam_tier,
        "spam_score": spam_score,
        "quality_score": quality_score,
        "overall_confidence": enriched.get("overall_confidence"),
        "field_confidence": enriched.get("field_confidence"),
        "naics_code": enriched.get("naics_code"),
        "soc_code": enriched.get("soc_code"),
        "role_classification": enriched.get("role_classification"),
        # Forward fields the promotion path COALESCEs into job_postings.
        # Without these, seniority_level + employer_profile_id stay NULL and
        # the backfill scripts have to plug the gap (issues #281, #282).
        "seniority_level": enriched.get("seniority_level") or enriched.get("seniority"),
        "seniority": enriched.get("seniority"),
        "employer_metadata": enriched.get("employer_metadata"),
        "company_id": enriched.get("company_id"),
    }


class EnrichmentAgent(BaseAgent):
    """Deterministic enrichment: role + seniority; batch Pair D when ``records`` is set."""

    @property
    def agent_id(self) -> str:
        return "enrichment-agent"

    def __init__(self, external_facade: ExternalEnrichmentFacade | None = None) -> None:
        self._fixture: dict[int, dict] = {}
        self._refs: tuple[list[tuple[str, str]], list[tuple[str, str]]] | None = None
        self._external_facade = external_facade or ExternalEnrichmentFacade()

    @staticmethod
    def _load_reference_labels(session: Session) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        tech_rows = session.execute(select(TechnologyArea.id, TechnologyArea.title)).all()
        technology_areas = [(str(r[0]), str(r[1])) for r in tech_rows]
        sec_rows = session.execute(select(IndustrySector.industry_sector_id, IndustrySector.sector_title)).all()
        industry_sectors = [(str(r[0]), str(r[1])) for r in sec_rows]
        return technology_areas, industry_sectors

    def _ensure_refs(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        if self._refs is not None:
            return self._refs
        if not _db_url_configured():
            self._refs = (list(FALLBACK_TECH_AREA_LABELS), [])
            return self._refs
        try:
            with session_scope() as session:
                self._refs = self._load_reference_labels(session)
        except Exception as exc:
            log.warning("enrichment_reference_load_failed", error=str(exc))
            self._refs = (list(FALLBACK_TECH_AREA_LABELS), [])
        return self._refs

    def health_check(self) -> dict:
        metrics: dict = {}
        if not _db_url_configured():
            return {
                "status": "degraded",
                "agent": self.agent_id,
                "last_run": None,
                "metrics": {**metrics, "reason": "PYTHON_DATABASE_URL not set"},
            }
        if check_db_connection():
            return {
                "status": "ok",
                "agent": self.agent_id,
                "last_run": None,
                "metrics": metrics,
            }
        return {
            "status": "down",
            "agent": self.agent_id,
            "last_run": None,
            "metrics": {**metrics, "reason": "database_unreachable"},
        }

    def _process_skills_extracted_batch(self, event: EventEnvelope) -> EventEnvelope:
        import json as _json
        from contextlib import nullcontext, suppress

        from common.llm_adapter import get_tracer

        payload = event.payload
        correlation_id = event.correlation_id
        batch_id = str(payload.get("batch_id") or "batch-unknown")

        tracer = get_tracer()
        rows = _records_from_skills_payload(payload)

        # Serialize input EventEnvelope summary for Langfuse trace visibility
        _input_str: str | None = None
        if tracer:
            with suppress(Exception):
                _input_str = _json.dumps(
                    {
                        "event_type": payload.get("event_type", "SkillsExtracted"),
                        "correlation_id": correlation_id,
                        "batch_id": batch_id,
                        "record_count": len(rows),
                    }
                )

        # --- Parallel fast path ---
        if self._enrichment_parallel_enabled() and len(rows) > 0:
            concurrency = self._enrichment_concurrency()
            log.info(
                "enrichment_batch_parallel_start",
                batch_id=batch_id,
                record_count=len(rows),
                concurrency=concurrency,
            )
            batch_start = time.perf_counter()
            parallel_results = self._enrich_batch_parallel_bridge(
                rows,
                payload,
                correlation_id=correlation_id,
                concurrency=concurrency,
            )
            if parallel_results:  # non-empty means parallel path succeeded
                enriched_count = 0
                spam_rejected_count = 0
                flagged_for_review_count = 0
                temporal_period_distribution: dict[str, int] = defaultdict(int)
                borderplex_subregion_distribution: dict[str, int] = defaultdict(int)
                duplicate_count = 0
                soc_classified_count = 0
                naics_classified_count = 0
                dedup_stub_count = 0
                dedup_rows_with_duplicate_cluster_id = 0
                dedup_rows_with_matched_job_posting_id = 0
                freshness_records: list[dict[str, Any]] = []

                for r in parallel_results:
                    if r is None or r.get("__error"):
                        continue
                    if r.get("__spam_bucket") == "rejected":
                        spam_rejected_count += 1
                        continue
                    if r.get("__spam_bucket") == "flagged":
                        flagged_for_review_count += 1
                        continue
                    enriched_count += 1
                    posting = r.get("__posting", {})
                    tp = _distribution_bucket(r.get("temporal_period", posting.get("temporal_period")))
                    temporal_period_distribution[tp] += 1
                    bp = _distribution_bucket(r.get("borderplex_subregion"))
                    borderplex_subregion_distribution[bp] += 1
                    if r.get("is_duplicate") is True:
                        duplicate_count += 1
                    soc_raw = r.get("soc_code") or posting.get("soc_code")
                    if soc_raw is not None and str(soc_raw).strip():
                        soc_classified_count += 1
                    naics_raw = r.get("naics_code") or posting.get("naics_code")
                    naics_st = str(naics_raw).strip() if naics_raw is not None else ""
                    if naics_st and naics_st.lower() != "unknown":
                        naics_classified_count += 1
                    ds, dc, dm = _rollup_fuzzy_dedup_signals(r, posting)
                    dedup_stub_count += ds
                    dedup_rows_with_duplicate_cluster_id += dc
                    dedup_rows_with_matched_job_posting_id += dm
                    freshness_records.append(build_freshness_record_for_analytics(posting, r))

                batch_duration_ms = int((time.perf_counter() - batch_start) * 1000)
                log.info(
                    "enrichment_batch_parallel_complete",
                    batch_id=batch_id,
                    enriched_count=enriched_count,
                    spam_rejected_count=spam_rejected_count,
                    flagged_for_review_count=flagged_for_review_count,
                    duration_ms=batch_duration_ms,
                    concurrency=concurrency,
                    execution_mode="parallel",
                )

                _check_soc_unclassified_rate(
                    soc_classified_count=soc_classified_count,
                    enriched_count=enriched_count,
                    correlation_id=correlation_id,
                    batch_id=batch_id,
                    triggered_by_event_type=payload.get("event_type"),
                )

                return build_record_enriched_event(
                    correlation_id=correlation_id,
                    batch_id=batch_id,
                    enriched_count=enriched_count,
                    spam_rejected_count=spam_rejected_count,
                    flagged_for_review_count=flagged_for_review_count,
                    temporal_period_distribution=dict(temporal_period_distribution),
                    borderplex_subregion_distribution=dict(borderplex_subregion_distribution),
                    duplicate_count=duplicate_count,
                    soc_classified_count=soc_classified_count,
                    naics_classified_count=naics_classified_count,
                    dedup_stub_count=dedup_stub_count,
                    dedup_rows_with_duplicate_cluster_id=dedup_rows_with_duplicate_cluster_id,
                    dedup_rows_with_matched_job_posting_id=dedup_rows_with_matched_job_posting_id,
                    freshness_records=freshness_records,
                )
            # else: parallel bridge returned empty (loop already running), fall through to serial

        # --- Serial fallback path (original) ---
        span_ctx = (
            tracer.start_span(
                "enrichment",
                correlation_id=correlation_id,
                input=_input_str,
                metadata={"batch_id": batch_id, "record_count": len(rows)},
            )
            if tracer
            else nullcontext()
        )
        enriched_count = 0
        spam_rejected_count = 0
        flagged_for_review_count = 0
        temporal_period_distribution: dict[str, int] = defaultdict(int)
        borderplex_subregion_distribution: dict[str, int] = defaultdict(int)
        duplicate_count = 0
        soc_classified_count = 0
        naics_classified_count = 0
        dedup_stub_count = 0
        dedup_rows_with_duplicate_cluster_id = 0
        dedup_rows_with_matched_job_posting_id = 0
        freshness_records: list[dict[str, Any]] = []

        def run_batch(session: Session | None) -> None:
            nonlocal enriched_count, spam_rejected_count, flagged_for_review_count
            nonlocal duplicate_count, soc_classified_count, naics_classified_count
            nonlocal dedup_stub_count, dedup_rows_with_duplicate_cluster_id
            nonlocal dedup_rows_with_matched_job_posting_id
            nonlocal freshness_records
            for idx, row in enumerate(rows):
                bucket = _spam_bucket(row)
                if bucket == "rejected":
                    spam_rejected_count += 1
                    continue
                if bucket == "flagged":
                    flagged_for_review_count += 1
                    continue

                posting = _posting_for_enrichment(row, payload)

                # Wrap each job's enrichment in a Langfuse span so NAICS/SOC/employer
                # classifier calls are grouped under the job title in the trace timeline.
                job_title = (posting.get("title") or "untitled")[:60]
                job_span_ctx = (
                    tracer.start_span(
                        f"enrich/{job_title}",
                        correlation_id=correlation_id,
                        input=_json.dumps(
                            {
                                "title": posting.get("title", ""),
                                "company": posting.get("company", ""),
                                "normalized_job_id": posting.get("normalized_job_id"),
                            }
                        ),
                        metadata={"idx": idx + 1, "total": len(rows)},
                    )
                    if tracer
                    else nullcontext()
                )

                with job_span_ctx:
                    try:
                        enriched = self.enrich_record(posting, session=session)
                        sector_id = resolve_sector(posting.get("role_classification"), session=session)
                        enriched["sector_id"] = sector_id

                        if enriched.get("spam_degraded") is True:
                            _emit_enrichment_degraded(
                                correlation_id=correlation_id,
                                posting_id=posting.get("posting_id"),
                                normalized_job_id=_coerce_normalized_job_id(row.get("normalized_job_id")),
                                triggered_by_event_type=payload.get("event_type"),
                                reason="spam_classifier_unavailable",
                                extraction_note=enriched.get("spam_extraction_note"),
                            )

                        # Score quality (deterministic — no LLM call)
                        extraction = build_extraction_dict(
                            row.get("skills"),
                            row.get("tools"),
                            row.get("tasks"),
                            row.get("responsibilities"),
                            row.get("context"),
                        )
                        q_res = score_quality(
                            job_title=posting.get("title") or "",
                            job_description=posting.get("description"),
                            extraction=extraction,
                            extraction_failed=bool(row.get("extraction_failed")),
                        )
                        enriched["quality_score"] = q_res.quality_score
                        enriched["quality_components"] = q_res.components

                        # Classify role + seniority (closes #282/#283 — was previously
                        # filled only by scripts/backfill_qna_columns.py post-hoc).
                        try:
                            tech_refs, sector_refs = self._ensure_refs()
                            role_cls, seniority_cls = classify_job(
                                posting.get("title") or "",
                                posting.get("description"),
                                extraction,
                                tech_refs,
                                sector_refs,
                                is_internship=bool(posting.get("is_internship", False)),
                            )
                            if role_cls and not enriched.get("role_classification"):
                                enriched["role_classification"] = role_cls
                            if seniority_cls and not enriched.get("seniority"):
                                enriched["seniority"] = seniority_cls
                        except Exception as cls_exc:  # noqa: BLE001
                            log.warning(
                                "enrichment_batch_classify_job_failed",
                                normalized_job_id=posting.get("normalized_job_id"),
                                error=str(cls_exc),
                            )

                        enriched_count += 1

                        tp = _distribution_bucket(enriched.get("temporal_period", posting.get("temporal_period")))
                        temporal_period_distribution[tp] += 1
                        bp = _distribution_bucket(enriched.get("borderplex_subregion"))
                        borderplex_subregion_distribution[bp] += 1
                        if enriched.get("is_duplicate") is True:
                            duplicate_count += 1
                        soc_raw = enriched.get("soc_code") or posting.get("soc_code")
                        if soc_raw is not None and str(soc_raw).strip():
                            soc_classified_count += 1
                        naics_raw = enriched.get("naics_code") or posting.get("naics_code")
                        naics_st = str(naics_raw).strip() if naics_raw is not None else ""
                        if naics_st and naics_st.lower() != "unknown":
                            naics_classified_count += 1
                        ds, dc, dm = _rollup_fuzzy_dedup_signals(enriched, posting)
                        dedup_stub_count += ds
                        dedup_rows_with_duplicate_cluster_id += dc
                        dedup_rows_with_matched_job_posting_id += dm
                        nj_promo = _coerce_normalized_job_id(
                            enriched.get("normalized_job_id") or posting.get("normalized_job_id")
                        )
                        # JIE #289: do NOT silently skip when nj_promo is None or
                        # session is None. Both produced 469 orphan rows over multiple
                        # batches before this fix. Log loud at ERROR so the bug is
                        # observable; sweeper picks up the row after the grace window.
                        if nj_promo is None:
                            log.error(
                                "promotion_skipped_missing_normalized_job_id",
                                source=posting.get("source"),
                                external_id=posting.get("external_id"),
                                call_site="batch_serial",
                                enriched_has_key="normalized_job_id" in enriched,
                                posting_has_key="normalized_job_id" in posting,
                            )
                        elif session is None:
                            log.error(
                                "promotion_skipped_session_unavailable",
                                normalized_job_id=nj_promo,
                                call_site="batch_serial",
                            )
                        else:
                            try:
                                apply_enrichment_to_job_postings(
                                    session,
                                    nj_promo,
                                    _job_postings_promotion_payload(enriched, posting),
                                )
                            except Exception as promo_exc:
                                log.warning(
                                    "enrichment_batch_job_posting_promotion_failed",
                                    normalized_job_id=nj_promo,
                                    error=str(promo_exc),
                                )

                        freshness_records.append(build_freshness_record_for_analytics(posting, enriched))
                    except Exception:
                        log.warning("enrichment_process_degraded", agent=self.agent_id)

        with span_ctx:
            if check_db_connection():
                try:
                    with session_scope() as db_session:
                        run_batch(db_session)
                except Exception as exc:
                    # JIE #289: this fall-through used to silently produce orphans
                    # for every row in the batch. Now it logs ERROR with the batch
                    # size so operators can correlate orphan spikes with session
                    # failures. The rows still get enriched (via run_batch(None))
                    # but promotion is skipped — sweeper picks them up after grace.
                    log.error(
                        "promotion_skipped_session_scope_failed",
                        agent=self.agent_id,
                        error=str(exc),
                        affected_records=len(rows) if rows else 0,
                    )
                    run_batch(None)
            else:
                # JIE #289: same observability concern when DB is unreachable.
                log.error(
                    "promotion_skipped_db_unavailable",
                    agent=self.agent_id,
                    affected_records=len(rows) if rows else 0,
                )
                run_batch(None)

            # Log enrichment output to Langfuse
            if tracer:
                with suppress(Exception):
                    total_processed = enriched_count + spam_rejected_count + flagged_for_review_count
                    tracer.log_event(
                        "enrichment_complete",
                        {
                            "output": _json.dumps(
                                {
                                    "event_type": "RecordEnriched",
                                    "batch_id": batch_id,
                                    "enriched_count": enriched_count,
                                    "spam_rejected_count": spam_rejected_count,
                                    "flagged_for_review_count": flagged_for_review_count,
                                    "soc_classified_count": soc_classified_count,
                                    "naics_classified_count": naics_classified_count,
                                    "duplicate_count": duplicate_count,
                                }
                            ),
                            "enriched_count": enriched_count,
                            "spam_rejected_count": spam_rejected_count,
                            "flagged_for_review_count": flagged_for_review_count,
                            "soc_classified_count": soc_classified_count,
                            "naics_classified_count": naics_classified_count,
                            "total_processed": total_processed,
                        },
                    )

            _check_soc_unclassified_rate(
                soc_classified_count=soc_classified_count,
                enriched_count=enriched_count,
                correlation_id=correlation_id,
                batch_id=batch_id,
                triggered_by_event_type=payload.get("event_type"),
            )

        return build_record_enriched_event(
            correlation_id=correlation_id,
            batch_id=batch_id,
            enriched_count=enriched_count,
            spam_rejected_count=spam_rejected_count,
            flagged_for_review_count=flagged_for_review_count,
            temporal_period_distribution=dict(temporal_period_distribution),
            borderplex_subregion_distribution=dict(borderplex_subregion_distribution),
            duplicate_count=duplicate_count,
            soc_classified_count=soc_classified_count,
            naics_classified_count=naics_classified_count,
            dedup_stub_count=dedup_stub_count,
            dedup_rows_with_duplicate_cluster_id=dedup_rows_with_duplicate_cluster_id,
            dedup_rows_with_matched_job_posting_id=dedup_rows_with_matched_job_posting_id,
            freshness_records=freshness_records,
        )

    def process(self, event: EventEnvelope) -> EventEnvelope:
        """
        Single-record path: ``RecordEnriched`` with classification, quality, spam preview.
        Includes ``temporal_period`` and ``borderplex_subregion`` when normalized-job context
        is available.

        Batch path (non-empty ``records``): one aggregate ``RecordEnriched`` via
        :func:`build_record_enriched_event`.
        """
        if not self._fixture:
            if _FIXTURE_PATH.exists():
                records = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
                self._fixture = {r["posting_id"]: r for r in records}
            else:
                self._fixture = {}

        raw_records = event.payload.get("records")
        if isinstance(raw_records, list) and len(raw_records) > 0:
            return self._process_skills_extracted_batch(event)

        posting_id = event.payload.get("posting_id")
        fx = self._fixture.get(posting_id, {})
        title = (event.payload.get("title") or fx.get("title") or "").strip()
        description = event.payload.get("description") or fx.get("description")
        company = event.payload.get("company") if event.payload.get("company") is not None else fx.get("company")

        tech, sectors = self._ensure_refs()

        nj_id = _coerce_normalized_job_id(event.payload.get("normalized_job_id"))
        desc_str = description if isinstance(description, str) else None

        ei_row: dict[str, Any] | None = None
        resolved_job_posting: dict[str, Any] | None = None
        ei_fetch_error = False
        if nj_id is not None and _db_url_configured():
            try:
                with session_scope() as session:
                    ei_row = session.execute(_LATEST_EI_BY_NJ_ID_SQL, {"nj_id": nj_id}).mappings().first()
                    resolved_job_posting = resolve_job_posting_row(session, nj_id)
            except Exception as exc:
                log.warning("enrichment_ei_load_failed", normalized_job_id=nj_id, error=str(exc))
                ei_fetch_error = True

        ext: dict[str, Any] | None = None
        extraction_failed = False
        if ei_row is not None:
            ext = build_extraction_dict(
                ei_row.get("skills"),
                ei_row.get("tools"),
                ei_row.get("tasks"),
                ei_row.get("responsibilities"),
                ei_row.get("context"),
            )
            extraction_failed = bool(ei_row.get("extraction_failed"))
        elif nj_id is None:
            ext = build_extraction_dict(
                event.payload.get("skills"),
                event.payload.get("tools"),
                [],
                [],
                [],
            )

        is_internship = bool(event.payload.get("is_internship", False))

        role_classification, seniority = classify_job(
            title,
            desc_str,
            ext,
            tech,
            sectors,
            is_internship=is_internship,
        )

        quality_res = score_quality(
            job_title=title,
            job_description=desc_str,
            extraction=ext,
            extraction_failed=extraction_failed,
        )
        quality_score = quality_res.quality_score
        quality_components = quality_res.components

        spam_block: dict[str, Any] = {}
        spam_result: SpamPreviewResult | None = None
        degraded_reason: str | None = None
        if nj_id is not None and _db_url_configured():
            if ei_fetch_error:
                spam_result = _degraded_spam_result(extraction_note=None)
                degraded_reason = "extracted_intelligence_unavailable"
                spam_block = _spam_from_preview_result(spam_result)
            elif ei_row is not None:
                extraction_empty = ext is None
                spam_result = score_spam_preview(
                    job_title=title,
                    job_description=desc_str,
                    extraction=ext or {},
                    extraction_failed=extraction_failed,
                    extraction_empty=extraction_empty,
                )
                if spam_result.degraded:
                    degraded_reason = "spam_classifier_unavailable"
                spam_block = _spam_from_preview_result(spam_result)
            else:
                spam_result = score_spam_preview(
                    job_title=title,
                    job_description=desc_str,
                    extraction={},
                    extraction_failed=False,
                    extraction_empty=True,
                )
                if spam_result.degraded:
                    degraded_reason = "spam_classifier_unavailable"
                spam_block = _spam_from_preview_result(spam_result)
            payload_spam_score = spam_block["spam_score"]
            payload_is_spam = spam_block["is_spam"]
        else:
            payload_spam_score = fx.get("spam_score")
            payload_is_spam = fx.get("is_spam")

        derived_output_fields = derive_enrichment_output_fields(resolved_job_posting)
        enriched_job_profile = EnrichedJobProfile(
            job_record={
                "posting_id": posting_id,
                "normalized_job_id": nj_id,
                "title": title or fx.get("title"),
                "company": company,
                "skills": event.payload.get("skills", []),
            },
            temporal_period=derived_output_fields.get("temporal_period"),
            borderplex_subregion=derived_output_fields.get("borderplex_subregion"),
        )
        pair_a_profile_fields = {
            "temporal_period": enriched_job_profile.temporal_period,
            "borderplex_subregion": enriched_job_profile.borderplex_subregion,
        }

        resolved_company_id: str | None = None
        if resolved_job_posting and resolved_job_posting.get("company_id") is not None:
            rc = str(resolved_job_posting["company_id"]).strip()
            resolved_company_id = rc or None
        fixture_company_id = fx.get("company_id")
        if fixture_company_id is not None and not isinstance(fixture_company_id, str):
            fixture_company_id = str(fixture_company_id).strip() or None
        elif isinstance(fixture_company_id, str):
            fixture_company_id = fixture_company_id.strip() or None
        effective_company_id = resolved_company_id or fixture_company_id

        base_payload: dict[str, Any] = {
            "event_type": "RecordEnriched",
            "posting_id": posting_id,
            "title": title or fx.get("title"),
            "company": company,
            "company_id": effective_company_id,
            "sector_id": fx.get("sector_id"),
            "role_classification": role_classification,
            "seniority": seniority,
            "quality_score": quality_score,
            "quality_components": quality_components,
            "spam_score": payload_spam_score,
            "is_spam": payload_is_spam,
            "enrichment_status": fx.get("enrichment_status", "success"),
            "skills": event.payload.get("skills", []),
            **pair_a_profile_fields,
        }
        if nj_id is not None:
            base_payload["normalized_job_id"] = nj_id
        if spam_block:
            base_payload.update(spam_block)
        if degraded_reason is not None and spam_result is not None:
            _emit_enrichment_degraded(
                correlation_id=event.correlation_id,
                posting_id=posting_id,
                normalized_job_id=nj_id,
                triggered_by_event_type=event.payload.get("event_type"),
                reason=degraded_reason,
                extraction_note=spam_result.extraction_note,
            )

        # JIE #289: log loud at ERROR when nj_id is None on the single-record path.
        # Unlike the batch paths, this path skips DB writes entirely when nj_id is
        # missing — the sweeper cannot rescue these (no normalized_jobs row was
        # ever persisted via this code path), so the goal here is observability
        # for upstream callers that drop normalized_job_id from the payload.
        if nj_id is None and _db_url_configured():
            log.error(
                "promotion_skipped_missing_normalized_job_id",
                source=event.payload.get("source"),
                external_id=event.payload.get("external_id"),
                call_site="single_record",
                payload_has_key="normalized_job_id" in event.payload,
            )

        if nj_id is not None and _db_url_configured():
            try:
                with session_scope() as session:
                    raw_naics = classify_naics(title, desc_str, session)
                    base_payload["naics_code"] = (raw_naics or "unknown").strip() or "unknown"
                    try:
                        raw_soc = run_coroutine(
                            classify_soc(
                                title or "",
                                desc_str or "",
                                session,
                                _enrichment_soc_llm(),
                            )
                        )
                        base_payload["soc_code"] = None if raw_soc == "unclassified" else raw_soc
                        _soc = base_payload["soc_code"]
                        oc_value = _soc[:20] if _soc else None
                        session.execute(
                            update(NormalizedJob).where(NormalizedJob.id == nj_id).values(occupation_code=oc_value)
                        )
                    except Exception as soc_exc:
                        log.warning(
                            "enrichment_soc_failed",
                            normalized_job_id=nj_id,
                            error=str(soc_exc),
                        )
                        base_payload["soc_code"] = None
                    ep = build_employer_profile(desc_str, str(company or ""), session)
                    base_payload["employer_metadata"] = ep.model_dump(mode="json")
                    persist_employer_metadata(
                        session,
                        ep,
                        company_id=effective_company_id,
                        normalized_job_id=nj_id,
                        source=event.payload.get("source"),
                        external_id=event.payload.get("external_id"),
                    )
                    apply_enrichment_to_job_postings(session, nj_id, base_payload)
            except Exception as exc:
                log.warning(
                    "enrichment_promotion_failed",
                    normalized_job_id=nj_id,
                    error=str(exc),
                )

        return EventEnvelope(
            correlation_id=event.correlation_id,
            agent_id=self.agent_id,
            payload=base_payload,
        )

    def enrich_record(
        self,
        posting: dict[str, Any],
        session: Session | None,
    ) -> dict[str, Any]:
        try:
            company_id, company_confidence = resolve_company(posting.get("company") or "", session)
            location_id, location_confidence, raw_location_text, borderplex_subregion = resolve_location(
                posting.get("location", ""), session
            )
            field_confidence = compute_field_confidence(
                company_confidence,
                location_confidence,
                sector_id=None,
                seniority_confidence=posting.get("seniority_confidence"),
            )
            overall_confidence = compute_overall_confidence(
                field_confidence=field_confidence,
                extraction_confidence=posting.get("extraction_confidence"),
                quality_score=posting.get("quality_score"),
                taxonomy_coverage=posting.get("taxonomy_coverage"),
            )
            merged: dict[str, Any] = {
                **posting,
                "company_id": company_id,
                "location_id": location_id,
                "raw_location_text": raw_location_text,
                "borderplex_subregion": borderplex_subregion,
                "field_confidence": field_confidence,
                "overall_confidence": overall_confidence,
            }
            try:
                ext = run_coroutine(self._external_facade.fetch_for_posting(merged))
                merged.update(ext)
            except RuntimeError as re_exc:
                log.warning(
                    "enrichment_external_adapters_skipped",
                    reason=str(re_exc),
                )
            except Exception as ext_exc:  # noqa: BLE001
                log.warning(
                    "enrichment_external_adapters_failed",
                    error=str(ext_exc),
                )
            if session is not None:
                desc_raw = posting.get("description")
                desc_str = desc_raw if isinstance(desc_raw, str) else None
                try:
                    raw_naics = classify_naics(posting.get("title") or "", desc_str, session)
                    merged["naics_code"] = (raw_naics or "unknown").strip() or "unknown"
                except Exception as naics_exc:
                    log.warning("enrich_record_naics_failed", error=str(naics_exc))
                    merged["naics_code"] = posting.get("naics_code")
                try:
                    raw_soc = run_coroutine(
                        classify_soc(
                            posting.get("title") or "",
                            desc_str or "",
                            session,
                            _enrichment_soc_llm(),
                        )
                    )
                    if raw_soc == "unclassified":
                        # Individual-record warning; batch-level rate check fires separately
                        # in _check_soc_unclassified_rate after the full batch completes.
                        log.warning(
                            "enrich_record_soc_unclassified",
                            title=(posting.get("title") or "")[:200],
                            normalized_job_id=posting.get("normalized_job_id"),
                        )
                        merged["soc_code"] = None
                    else:
                        merged["soc_code"] = raw_soc
                except Exception as soc_exc:
                    log.warning("enrich_record_soc_failed", error=str(soc_exc))
                    merged["soc_code"] = posting.get("soc_code")
                else:
                    nj_soc = _coerce_normalized_job_id(posting.get("normalized_job_id"))
                    sc = merged.get("soc_code")
                    if nj_soc is not None and isinstance(sc, str) and sc.strip():
                        try:
                            session.execute(
                                update(NormalizedJob)
                                .where(NormalizedJob.id == nj_soc)
                                .values(occupation_code=sc.strip()[:20])
                            )
                        except Exception as oc_exc:
                            log.warning(
                                "enrich_record_occupation_code_persist_failed",
                                error=str(oc_exc),
                            )
                try:
                    ep = build_employer_profile(desc_str, posting.get("company") or "", session)
                    merged["employer_metadata"] = ep.model_dump(mode="json")
                    cid_raw = merged.get("company_id")
                    persist_company_id = str(cid_raw).strip() if cid_raw is not None and str(cid_raw).strip() else None
                    persist_employer_metadata(
                        session,
                        ep,
                        company_id=persist_company_id,
                        normalized_job_id=_coerce_normalized_job_id(posting.get("normalized_job_id")),
                        source=posting.get("source"),
                        external_id=posting.get("external_id"),
                    )
                except Exception as emp_exc:
                    log.warning("enrich_record_employer_failed", error=str(emp_exc))
                    merged["employer_metadata"] = EmployerProfile().model_dump(mode="json")
            return merged
        except Exception:
            log.warning(
                "enrich_record_degraded",
                agent=self.agent_id,
                reason="resolver_exception",
            )
            return {
                **posting,
                "company_id": None,
                "location_id": None,
                "raw_location_text": None,
                "borderplex_subregion": None,
                "naics_code": posting.get("naics_code"),
                "employer_metadata": EmployerProfile().model_dump(mode="json"),
                "field_confidence": {
                    "company_id": 0.0,
                    "location_id": 0.0,
                    "sector_id": 0.0,
                    "seniority": 0.0,
                },
                "overall_confidence": 0.0,
                "enrichment_status": "degraded",
            }

    async def enrich_record_async(
        self,
        posting: dict[str, Any],
        session: Session | None,
    ) -> dict[str, Any]:
        """Async counterpart to :meth:`enrich_record` — runs SOC, NAICS, employer concurrently."""
        try:
            company_id, company_confidence = resolve_company(posting.get("company") or "", session)
            location_id, location_confidence, raw_location_text, borderplex_subregion = resolve_location(
                posting.get("location", ""), session
            )
            field_confidence = compute_field_confidence(
                company_confidence,
                location_confidence,
                sector_id=None,
                seniority_confidence=posting.get("seniority_confidence"),
            )
            overall_confidence = compute_overall_confidence(
                field_confidence=field_confidence,
                extraction_confidence=posting.get("extraction_confidence"),
                quality_score=posting.get("quality_score"),
                taxonomy_coverage=posting.get("taxonomy_coverage"),
            )
            merged: dict[str, Any] = {
                **posting,
                "company_id": company_id,
                "location_id": location_id,
                "raw_location_text": raw_location_text,
                "borderplex_subregion": borderplex_subregion,
                "field_confidence": field_confidence,
                "overall_confidence": overall_confidence,
            }
            # External adapters (Census, BLS, O*NET) — run if available
            try:
                ext = await self._external_facade.fetch_for_posting(merged)
                merged.update(ext)
            except Exception as ext_exc:
                log.warning("enrichment_external_adapters_failed", error=str(ext_exc))

            if session is not None:
                desc_raw = posting.get("description")
                desc_str = desc_raw if isinstance(desc_raw, str) else None
                title = posting.get("title") or ""
                company = posting.get("company") or ""

                # Run SOC, NAICS, employer LLM calls concurrently with per-call timeout
                from enrichment._config import enrichment_llm_timeout_seconds

                _ENRICH_LLM_TIMEOUT = enrichment_llm_timeout_seconds()
                _nj_id = posting.get("normalized_job_id")
                _gather_start = time.perf_counter()

                try:
                    naics_result, soc_result, employer_result = await asyncio.wait_for(
                        asyncio.gather(
                            classify_naics_async(title, desc_str, session),
                            classify_soc(
                                title,
                                desc_str or "",
                                session,
                                _enrichment_soc_llm(),
                                async_llm=_enrichment_soc_llm_async(),
                            ),
                            build_employer_profile_async(desc_str, company, session),
                            return_exceptions=True,
                        ),
                        timeout=_ENRICH_LLM_TIMEOUT,
                    )
                except TimeoutError:
                    _elapsed = int((time.perf_counter() - _gather_start) * 1000)
                    log.error(
                        "enrich_record_async_gather_timeout",
                        normalized_job_id=_nj_id,
                        timeout_s=_ENRICH_LLM_TIMEOUT,
                        elapsed_ms=_elapsed,
                    )
                    naics_result = TimeoutError(f"gather timeout after {_ENRICH_LLM_TIMEOUT}s")
                    soc_result = TimeoutError(f"gather timeout after {_ENRICH_LLM_TIMEOUT}s")
                    employer_result = TimeoutError(f"gather timeout after {_ENRICH_LLM_TIMEOUT}s")

                _gather_ms = int((time.perf_counter() - _gather_start) * 1000)
                log.debug(
                    "enrich_record_async_gather_complete",
                    normalized_job_id=_nj_id,
                    gather_ms=_gather_ms,
                    naics_ok=not isinstance(naics_result, Exception),
                    soc_ok=not isinstance(soc_result, Exception),
                    employer_ok=not isinstance(employer_result, Exception),
                )

                # NAICS
                if isinstance(naics_result, Exception):
                    log.warning("enrich_record_async_naics_failed", normalized_job_id=_nj_id, error=str(naics_result))
                    merged["naics_code"] = posting.get("naics_code")
                else:
                    merged["naics_code"] = (naics_result or "unknown").strip() or "unknown"

                # SOC
                if isinstance(soc_result, Exception):
                    log.warning("enrich_record_async_soc_failed", normalized_job_id=_nj_id, error=str(soc_result))
                    merged["soc_code"] = posting.get("soc_code")
                else:
                    merged["soc_code"] = None if soc_result == "unclassified" else soc_result
                    nj_soc = _coerce_normalized_job_id(posting.get("normalized_job_id"))
                    sc = merged.get("soc_code")
                    if nj_soc is not None and isinstance(sc, str) and sc.strip():
                        try:
                            from sqlalchemy import update as sa_update

                            session.execute(
                                sa_update(NormalizedJob)
                                .where(NormalizedJob.id == nj_soc)
                                .values(occupation_code=sc.strip()[:20])
                            )
                        except Exception as oc_exc:
                            log.warning("enrich_record_occupation_code_persist_failed", error=str(oc_exc))

                # Employer
                if isinstance(employer_result, Exception):
                    log.warning("enrich_record_async_employer_failed", error=str(employer_result))
                    merged["employer_metadata"] = EmployerProfile().model_dump(mode="json")
                else:
                    merged["employer_metadata"] = employer_result.model_dump(mode="json")
                    cid_raw = merged.get("company_id")
                    persist_company_id = str(cid_raw).strip() if cid_raw is not None and str(cid_raw).strip() else None
                    try:
                        persist_employer_metadata(
                            session,
                            employer_result,
                            company_id=persist_company_id,
                            normalized_job_id=_coerce_normalized_job_id(posting.get("normalized_job_id")),
                            source=posting.get("source"),
                            external_id=posting.get("external_id"),
                        )
                    except Exception as emp_exc:
                        log.warning("enrich_record_employer_persist_failed", error=str(emp_exc))

            # Classify role + seniority (closes #282/#283; mirrors the serial
            # path in _process_skills_extracted_batch). Without this, the
            # parallel path leaves role_classification + seniority_level NULL.
            try:
                from scripts.jsearch_enrichment_preview_lib import build_extraction_dict

                tech_refs, sector_refs = self._ensure_refs()
                extraction_dict = build_extraction_dict(
                    posting.get("skills"),
                    posting.get("tools"),
                    posting.get("tasks"),
                    posting.get("responsibilities"),
                    posting.get("context"),
                )
                role_cls, seniority_cls = classify_job(
                    posting.get("title") or "",
                    posting.get("description"),
                    extraction_dict,
                    tech_refs,
                    sector_refs,
                    is_internship=bool(posting.get("is_internship", False)),
                )
                if role_cls and not merged.get("role_classification"):
                    merged["role_classification"] = role_cls
                if seniority_cls and not merged.get("seniority"):
                    merged["seniority"] = seniority_cls
            except Exception as cls_exc:  # noqa: BLE001
                log.warning(
                    "enrich_record_async_classify_job_failed",
                    normalized_job_id=posting.get("normalized_job_id"),
                    error=str(cls_exc),
                )

            return merged
        except Exception:
            log.warning("enrich_record_async_degraded", agent=self.agent_id, reason="resolver_exception")
            return {
                **posting,
                "company_id": None,
                "location_id": None,
                "raw_location_text": None,
                "borderplex_subregion": None,
                "naics_code": posting.get("naics_code"),
                "employer_metadata": EmployerProfile().model_dump(mode="json"),
                "field_confidence": {"company_id": 0.0, "location_id": 0.0, "sector_id": 0.0, "seniority": 0.0},
                "overall_confidence": 0.0,
                "enrichment_status": "degraded",
            }

    # ------------------------------------------------------------------
    # Parallel batch enrichment (mirrors skills_extraction pattern)
    # ------------------------------------------------------------------

    def _enrichment_parallel_enabled(self) -> bool:
        from enrichment._config import enrichment_parallel

        return enrichment_parallel()

    def _enrichment_concurrency(self) -> int:
        from enrichment._config import enrichment_concurrency

        return enrichment_concurrency()

    async def _enrich_batch_parallel(
        self,
        rows: list[dict[str, Any]],
        payload: dict[str, Any],
        *,
        correlation_id: str,
        concurrency: int,
    ) -> list[dict[str, Any]]:
        """Run enrichment across jobs concurrently with a semaphore cap."""
        semaphore = asyncio.Semaphore(concurrency)
        _in_flight = 0
        _peak_in_flight = 0
        _saturation_events = 0

        _completed = 0
        _total = len(rows)

        async def _enrich_one(idx: int, row: dict[str, Any]) -> dict[str, Any] | None:
            nonlocal _in_flight, _peak_in_flight, _saturation_events, _completed
            bucket = _spam_bucket(row)
            if bucket == "rejected":
                _completed += 1
                return {"__spam_bucket": "rejected"}
            if bucket == "flagged":
                _completed += 1
                return {"__spam_bucket": "flagged"}

            if semaphore.locked():
                _saturation_events += 1
            async with semaphore:
                _in_flight += 1
                _peak_in_flight = max(_peak_in_flight, _in_flight)
                job_start = time.perf_counter()
                posting = _posting_for_enrichment(row, payload)
                try:
                    with session_scope() as job_session:
                        enriched = await self.enrich_record_async(posting, job_session)
                        sector_id = resolve_sector(posting.get("role_classification"), session=job_session)
                        enriched["sector_id"] = sector_id

                        # Quality score (deterministic — no LLM call)
                        extraction = build_extraction_dict(
                            row.get("skills"),
                            row.get("tools"),
                            row.get("tasks"),
                            row.get("responsibilities"),
                            row.get("context"),
                        )
                        q_res = score_quality(
                            job_title=posting.get("title") or "",
                            job_description=posting.get("description"),
                            extraction=extraction,
                            extraction_failed=bool(row.get("extraction_failed")),
                        )
                        enriched["quality_score"] = q_res.quality_score
                        enriched["quality_components"] = q_res.components

                        # Promotion
                        nj_promo = _coerce_normalized_job_id(
                            enriched.get("normalized_job_id") or posting.get("normalized_job_id")
                        )
                        # JIE #289: log loud at ERROR when nj_promo is None so the bug
                        # is observable. Sweeper picks up the orphaned row after grace.
                        if nj_promo is None:
                            log.error(
                                "promotion_skipped_missing_normalized_job_id",
                                source=posting.get("source"),
                                external_id=posting.get("external_id"),
                                call_site="batch_parallel",
                                enriched_has_key="normalized_job_id" in enriched,
                                posting_has_key="normalized_job_id" in posting,
                            )
                        else:
                            try:
                                apply_enrichment_to_job_postings(
                                    job_session,
                                    nj_promo,
                                    _job_postings_promotion_payload(enriched, posting),
                                )
                            except Exception as promo_exc:
                                log.warning("enrichment_parallel_promotion_failed", error=str(promo_exc))

                        enriched["__posting"] = posting
                        enriched["__row"] = row
                except Exception as exc:
                    log.warning("enrichment_parallel_job_failed", idx=idx, error=str(exc))
                    enriched = {"__error": True, "__posting": posting, "__row": row}
                finally:
                    _in_flight -= 1
                    _completed += 1
                    job_ms = int((time.perf_counter() - job_start) * 1000)
                    if job_ms > 60_000:
                        log.warning(
                            "enrichment_slow_job",
                            idx=idx,
                            completed=_completed,
                            total=_total,
                            job_ms=job_ms,
                            in_flight=_in_flight,
                        )
                    if _completed % 10 == 0 or _completed == _total:
                        log.info(
                            "enrichment_progress",
                            completed=_completed,
                            total=_total,
                            in_flight=_in_flight,
                            job_ms=job_ms,
                        )
                return enriched

        results = list(await asyncio.gather(*[_enrich_one(i, row) for i, row in enumerate(rows)]))

        if _saturation_events > 0:
            log.info(
                "enrichment_semaphore_saturation_summary",
                concurrency=concurrency,
                total_jobs=len(rows),
                saturation_events=_saturation_events,
                peak_in_flight=_peak_in_flight,
            )
        return results

    def _enrich_batch_parallel_bridge(
        self,
        rows: list[dict[str, Any]],
        payload: dict[str, Any],
        *,
        correlation_id: str,
        concurrency: int,
    ) -> list[dict[str, Any]]:
        """Sync-to-async bridge for parallel enrichment (mirrors skills extraction pattern)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self._enrich_batch_parallel(
                    rows,
                    payload,
                    correlation_id=correlation_id,
                    concurrency=concurrency,
                )
            )
        log.warning(
            "enrichment_parallel_fallback_serial",
            reason="event_loop_running",
            total_jobs=len(rows),
        )
        return []  # caller falls back to serial path

    def run_cli_preview(self, limit: int) -> None:
        """Load jobs from DB and print role + seniority (stdout)."""
        _load_repo_dotenv()
        if not _db_url_configured():
            print("PYTHON_DATABASE_URL is required for CLI mode.", file=sys.stderr)  # noqa: T201
            sys.exit(1)
        with session_scope() as session:
            technology_areas, industry_sectors = self._load_reference_labels(session)
            log.info(
                "enrichment_cli_start",
                limit=limit,
                tech_areas=len(technology_areas),
                sectors=len(industry_sectors),
            )
            rows = session.execute(_LATEST_EI_SQL, {"lim": limit}).mappings().all()

        for row in rows:
            extraction = {
                "skills": row.get("skills"),
                "tools": row.get("tools"),
                "tasks": row.get("tasks"),
                "responsibilities": row.get("responsibilities"),
                "context": row.get("context"),
            }
            role, seniority = classify_job(
                row.get("job_title") or "",
                row.get("job_description"),
                extraction,
                technology_areas,
                industry_sectors,
                is_internship=bool(row.get("is_internship")),
            )
            jid = row.get("job_posting_id") or ""
            src = row.get("source") or ""
            ext_id = row.get("external_id") or ""
            line = (
                f"job_posting_id={jid}\tsource={src}\texternal_id={ext_id}\t"
                f"seniority={seniority}\trole_classification={role}"
            )
            print(line)  # noqa: T201


def main() -> None:
    _load_repo_dotenv()
    parser = argparse.ArgumentParser(description="Print deterministic enrichment labels per job.")
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max rows from job_postings to process (default 50).",
    )
    args = parser.parse_args()
    EnrichmentAgent().run_cli_preview(limit=args.limit)


if __name__ == "__main__":
    main()
