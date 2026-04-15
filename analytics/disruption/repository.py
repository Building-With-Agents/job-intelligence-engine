"""Persistence and read paths for disruption fingerprints (#108 reads + writes).

PostgreSQL **schema** for raw SQL is resolved in order:

1. ``JIE_SQL_SCHEMA`` env (ASCII identifier, e.g. ``public`` or ``dbo``) — forces
   all qualified table names to ``<schema>.<table>``.
2. Else, on a real :class:`sqlalchemy.orm.Session`, one lookup of
   ``information_schema.tables`` for ``canonical_roles`` (prefers ``dbo``, then
   ``public``, then lexicographic).
3. Else (e.g. unit-test doubles), falls back to :attr:`CanonicalRole.__table__.schema`
   (ORM metadata, usually ``dbo``).

Table set and join + spam gates match ``analytics.aggregators.demand_weekly`` (IMP-021):

- ``extracted_intelligence`` … ``normalized_jobs`` … ``job_postings`` … ``companies``
- Filters: ``extraction_failed``, ``canonical_role_id``, locked ``temporal_period``,
  ``is_spam``, ``spam_score`` vs reject tier, ``is_duplicate``.

Canonical role list reads ``canonical_roles.role_id``.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as OrmSession

from analytics.disruption.models import (
    TEMPORAL_PERIOD_SEQUENCE,
    DisruptionFingerprintRecord,
    TemporalPeriodSnapshot,
)
from common.data_store.models import CanonicalRole, DisruptionFingerprint
from enrichment.classifiers.spam_preview import get_spam_thresholds

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_SCHEMA_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_schema_cache: dict[str, str] = {}

_PERIOD_ORDER = {p: i for i, p in enumerate(TEMPORAL_PERIOD_SEQUENCE)}

_DETECT_CANONICAL_ROLES_SCHEMA_SQL = text(
    """
    SELECT table_schema
    FROM information_schema.tables
    WHERE table_name = 'canonical_roles'
      AND table_type = 'BASE TABLE'
    ORDER BY CASE table_schema WHEN 'dbo' THEN 0 WHEN 'public' THEN 1 ELSE 2 END,
        table_schema
    LIMIT 1
    """
)


class DisruptionRepositoryQueryError(RuntimeError):
    """Raised when a disruption repository SQL read fails (session/DB error or bad driver state).

    Chains the original :exc:`sqlalchemy.exc.SQLAlchemyError` as ``__cause__`` for debugging.
    """


def _validate_schema_identifier(raw: str) -> str:
    s = raw.strip()
    if not _SCHEMA_IDENT.fullmatch(s):
        raise DisruptionRepositoryQueryError(
            f"JIE_SQL_SCHEMA must match {_SCHEMA_IDENT.pattern!r}, got {raw!r}"
        )
    return s


def _agent_table_prefix(session: Any) -> str:
    """Return ``'<schema>.'`` for qualifying agent tables in raw SQL (PostgreSQL)."""
    env_raw = (os.getenv("JIE_SQL_SCHEMA") or "").strip()
    if env_raw:
        return f"{_validate_schema_identifier(env_raw)}."
    if not isinstance(session, OrmSession):
        orm_schema = CanonicalRole.__table__.schema
        return f"{(orm_schema or 'public').strip()}."
    bind = session.get_bind()
    if bind is None:
        orm_schema = CanonicalRole.__table__.schema
        return f"{(orm_schema or 'public').strip()}."
    cache_key = bind.url.render_as_string(hide_password=True)
    if cache_key in _schema_cache:
        return f"{_schema_cache[cache_key]}."
    try:
        row = session.execute(_DETECT_CANONICAL_ROLES_SCHEMA_SQL).scalar_one_or_none()
    except SQLAlchemyError:
        row = None
    fallback = CanonicalRole.__table__.schema or "public"
    schema = (str(row).strip() if row else fallback) or fallback
    _schema_cache[cache_key] = schema
    return f"{schema}."


def _execute_mappings(
    session: OrmSession,
    statement: Any,
    params: Mapping[str, Any] | None,
    *,
    operation: str,
) -> list[Mapping[str, Any]]:
    """Run ``session.execute`` and return mapping rows; wrap failures with context (no logging)."""
    try:
        result = session.execute(statement) if params is None else session.execute(statement, params)
        return list(result.mappings().all())
    except SQLAlchemyError as exc:
        ctx = dict(params) if params is not None else {}
        raise DisruptionRepositoryQueryError(
            f"{operation} failed (context keys: {sorted(ctx.keys())}): {exc}"
        ) from exc


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_period_aggregate_rows(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[int, float | None, float | None]]:
    """Build ``period -> (posting_count, ai_relevance_avg, responsibility_density)`` dropping bad rows.

    Unknown ``temporal_period`` values (not in the locked enrichment set) and
    non-positive ``posting_count`` are ignored so a single bad row does not fail the batch.
    """
    out: dict[str, tuple[int, float | None, float | None]] = {}
    for row in rows:
        raw_period = row.get("temporal_period")
        if raw_period is None:
            continue
        period = str(raw_period).strip()
        if period not in _PERIOD_ORDER:
            continue
        try:
            posting_count = int(row.get("posting_count") or 0)
        except (TypeError, ValueError):
            continue
        if posting_count <= 0:
            continue
        ai_avg = _optional_float(row.get("ai_relevance_avg"))
        resp = _optional_float(row.get("responsibility_density"))
        out[period] = (posting_count, ai_avg, resp)
    return out

# Mirrors ``DisruptionClassifier`` keyword heuristics for density when ``ai_relevance_score`` is absent.
_AI_SIGNAL_TOKENS: tuple[str, ...] = (
    "ai",
    "gpt",
    "llm",
    "copilot",
    "agent",
    "prompt",
    "claude",
    "openai",
    "anthropic",
    "langchain",
    "langgraph",
)

# Join + filter template aligned with ``demand_weekly._SKILLS_EXPANDED`` (IMP-021 spam/dedup gates).
# ``dot`` is ``'<schema>.'`` from :func:`_agent_table_prefix` (never user-controlled at runtime).
_DISRUPTION_BASE_FILTERS = """
WHERE NOT ei.extraction_failed
    AND jp.is_spam IS NOT TRUE
    AND (jp.spam_score IS NULL OR jp.spam_score <= :reject_threshold)
    AND jp.is_duplicate IS NOT TRUE
    AND jp.canonical_role_id = :role_id
    AND jp.temporal_period IN ('pre_chatgpt', 'early_genai', 'post_gpt4', 'agentic_era')
