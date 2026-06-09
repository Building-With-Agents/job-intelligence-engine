"""Nearest canonical-role assignment for NULL ``job_postings.canonical_role_id`` (JIE #363).

EXEMPLAR: Phase 2 reference — centroid similarity fallback for HDBSCAN noise rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from analytics.clustering.config import (
    cluster_assignment_min_similarity,
    cluster_embedding_audit_agent_name,
)
from analytics.clustering.text import build_clustering_text
from analytics.clustering.types import PostingClusterFeatures
from analytics.query_engine.sql_guardrails import _ISSUE197_NA_LABEL
from common.embedding_vectors import parse_stored_embedding, vector_to_pg_cast_param
from common.embeddings import embed_texts_azure

log = structlog.get_logger()

_EMBEDDING_DIM = 1536

# Loader-aligned eligibility for H1 backfill (normalized_jobs + successful extraction).
_ELIGIBLE_NULL_POSTING_SQL = """
WITH latest_ei AS (
    SELECT DISTINCT ON (normalized_job_id)
        normalized_job_id,
        skills,
        tools,
        responsibilities,
        COALESCE(extraction_failed, false) AS extraction_failed
    FROM dbo.extracted_intelligence
    WHERE normalized_job_id IS NOT NULL
    ORDER BY normalized_job_id, extracted_at DESC NULLS LAST, id DESC
)
SELECT
    jp.job_posting_id::text AS job_posting_id,
    jp.job_title AS title,
    jp.company_id::text AS company_id,
    c.company_name AS company_name,
    jp.quality_score AS quality_score,
    nj.experience_level AS seniority,
    le.skills AS skills,
    le.tools AS tools,
    le.responsibilities AS responsibilities,
    jp.dedup_embedding::text AS dedup_embedding_text
FROM dbo.job_postings jp
INNER JOIN dbo.normalized_jobs nj
    ON jp.source IS NOT NULL
    AND jp.external_id IS NOT NULL
    AND nj.source = jp.source
    AND nj.external_id = jp.external_id
INNER JOIN latest_ei le ON le.normalized_job_id = nj.id
LEFT JOIN dbo.companies c ON c.company_id = jp.company_id
WHERE jp.canonical_role_id IS NULL
    AND jp.company_id IS NOT NULL
    AND COALESCE(jp.is_duplicate, false) = false
    AND (jp.is_spam IS NOT TRUE)
    AND (jp.spam_score IS NULL OR jp.spam_score < 0.9)
    AND le.extraction_failed = false
    AND (jp.role_classification IS DISTINCT FROM :na_not_it_role)
ORDER BY jp.job_posting_id
{limit_clause}
"""

_NEAREST_ROLE_SQL = """
SELECT
    role_id,
    label,
    1 - (label_embedding <=> CAST(:vec AS vector)) AS similarity
FROM dbo.canonical_roles
WHERE label_embedding IS NOT NULL
ORDER BY label_embedding <=> CAST(:vec AS vector)
LIMIT 1
"""

_UPDATE_CANONICAL_ROLE_SQL = """
UPDATE dbo.job_postings
SET canonical_role_id = :role_id
WHERE job_posting_id = CAST(:posting_id AS text)
  AND canonical_role_id IS NULL
  AND company_id IS NOT NULL
  AND COALESCE(is_duplicate, false) = false
  AND (is_spam IS NOT TRUE)
  AND (spam_score IS NULL OR spam_score < 0.9)
  AND (role_classification IS DISTINCT FROM :na_not_it_role)
