"""Truth layer: deterministic evidence from router output."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from numbers import Real
from typing import Any

from analytics.query_engine.constants import (
    CONFIDENCE_TRANSPARENCY_THRESHOLD,
    VOLUME_WARNING_POSTING_THRESHOLD,
)
from analytics.query_engine.schemas import (
    DataSufficiency,
    EvidenceBundle,
    EvidenceCitation,
    QueryResultPayload,
)

_COUNT_KEYS = (
    "posting_count",
    "postings_count",
    "job_posting_count",
    "job_postings_count",
    "total_postings",
    "total_posting_count",
    "job_count",
    "jobs_count",
    "total_jobs",
    "total_job_count",
)
_PERIOD_KEYS = (
    "time_period",
    "period",
    "period_label",
    "quarter",
    "month",
    "week",
    "year",
    "date",
)
_PERIOD_START_KEYS = ("period_start", "start_date", "date_from", "from_date")
_PERIOD_END_KEYS = ("period_end", "end_date", "date_to", "to_date")
_MAX_FACT_FIELDS = 5
_SALARY_TOKENS = ("salary", "wage", "compensation", "p25", "p50", "p75", "p95")


def _normalize_key(value: str) -> str:
    return re.sub(r"_+", "_", value.strip().lower().replace("-", "_").replace(" ", "_"))


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(float(value))


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if _is_number(value):
        number = float(value)
        if number.is_integer():
            return f"{int(number):,}"
        return f"{number:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return ", ".join(f"{key}={_format_value(val)}" for key, val in list(value.items())[:3])
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        rendered = [_format_value(item) for item in list(value)[:3]]
        return ", ".join(rendered)
    return str(value)


def _pretty_key(key: str) -> str:
    return _normalize_key(key).replace("_", " ")


def _ordered_row_keys(payload: QueryResultPayload, row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in payload.columns:
        if key in row and not _is_empty(row.get(key)):
            keys.append(key)
    for key in row:
        if key not in keys and not _is_empty(row.get(key)):
            keys.append(key)
    return keys


def _extract_count_value(row: dict[str, Any]) -> int | None:
    normalized = {_normalize_key(key): key for key in row}
    for count_key in _COUNT_KEYS:
        actual = normalized.get(count_key)
        if actual is None:
            continue
        value = row.get(actual)
        if _is_number(value) and float(value) >= 0:
            return int(float(value))
    for key, value in row.items():
        normalized_key = _normalize_key(key)
        if normalized_key.endswith("_count") and _is_number(value) and float(value) >= 0:
            return int(float(value))
    return None


def _volume_posting_count(payload: QueryResultPayload) -> int | None:
    """Derive canonical posting volume for policy.

    If ``distinct_posting_count`` is set by the router (e.g. COUNT(DISTINCT job_posting_id)),
    use it. Otherwise, with a single row use that row's count; with multiple rows use **max**
    (conservative lower bound when rows may overlap the same postings, e.g. multi-bucket rollups).
    Summing per-row counts can double-count postings across buckets — avoid unless router sets
    ``distinct_posting_count``.
    """
    if payload.distinct_posting_count is not None:
        return payload.distinct_posting_count
    counts = [count for row in payload.rows if (count := _extract_count_value(row)) is not None]
    if not counts:
        return None
    if len(counts) == 1:
        return counts[0]
    return max(counts)


def _extract_row_period(row: dict[str, Any]) -> str | None:
    normalized = {_normalize_key(key): key for key in row}

    start = next(
        (
            row[actual]
            for key in _PERIOD_START_KEYS
            if (actual := normalized.get(key)) is not None and not _is_empty(row.get(actual))
        ),
        None,
    )
    end = next(
        (
            row[actual]
            for key in _PERIOD_END_KEYS
            if (actual := normalized.get(key)) is not None and not _is_empty(row.get(actual))
        ),
        None,
    )
    if start is not None or end is not None:
        if start is not None and end is not None:
            return f"{_format_value(start)} to {_format_value(end)}"
        return _format_value(start if start is not None else end)

    for period_key in _PERIOD_KEYS:
        actual = normalized.get(period_key)
        if actual is None or _is_empty(row.get(actual)):
            continue
        return _format_value(row[actual])
    return None


def _period_coverage(payload: QueryResultPayload) -> tuple[str, bool]:
    periods = [period for row in payload.rows if (period := _extract_row_period(row))]
    if not periods:
        return "period unknown", False

    unique_periods = list(dict.fromkeys(periods))
    partial_period_coverage = len(periods) < len(payload.rows)
    if len(unique_periods) == 1:
        coverage = unique_periods[0]
    elif len(unique_periods) <= 4:
        coverage = ", ".join(unique_periods)
    else:
        coverage = f"{unique_periods[0]} to {unique_periods[-1]} ({len(unique_periods)} periods)"

    if partial_period_coverage:
        coverage = f"{coverage} (partial period coverage)"
    return coverage, partial_period_coverage


def _source_table(payload: QueryResultPayload) -> str | None:
    if not payload.tables_referenced:
        return None
    if len(payload.tables_referenced) == 1:
        return payload.tables_referenced[0]
    return ", ".join(sorted(payload.tables_referenced))


def _row_summary(payload: QueryResultPayload, row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in _ordered_row_keys(payload, row)[:_MAX_FACT_FIELDS]:
        value = row.get(key)
        if _is_empty(value):
            continue
        parts.append(f"{_pretty_key(key)}={_format_value(value)}")
    return "; ".join(parts) + "." if parts else "Returned row contained no usable values."


def _build_citations(
    payload: QueryResultPayload,
    *,
    source_table: str | None,
    period_coverage: str,
) -> list[EvidenceCitation]:
    citations: list[EvidenceCitation] = []
    for index, row in enumerate(payload.rows, start=1):
        row_period = _extract_row_period(row)
        citations.append(
            EvidenceCitation(
                citation_id=f"c{index}",
                summary=_row_summary(payload, row),
                source_table=source_table,
                supporting_count=_extract_count_value(row),
                time_period=row_period or (None if period_coverage == "period unknown" else period_coverage),
            )
        )

    if payload.result_truncated:
        citations.append(
            EvidenceCitation(
                citation_id=f"c{len(citations) + 1}",
                summary=(
                    "Query results were truncated by SQL guardrails; conclusions must be limited "
                    f"to the {max(payload.row_count_returned, len(payload.rows))} returned rows."
                ),
                source_table=source_table,
                supporting_count=None,
                time_period=None if period_coverage == "period unknown" else period_coverage,
            )
        )
    return citations


def _available_metric_keys(payload: QueryResultPayload) -> set[str]:
    keys = set(payload.columns)
    for row in payload.rows:
        keys.update(row.keys())
    return {_normalize_key(key) for key in keys}


def _has_salary_metric(payload: QueryResultPayload) -> bool:
    for row in payload.rows:
        for key, value in row.items():
            normalized = _normalize_key(key)
            if any(token in normalized for token in _SALARY_TOKENS) and not _is_empty(value):
                return True
    return False


def _structural_gaps(payload: QueryResultPayload) -> list[str]:
    gaps: list[str] = []
    for column in payload.columns:
        if all(column not in row for row in payload.rows):
            gaps.append(column)
            continue
        if all(_is_empty(row.get(column)) for row in payload.rows):
            gaps.append(column)
    return gaps


def _completeness_score(
    *,
    payload: QueryResultPayload,
    structural_gaps: list[str],
    partial_period_coverage: bool,
    period_coverage: str,
) -> float:
    score = 1.0
    if structural_gaps:
        score -= 0.25
    if period_coverage == "period unknown":
        score -= 0.1
    if partial_period_coverage:
        score -= 0.15
    if payload.result_truncated:
        score -= 0.1
    return max(0.0, round(score, 3))


def _volume_score(volume_posting_count: int | None) -> float:
    if volume_posting_count is None:
        return 0.65
    if volume_posting_count <= 0:
        return 0.0
    if volume_posting_count < 10:
        return 0.2
    if volume_posting_count < VOLUME_WARNING_POSTING_THRESHOLD:
        return 0.5
    return 1.0


def _sufficiency(volume_posting_count: int | None) -> DataSufficiency:
    if volume_posting_count is None:
        return DataSufficiency.SPARSE
    if volume_posting_count <= 0:
        return DataSufficiency.NO_DATA
    if volume_posting_count < VOLUME_WARNING_POSTING_THRESHOLD:
        return DataSufficiency.SPARSE
    return DataSufficiency.ADEQUATE


def _blend_confidence(
    *,
    classification_confidence: float,
    volume_posting_count: int | None,
    completeness_score: float,
) -> float:
    blended = 0.6 * classification_confidence + 0.25 * _volume_score(volume_posting_count) + 0.15 * completeness_score
    if classification_confidence < CONFIDENCE_TRANSPARENCY_THRESHOLD:
        blended = min(blended, classification_confidence)
    return round(max(0.0, min(1.0, blended)), 3)


def _confidence_explanation(
    *,
    payload: QueryResultPayload,
    period_coverage: str,
    partial_period_coverage: bool,
    volume_posting_count: int | None,
    structural_gaps: list[str],
    refusal_reason: str | None,
) -> str:
    reasons = [f"Intent classification confidence is {payload.classification_confidence:.2f}."]

    if payload.router_error:
        reasons.append("The routed SQL step failed, so no evidence bundle could be grounded.")
    elif volume_posting_count is None:
        reasons.append(
            "The returned rows did not expose an explicit posting count, so sample size could not be verified."
        )
    elif volume_posting_count == 0:
        reasons.append("No rows matched the current filters, so there is no in-scope data to synthesize.")
    elif volume_posting_count < VOLUME_WARNING_POSTING_THRESHOLD:
        reasons.append(
            f"The answer is based on {volume_posting_count} postings, below the "
            f"{VOLUME_WARNING_POSTING_THRESHOLD}-posting transparency threshold."
        )
    else:
        reasons.append(f"The answer is supported by {volume_posting_count} postings in scope.")

    if payload.classification_confidence < CONFIDENCE_TRANSPARENCY_THRESHOLD:
        reasons.append(
            f"Classification confidence is below the {CONFIDENCE_TRANSPARENCY_THRESHOLD:.1f} transparency threshold."
        )

    if period_coverage == "period unknown":
        reasons.append("The result did not include an explicit time period.")
    elif partial_period_coverage:
        reasons.append(
            "Some rows were missing period values, so the reported period coverage is partial period coverage."
        )

    if structural_gaps:
        preview = ", ".join(structural_gaps[:3])
        reasons.append(f"Some expected columns were missing or empty in the returned rows ({preview}).")

    if payload.result_truncated:
        reasons.append(
            f"The SQL guardrails truncated the result set to {max(payload.row_count_returned, len(payload.rows))} rows."
        )

    if refusal_reason:
        reasons.append(f"Refusal reason: {refusal_reason}")

    return " ".join(reasons)


def build_evidence_bundle(payload: QueryResultPayload) -> EvidenceBundle:
    """Derive citations, sufficiency, blended confidence, and refusal flags.

    Run after **`QueryResultPayload`** is available. Do not call the synthesis LLM here.
    """
    period_coverage, partial_period_coverage = _period_coverage(payload)
    source_table = _source_table(payload)
    volume_posting_count = _volume_posting_count(payload)

    if payload.router_error:
        refusal_reason = f"Query execution failed before evidence could be gathered: {payload.router_error}"
        return EvidenceBundle(
            facts=[],
            period_coverage=period_coverage,
            volume_posting_count=0,
            sufficiency=DataSufficiency.NO_DATA,
            blended_confidence=0.0,
            confidence_explanation=_confidence_explanation(
                payload=payload,
                period_coverage=period_coverage,
                partial_period_coverage=partial_period_coverage,
                volume_posting_count=0,
                structural_gaps=[],
                refusal_reason=refusal_reason,
            ),
            refuse_synthesis=True,
            refusal_reason=refusal_reason,
        )

    if not payload.rows:
        refusal_reason = "No data in scope for the selected filters."
        return EvidenceBundle(
            facts=[],
            period_coverage=period_coverage,
            volume_posting_count=0,
            sufficiency=DataSufficiency.NO_DATA,
            blended_confidence=0.0,
            confidence_explanation=_confidence_explanation(
                payload=payload,
                period_coverage=period_coverage,
                partial_period_coverage=partial_period_coverage,
                volume_posting_count=0,
                structural_gaps=[],
                refusal_reason=refusal_reason,
            ),
            refuse_synthesis=True,
            refusal_reason=refusal_reason,
        )

    structural_gaps = _structural_gaps(payload)
    available_metric_keys = _available_metric_keys(payload)
    refusal_reason: str | None = None

    if "salary" in _normalize_key(payload.intent_label) and not _has_salary_metric(payload):
        refusal_reason = "Query results did not include salary metrics required for a salary answer."
    elif payload.columns and len(structural_gaps) >= max(1, math.ceil(len(payload.columns) / 2)):
        refusal_reason = "Returned rows were too incomplete to support a grounded synthesis."
    elif not available_metric_keys:
        refusal_reason = "Returned rows did not include usable evidence fields."

    sufficiency = _sufficiency(volume_posting_count)
    completeness_score = _completeness_score(
        payload=payload,
        structural_gaps=structural_gaps,
        partial_period_coverage=partial_period_coverage,
        period_coverage=period_coverage,
    )
    blended_confidence = (
        0.0
        if refusal_reason and sufficiency == DataSufficiency.NO_DATA
        else _blend_confidence(
            classification_confidence=payload.classification_confidence,
            volume_posting_count=volume_posting_count,
            completeness_score=completeness_score,
        )
    )

    return EvidenceBundle(
        facts=_build_citations(payload, source_table=source_table, period_coverage=period_coverage),
        period_coverage=period_coverage,
        volume_posting_count=volume_posting_count,
        sufficiency=sufficiency,
        blended_confidence=blended_confidence,
        confidence_explanation=_confidence_explanation(
            payload=payload,
            period_coverage=period_coverage,
            partial_period_coverage=partial_period_coverage,
            volume_posting_count=volume_posting_count,
            structural_gaps=structural_gaps,
            refusal_reason=refusal_reason,
        ),
        refuse_synthesis=refusal_reason is not None,
        refusal_reason=refusal_reason,
    )
