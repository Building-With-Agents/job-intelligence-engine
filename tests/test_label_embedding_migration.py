"""Migration coverage for dbo.canonical_roles.label_embedding (issue #229).

Requires PostgreSQL with pgvector — same pattern as tests/test_database.py.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from common.data_store.database import get_engine
from common.data_store.migrations import run_migrations


@pytest.mark.skipif(not os.getenv("PYTHON_DATABASE_URL"), reason="requires database")
def test_migration_adds_label_embedding_vector_type() -> None:
    """run_migrations creates label_embedding as pgvector type; idempotent."""
    engine = get_engine()
    run_migrations(engine)
    run_migrations(engine)

    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT udt_name
                FROM information_schema.columns
                WHERE table_schema = 'dbo'
                  AND table_name = 'canonical_roles'
                  AND column_name = 'label_embedding'
                """
            )
        ).fetchone()

    assert row is not None, "label_embedding column missing from dbo.canonical_roles"
    assert row[0] == "vector", f"expected udt_name 'vector', got {row[0]!r}"