"""


def _disruption_base_from(dot: str) -> str:
    return f"""
FROM {dot}extracted_intelligence ei
INNER JOIN {dot}normalized_jobs nj ON nj.id = ei.normalized_job_id
INNER JOIN {dot}job_postings jp
    ON jp.source IS NOT NULL
    AND jp.external_id IS NOT NULL
    AND nj.source = jp.source
    AND nj.external_id = jp.external_id
INNER JOIN {dot}companies c ON c.company_id = jp.company_id::text
"""


def _period_agg_sql(dot: str) -> Any:
    return text(
        f"""
    SELECT
        jp.temporal_period::text AS temporal_period,
        COUNT(DISTINCT jp.job_posting_id) AS posting_count,
        AVG(jp.ai_relevance_score) FILTER (WHERE jp.ai_relevance_score IS NOT NULL) AS ai_relevance_avg,
        AVG(
            LEAST(
                1.0::double precision,
                GREATEST(
                    0.0::double precision,
                    COALESCE(
                        jsonb_array_length(
                            CASE
                                WHEN jsonb_typeof(COALESCE(ei.responsibilities, '[]'::jsonb)) = 'array'
                                THEN ei.responsibilities
                                ELSE '[]'::jsonb
                            END
                        ),
                        0
                    )::double precision / 20.0
                )
            )
        ) AS responsibility_density
    {_disruption_base_from(dot)}
    {_DISRUPTION_BASE_FILTERS}
    GROUP BY jp.temporal_period
    """
    )


def _skills_by_period_sql(dot: str) -> Any:
    return text(
        f"""
    SELECT
        jp.temporal_period::text AS temporal_period,
        NULLIF(
            trim(COALESCE(skel.value->>'skill_name', skel.value->>'label')),
            ''
        ) AS label,
        COUNT(DISTINCT jp.job_posting_id) AS cnt
    {_disruption_base_from(dot)}
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(COALESCE(ei.skills, '[]'::jsonb)) = 'array' THEN ei.skills
            ELSE '[]'::jsonb
        END
    ) AS skel(value)
    {_DISRUPTION_BASE_FILTERS}
        AND NULLIF(
            trim(COALESCE(skel.value->>'skill_name', skel.value->>'label')),
            ''
        ) IS NOT NULL
    GROUP BY jp.temporal_period,
        NULLIF(
            trim(COALESCE(skel.value->>'skill_name', skel.value->>'label')),
            ''
        )
    """
    )


def _tools_by_period_sql(dot: str) -> Any:
    return text(
        f"""
    SELECT
        jp.temporal_period::text AS temporal_period,
        NULLIF(
            trim(COALESCE(tel.value->>'tool_name', tel.value->>'label')),
            ''
        ) AS label,
        COUNT(DISTINCT jp.job_posting_id) AS cnt
    {_disruption_base_from(dot)}
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(COALESCE(ei.tools, '[]'::jsonb)) = 'array' THEN ei.tools
            ELSE '[]'::jsonb
        END
    ) AS tel(value)
    {_DISRUPTION_BASE_FILTERS}
        AND NULLIF(
            trim(COALESCE(tel.value->>'tool_name', tel.value->>'label')),
            ''
        ) IS NOT NULL
    GROUP BY jp.temporal_period,
        NULLIF(
            trim(COALESCE(tel.value->>'tool_name', tel.value->>'label')),
            ''
        )
    """
    )


def _tasks_by_period_sql(dot: str) -> Any:
    return text(
        f"""
    SELECT
        jp.temporal_period::text AS temporal_period,
        NULLIF(
            trim(COALESCE(tt.value->>'task_description', tt.value->>'task_category')),
            ''
        ) AS label,
        COUNT(DISTINCT jp.job_posting_id) AS cnt
    {_disruption_base_from(dot)}
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(COALESCE(ei.tasks, '[]'::jsonb)) = 'array' THEN ei.tasks
            ELSE '[]'::jsonb
        END
    ) AS tt(value)
    {_DISRUPTION_BASE_FILTERS}
        AND NULLIF(
            trim(COALESCE(tt.value->>'task_description', tt.value->>'task_category')),
            ''
        ) IS NOT NULL
    GROUP BY jp.temporal_period,
        NULLIF(
            trim(COALESCE(tt.value->>'task_description', tt.value->>'task_category')),
            ''
        )
    """
    )


def _ai_keyword_density(skill_mix: Mapping[str, float], tool_mix: Mapping[str, float]) -> float:
    combined = {**skill_mix, **tool_mix}
    total = sum(combined.values())
    if total <= 0:
        return 0.0
    scored = 0.0
    for label, weight in combined.items():
        low = (label or "").strip().lower()
        if any(tok in low for tok in _AI_SIGNAL_TOKENS):
            scored += weight
    return min(1.0, scored / total)


def _mix_from_counts(
    rows: Sequence[Mapping[str, Any]],
    period_totals: Mapping[str, int],
    period_key: str = "temporal_period",
    label_key: str = "label",
    count_key: str = "cnt",
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        period = row[period_key]
        label = row[label_key]
        cnt = int(row[count_key] or 0)
        if not period or not label:
            continue
        total = period_totals.get(period, 0)
        if total <= 0:
            continue
        out[str(period)][str(label)] = round(cnt / total, 6)
    return dict(out)


def _sorted_periods(periods: set[str]) -> list[str]:
    return sorted(periods, key=lambda p: (_PERIOD_ORDER.get(p, 999), p))


class DisruptionFingerprintRepository:
    """Load canonical roles and period snapshots from SQLAlchemy; writes remain TODO (#108).

    SQL failures raise :exc:`DisruptionRepositoryQueryError` with the underlying
    :exc:`sqlalchemy.exc.SQLAlchemyError` chained as ``__cause__`` (see ``_execute_mappings``).
    """

    def fetch_canonical_roles(self, session: Session | None = None) -> list[str]:
        """Return canonical ``role_id`` values to analyze (``<schema>.canonical_roles``).

        Uses the same schema prefix rules as :meth:`fetch_period_snapshots` (``JIE_SQL_SCHEMA`` /
        ``information_schema`` / ORM fallback). Raw SQL keeps reads aligned with the dynamic
        ``<schema>.`` prefix instead of hard-coding ``dbo``.

        Ordered by ``role_id`` ascending for stable iteration. Returns ``[]`` when
        ``session`` is ``None`` or the table is empty.

        Raises:
            DisruptionRepositoryQueryError: Database/driver errors from the read.
        """
        if session is None:
            return []
        dot = _agent_table_prefix(session)
        stmt = text(f"SELECT role_id FROM {dot}canonical_roles ORDER BY role_id ASC")
        try:
            rows = session.execute(stmt).scalars().all()
        except SQLAlchemyError as exc:
            raise DisruptionRepositoryQueryError(
                f"fetch_canonical_roles failed (SELECT role_id FROM {dot}canonical_roles): {exc}"
            ) from exc
        return [str(r) for r in rows if r is not None and str(r).strip() != ""]

    def fetch_period_snapshots(
        self,
        role_id: str,
        session: Session | None = None,
    ) -> list[TemporalPeriodSnapshot]:
        """Return temporal-period slices for ``role_id`` from enriched postings + extraction.

        Join path and spam/dedup gates match ``analytics.aggregators.demand_weekly`` (IMP-021).
        Only rows with ``temporal_period`` in the enrichment-locked set are included; missing
        periods are not returned here — callers use :func:`normalize_temporal_snapshots` for that.

        Returns ``[]`` when ``session`` is ``None``, ``role_id`` is blank, or no qualifying rows exist.
        Malformed aggregate rows (unknown period label, bad counts) are skipped.

        Raises:
            DisruptionRepositoryQueryError: Database/driver errors from any of the four reads.
        """
        if session is None:
            return []
        rid = (role_id or "").strip()
        if not rid:
            return []

        _, reject_threshold = get_spam_thresholds()
        params: dict[str, Any] = {"role_id": rid, "reject_threshold": reject_threshold}

        dot = _agent_table_prefix(session)

        period_rows = _execute_mappings(
            session,
            _period_agg_sql(dot),
            params,
            operation="DisruptionFingerprintRepository.fetch_period_snapshots[period_agg]",
        )
        aggregated = _normalize_period_aggregate_rows(period_rows)
        if not aggregated:
            return []

        period_totals = {p: aggregated[p][0] for p in aggregated}

        skill_rows = _execute_mappings(
            session,
            _skills_by_period_sql(dot),
            params,
            operation="DisruptionFingerprintRepository.fetch_period_snapshots[skills]",
        )
        tool_rows = _execute_mappings(
            session,
            _tools_by_period_sql(dot),
            params,
            operation="DisruptionFingerprintRepository.fetch_period_snapshots[tools]",
        )
        task_rows = _execute_mappings(
            session,
            _tasks_by_period_sql(dot),
            params,
            operation="DisruptionFingerprintRepository.fetch_period_snapshots[tasks]",
        )

        skill_by_period = _mix_from_counts(skill_rows, period_totals)
        tool_by_period = _mix_from_counts(tool_rows, period_totals)
        task_by_period = _mix_from_counts(task_rows, period_totals)

        snapshots: list[TemporalPeriodSnapshot] = []
        for period in _sorted_periods(set(period_totals.keys())):
            posting_count, ai_avg, raw_resp = aggregated[period]
            resp_d = 0.0
            if raw_resp is not None:
                resp_d = round(float(max(0.0, min(1.0, raw_resp))), 6)

            sm = dict(skill_by_period.get(period, {}))
            tm = dict(tool_by_period.get(period, {}))
            tk = dict(task_by_period.get(period, {}))

            if ai_avg is not None:
                ai_density = round(float(max(0.0, min(1.0, ai_avg))), 6)
            else:
                ai_density = round(_ai_keyword_density(sm, tm), 6)

            snapshots.append(
                TemporalPeriodSnapshot(
                    temporal_period=period,
                    posting_count=posting_count,
                    skill_mix=sm,
                    tool_mix=tm,
                    task_mix=tk,
                    responsibility_density=resp_d,
                    ai_requirement_density=ai_density,
                    has_observed_data=True,
                )
            )
        return snapshots

    def save_fingerprints(
        self,
        results: list[DisruptionFingerprintRecord],
        session: Session | None = None,
    ) -> None:
        """Upsert fingerprint rows into ``disruption_fingerprints`` via ``session.merge``.

        Primary key is ``canonical_role_id``; repeated refreshes overwrite metrics and
        ``computed_at``. Mirrors the ``session.merge`` pattern in
        ``analytics.insights.posting_freshness_store.persist_posting_freshness_rows``.

        No-op when ``session`` is ``None`` or ``results`` is empty. All rows in one call share the
        same ``computed_at`` (UTC batch timestamp). Event emission is handled by the service layer.

        Raises:
            DisruptionRepositoryQueryError: Database/driver errors during merge.
        """
        if session is None or not results:
            return
        now = datetime.now(timezone.utc)
        try:
            for rec in results:
                session.merge(
                    DisruptionFingerprint(
                        canonical_role_id=rec.canonical_role_id,
                        disruption_category=list(rec.disruption_category),
                        disruption_intensity=rec.disruption_intensity,
                        skill_velocity=list(rec.skill_velocity),
                        tool_transition=list(rec.tool_transition),
                        task_shift=list(rec.task_shift),
                        responsibility_expansion=rec.responsibility_expansion,
                        ai_intensity_trend=rec.ai_intensity_trend,
                        workflow_restructuring_score=rec.workflow_restructuring_score,
                        trajectory=rec.trajectory,
                        period_comparison=list(rec.period_comparison),
                        content_fingerprint=rec.content_fingerprint,
                        computed_at=now,
                    )
                )
        except SQLAlchemyError as exc:
            raise DisruptionRepositoryQueryError(
                f"save_fingerprints failed (rows={len(results)}): {exc}"
            ) from exc
