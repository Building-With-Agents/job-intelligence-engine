# ruff: noqa: T201
"""Backfill dbo.job_postings.canonical_role_id for eligible NULL rows (JIE #363).

Idempotent: only updates rows where ``canonical_role_id IS NULL`` and loader-aligned
eligibility passes. Uses nearest ``canonical_roles.label_embedding`` when similarity
meets ``clustering.assignment.min_centroid_similarity`` (env: CLUSTER_ASSIGNMENT_MIN_SIMILARITY).

Prerequisites:
    python scripts/db_check.py migrate
    python scripts/backfill_label_embeddings.py   # if label_embedding sparse

Usage (from repo root, venv activated):

    python scripts/backfill_canonical_role_id.py --dry-run
    python scripts/backfill_canonical_role_id.py
    python scripts/backfill_canonical_role_id.py --limit 100 --min-similarity 0.8
    python scripts/backfill_canonical_role_id.py --strict
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.canonical_roles.assign_from_centroids import (  # noqa: E402
    assign_canonical_role_for_posting,
    load_eligible_null_posting_rows,
    nearest_canonical_role_match,
    posting_vector_for_assignment,
)
from analytics.canonical_roles.loader import _row_to_features  # noqa: E402
from analytics.clustering.config import (  # noqa: E402
    cluster_assignment_max_per_run,
    cluster_assignment_min_similarity,
)
from common.data_store.database import get_engine  # noqa: E402
from common.env import load_repo_root_dotenv  # noqa: E402

log = structlog.get_logger()
load_repo_root_dotenv()

_STRICT_MIN_ASSIGN_RATE = 0.50

_NULL_STATS = """
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE canonical_role_id IS NULL) AS null_count
FROM dbo.job_postings
"""

_LABEL_EMBEDDING_CHECK = """
SELECT COUNT(*) AS n
FROM dbo.canonical_roles
WHERE label_embedding IS NOT NULL
"""


def _null_pct(null_count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return 100.0 * float(null_count) / float(total)


def _print_null_stats(conn, *, label: str) -> tuple[int, int]:
    row = conn.execute(text(_NULL_STATS)).one()
    total = int(row.total or 0)
    null_count = int(row.null_count or 0)
    pct = _null_pct(null_count, total)
    print(f"{label}: total={total} null_canonical_role_id={null_count} ({pct:.1f}%)")
    return null_count, total


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill job_postings.canonical_role_id via centroid similarity")
    parser.add_argument("--dry-run", action="store_true", help="Count and log would-assign; no UPDATE")
    parser.add_argument("--limit", type=int, default=None, help="Max candidate rows to process")
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=None,
        help="Override clustering.assignment.min_centroid_similarity",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=f"Exit 1 if assign rate < {_STRICT_MIN_ASSIGN_RATE:.0%} of processed candidates",
    )
    args = parser.parse_args()

    min_sim = float(args.min_similarity) if args.min_similarity is not None else cluster_assignment_min_similarity()
    max_assign = cluster_assignment_max_per_run()
    effective_limit = args.limit if args.limit is not None else max_assign
    if effective_limit > max_assign:
        effective_limit = max_assign

    engine = get_engine()
    with engine.connect() as conn:
        emb_roles = int(conn.execute(text(_LABEL_EMBEDDING_CHECK)).scalar() or 0)
        if emb_roles == 0:
            print(
                "WARNING: no canonical_roles.label_embedding rows. Run: python scripts/backfill_label_embeddings.py",
                file=sys.stderr,
            )

    assigned = 0
    skipped_below = 0
    no_vector = 0
    processed = 0

    with engine.connect() as conn:
        print("=== Before backfill ===")
        _print_null_stats(conn, label="before")

    with engine.begin() as session:
        rows = load_eligible_null_posting_rows(session, limit=effective_limit)
        print(f"candidates_loaded={len(rows)} min_similarity={min_sim} dry_run={args.dry_run}")

        for row in rows:
            if assigned >= max_assign:
                break
            processed += 1
            posting_id = str(row["job_posting_id"])
            features = _row_to_features(row)
            dedup_text = row.get("dedup_embedding_text")
            dedup_str = str(dedup_text) if dedup_text is not None else None

            vec = posting_vector_for_assignment(
                session,
                posting_id,
                features,
                dedup_embedding_text=dedup_str,
            )
            if vec is None:
                no_vector += 1
                continue
            role_id, _label, similarity = nearest_canonical_role_match(
                vec,
                session,
                min_similarity=min_sim,
            )
            if role_id is None:
                skipped_below += 1
                continue

            if assign_canonical_role_for_posting(
                posting_id,
                session,
                min_similarity=min_sim,
                embedding=vec,
                dry_run=args.dry_run,
            ):
                assigned += 1

    with engine.connect() as conn:
        print("\n=== After backfill ===")
        _print_null_stats(conn, label="after")

    assign_rate = (assigned / processed) if processed else 0.0
    print(
        f"\nprocessed={processed} assigned={assigned} skipped_below_threshold={skipped_below} "
        f"no_vector={no_vector} assign_rate={assign_rate:.1%}"
    )

    if args.strict and processed > 0 and assign_rate < _STRICT_MIN_ASSIGN_RATE:
        print(
            f"ERROR: assign rate {assign_rate:.1%} < {_STRICT_MIN_ASSIGN_RATE:.0%} (--strict)",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
