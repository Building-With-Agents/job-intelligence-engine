# ruff: noqa: T201
"""Backfill dbo.canonical_roles.label_embedding from cluster_centroid (pgvector).

Runs a one-time UPDATE that casts JSONB cluster_centroid to vector(1536).
Idempotent: rows already having label_embedding are unchanged; second run
updates 0 rows unless new rows were inserted with NULL label_embedding.

Prerequisites:
    Run migrations first so label_embedding exists:
        python scripts/db_check.py migrate

Usage (from repo root, venv activated):

    python scripts/backfill_label_embeddings.py

Reads PYTHON_DATABASE_URL from .env automatically (same as scripts/db_check.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from common.data_store.database import get_engine  # noqa: E402
from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

_LABEL_EMBEDDING_CHECK = """
SELECT 1
FROM information_schema.columns
WHERE table_schema = 'dbo'
  AND table_name = 'canonical_roles'
  AND column_name = 'label_embedding'
LIMIT 1
"""

_UPDATE_BACKFILL = """
UPDATE dbo.canonical_roles
SET label_embedding = cluster_centroid::text::vector
WHERE label_embedding IS NULL
  AND cluster_centroid IS NOT NULL
  AND jsonb_array_length(cluster_centroid) = 1536
"""

_STATS = """
SELECT
    COUNT(*) AS total,
    COUNT(label_embedding) AS with_embedding,
    COUNT(*) FILTER (WHERE label_embedding IS NULL) AS still_missing,
    COUNT(*) FILTER (
        WHERE label_embedding IS NULL
          AND (
              cluster_centroid IS NULL
              OR jsonb_typeof(cluster_centroid) <> 'array'
              OR jsonb_array_length(cluster_centroid) IS DISTINCT FROM 1536
          )
    ) AS skipped_no_valid_centroid
FROM dbo.canonical_roles
"""


def main() -> None:
    engine = get_engine()

    with engine.connect() as conn:
        exists = conn.execute(text(_LABEL_EMBEDDING_CHECK)).scalar()
        if not exists:
            print(
                "ERROR: dbo.canonical_roles.label_embedding does not exist.\n"
                "Run: python scripts/db_check.py migrate",
                file=sys.stderr,
            )
            sys.exit(1)

    with engine.begin() as conn:
        result = conn.execute(text(_UPDATE_BACKFILL))
        rows_updated = result.rowcount
        if rows_updated is None:
            rows_updated = 0

    with engine.connect() as conn:
        row = conn.execute(text(_STATS)).one()
        total = int(row.total or 0)
        with_embedding = int(row.with_embedding or 0)
        still_missing = int(row.still_missing or 0)
        skipped = int(row.skipped_no_valid_centroid or 0)

    print("backfill_label_embeddings complete")
    print(f"  1. rows updated: {rows_updated}")
    print(f"  2. total canonical_roles: {total}")
    print(f"  3. rows with label_embedding: {with_embedding}")
    print(f"  4. rows still missing label_embedding: {still_missing}")
    print(
        "  5. rows skipped (cluster_centroid null or wrong dimension / not array): "
        f"{skipped}"
    )


if __name__ == "__main__":
    main()