"""


def build_assignment_text(features: PostingClusterFeatures) -> str:
    """Reuse clustering embedding text shape for assignment vectors."""
    return build_clustering_text(features)


def _vector_to_pg_param(embedding: Sequence[float]) -> str:
    return vector_to_pg_cast_param([float(v) for v in embedding])


def nearest_canonical_role_match(
    embedding: Sequence[float],
    session: Session,
    *,
    min_similarity: float | None = None,
) -> tuple[str | None, str | None, float | None]:
    """Return ``(role_id, label, similarity)`` when nearest role meets threshold."""
    if len(embedding) != _EMBEDDING_DIM:
        return None, None, None
    threshold = float(min_similarity) if min_similarity is not None else cluster_assignment_min_similarity()
    row = session.execute(
        text(_NEAREST_ROLE_SQL),
        {"vec": _vector_to_pg_param(embedding)},
    ).fetchone()
    if row is None:
        return None, None, None
    role_id = str(row[0])
    label = str(row[1]) if row[1] is not None else None
    similarity = float(row[2]) if row[2] is not None else None
    if similarity is None or similarity < threshold:
        return None, label, similarity
    return role_id, label, similarity


def nearest_canonical_role_id(
    embedding: Sequence[float],
    session: Session,
    *,
    min_similarity: float | None = None,
) -> str | None:
    """Nearest ``canonical_roles.role_id`` when cosine similarity >= threshold."""
    role_id, _, _ = nearest_canonical_role_match(embedding, session, min_similarity=min_similarity)
    return role_id


def posting_vector_for_assignment(
    session: Session,
    posting_id: str,
    features: PostingClusterFeatures,
    *,
    dedup_embedding_text: str | None = None,
) -> list[float] | None:
    """Prefer stored ``dedup_embedding``; otherwise embed assignment text."""
    raw = dedup_embedding_text
    if raw is None:
        row = session.execute(
            text("SELECT dedup_embedding::text AS emb FROM dbo.job_postings WHERE job_posting_id = CAST(:pid AS text)"),
            {"pid": posting_id},
        ).fetchone()
        raw = str(row[0]) if row is not None and row[0] is not None else None
    parsed = parse_stored_embedding(raw)
    if parsed is not None and len(parsed) == _EMBEDDING_DIM:
        return [float(x) for x in parsed.tolist()]
    vectors = embed_texts_azure(
        [build_assignment_text(features)],
        audit_agent_name=cluster_embedding_audit_agent_name(),
    )
    if vectors is None or not vectors or not vectors[0]:
        return None
    vec = [float(v) for v in vectors[0]]
    return vec if len(vec) == _EMBEDDING_DIM else None


def assign_canonical_role_for_posting(
    posting_id: str,
    session: Session,
    *,
    min_similarity: float | None = None,
    embedding: Sequence[float] | None = None,
    features: PostingClusterFeatures | None = None,
    dedup_embedding_text: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Assign ``canonical_role_id`` when similarity >= threshold and row is eligible.

    When ``dry_run`` is True, no writes are performed, but the function still reads
    the database (e.g. ``nearest_canonical_role_match`` and embedding lookup paths).
    """
    vec = embedding
    if vec is None:
        if features is None:
            return False
        vec = posting_vector_for_assignment(
            session,
            posting_id,
            features,
            dedup_embedding_text=dedup_embedding_text,
        )
    if vec is None:
        log.info("canonical_role_assignment_skipped_no_vector", posting_id=posting_id)
        return False

    role_id, _label, similarity = nearest_canonical_role_match(
        vec,
        session,
        min_similarity=min_similarity,
    )
    if role_id is None:
        log.info(
            "skipped_below_threshold",
            posting_id=posting_id,
            similarity=similarity,
            min_similarity=min_similarity or cluster_assignment_min_similarity(),
        )
        return False

    if dry_run:
        log.info(
            "canonical_role_backfill_assigned",
            posting_id=posting_id,
            dry_run=True,
            similarity=similarity,
        )
        return True

    result = session.execute(
        text(_UPDATE_CANONICAL_ROLE_SQL),
        {
            "role_id": role_id,
            "posting_id": posting_id,
            "na_not_it_role": _ISSUE197_NA_LABEL,
        },
    )
    updated = (result.rowcount or 0) > 0
    if updated:
        log.info(
            "canonical_role_backfill_assigned",
            posting_id=posting_id,
            similarity=similarity,
        )
    return updated


def load_eligible_null_posting_rows(
    session: Session,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Rows eligible for H1 centroid backfill (loader-shaped dicts)."""
    limit_clause = ""
    params: dict[str, Any] = {"na_not_it_role": _ISSUE197_NA_LABEL}
    if limit is not None and limit > 0:
        limit_clause = " LIMIT :row_limit "
        params["row_limit"] = int(limit)
    sql = text(_ELIGIBLE_NULL_POSTING_SQL.format(limit_clause=limit_clause))
    return [dict(row) for row in session.execute(sql, params).mappings().all()]


__all__ = [
    "assign_canonical_role_for_posting",
    "build_assignment_text",
    "load_eligible_null_posting_rows",
    "nearest_canonical_role_id",
    "nearest_canonical_role_match",
    "posting_vector_for_assignment",
]
