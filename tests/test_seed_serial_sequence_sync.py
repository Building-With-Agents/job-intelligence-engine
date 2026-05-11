"""JIE #367 — post-seed serial sequence alignment (explicit fixture ids bypass nextval).

Regression: after inserts with explicit ``id``, sequences must be re-synced or the
next DEFAULT insert collides on primary key. Uses tag-and-teardown only — no TRUNCATE.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from common.data_store.migrations import (
    _SERIAL_SEQUENCE_TARGETS,
    _sync_agent_serial_sequences,
    run_migrations,
)


@pytest.fixture
def engine() -> Engine:
    database_url = os.getenv("PYTHON_DATABASE_URL")
    if not database_url:
        raise RuntimeError("PYTHON_DATABASE_URL is not set")
    return create_engine(database_url, future=True)


def _run_migrations(engine: Engine) -> None:
    run_migrations(engine)


def _sequence_last_value(conn, seq_fqname: str) -> int:
    """Return ``last_value`` for a quoted schema-qualified sequence name."""
    parts = seq_fqname.split(".")
    assert len(parts) == 2, seq_fqname
    schema, seqname = parts[0].strip('"'), parts[1].strip('"')
    row = conn.execute(
        text(
            """
            SELECT last_value
            FROM pg_sequences
            WHERE schemaname = CAST(:schema AS name)
              AND sequencename = CAST(:seqname AS name)
            """
        ),
        {"schema": schema, "seqname": seqname},
    ).scalar_one_or_none()
    assert row is not None, f"sequence not in pg_sequences: {seq_fqname}"
    return int(row)


@pytest.mark.skipif(not os.getenv("PYTHON_DATABASE_URL"), reason="requires database")
def test_raw_ingested_jobs_explicit_id_then_sync_allows_default_insert(engine: Engine) -> None:
    """Simulate fixture-style explicit PK, forced sequence drift, sync, then DEFAULT insert."""
    _run_migrations(engine)
    run_tag = f"jie367-seq-{uuid.uuid4().hex[:12]}"

    hash_a = "a" * 64
    hash_b = ("b" + "c" * 63)[:64]

    with engine.begin() as conn:
        max_row = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM dbo.raw_ingested_jobs")).scalar()
        explicit_id = int(max_row) + 500_000

        conn.execute(
            text(
                """
                INSERT INTO dbo.raw_ingested_jobs
                  (id, ingestion_run_id, source, external_id, raw_payload_hash,
                   title, company, processing_status, ingestion_timestamp, created_at)
                VALUES
                  (:id, :run_id, 'test_jie367', :ext_a, :hash_a,
                   'JIE367 seq test', 'Test Co', 'pending', NOW(), NOW())
                """
            ),
            {
                "id": explicit_id,
                "run_id": run_tag,
                "ext_a": f"jie367-a-{run_tag}",
                "hash_a": hash_a,
            },
        )

        seq = conn.execute(text("SELECT pg_get_serial_sequence('dbo.raw_ingested_jobs', 'id')")).scalar()
        assert seq
        conn.execute(text("SELECT setval(CAST(:seq AS regclass), 1, false)"), {"seq": seq})

    _sync_agent_serial_sequences(engine)

    with engine.begin() as conn:
        new_id = conn.execute(
            text(
                """
                INSERT INTO dbo.raw_ingested_jobs
                  (ingestion_run_id, source, external_id, raw_payload_hash,
                   title, company, processing_status, ingestion_timestamp, created_at)
                VALUES
                  (:run_id, 'test_jie367', :ext_b, :hash_b,
                   'JIE367 seq test 2', 'Test Co', 'pending', NOW(), NOW())
                RETURNING id
                """
            ),
            {"run_id": run_tag, "ext_b": f"jie367-b-{run_tag}", "hash_b": hash_b},
        ).scalar_one()
        assert int(new_id) > explicit_id

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM dbo.raw_ingested_jobs WHERE ingestion_run_id = :run_id"),
            {"run_id": run_tag},
        )


@pytest.mark.skipif(not os.getenv("PYTHON_DATABASE_URL"), reason="requires database")
def test_llm_audit_log_explicit_id_then_sync_allows_session_insert(engine: Engine) -> None:
    """Second table covering the same drift class (ORM insert uses DEFAULT id)."""
    _run_migrations(engine)
    from sqlalchemy.orm import Session

    from common.data_store.models import LLMAuditLog

    agent_tag = f"jie367-llm-{uuid.uuid4().hex[:12]}"

    with engine.begin() as conn:
        max_row = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM dbo.llm_audit_log")).scalar()
        explicit_id = int(max_row) + 500_000

        conn.execute(
            text(
                """
                INSERT INTO dbo.llm_audit_log
                  (id, agent_name, prompt_hash, model, provider,
                   latency_ms, input_tokens, output_tokens, token_count,
                   cost_usd, success, error_reason, created_at)
                VALUES
                  (:id, :agent, :ph, 'test-model', 'test-provider',
                   1, 0, 0, 0, 0.0, TRUE, NULL, NOW())
                """
            ),
            {
                "id": explicit_id,
                "agent": agent_tag,
                "ph": "0" * 64,
            },
        )

        seq = conn.execute(text("SELECT pg_get_serial_sequence('dbo.llm_audit_log', 'id')")).scalar()
        assert seq
        conn.execute(text("SELECT setval(CAST(:seq AS regclass), 1, false)"), {"seq": seq})

    _sync_agent_serial_sequences(engine)

    with Session(engine) as session:
        row = LLMAuditLog(
            agent_name=f"{agent_tag}-follow",
            prompt_hash=("1" * 64),
            model="test-model",
            provider="test-provider",
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            token_count=0,
            cost_usd=0.0,
            success=True,
            error_reason=None,
        )
        session.add(row)
        session.commit()
        new_id = row.id

    assert isinstance(new_id, int)
    assert new_id > explicit_id

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM dbo.llm_audit_log WHERE agent_name LIKE :pfx"), {"pfx": f"{agent_tag}%"})


@pytest.mark.skipif(not os.getenv("PYTHON_DATABASE_URL"), reason="requires database")
def test_serial_sequences_last_value_geq_max_id_after_global_sync(engine: Engine) -> None:
    """After ``_sync_agent_serial_sequences``, every non-empty SERIAL target aligns (AC1 invariant)."""
    _run_migrations(engine)
    _sync_agent_serial_sequences(engine)

    with engine.connect() as conn:
        for table_name, column_name in _SERIAL_SEQUENCE_TARGETS:
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(CAST(:t AS text), CAST(:c AS text))"),
                {"t": f"dbo.{table_name}", "c": column_name},
            ).scalar()
            if not seq:
                continue

            inspector = conn.execute(text("SELECT to_regclass(CAST(:r AS text))"), {"r": f"dbo.{table_name}"}).scalar()
            if inspector is None:
                continue

            mx = conn.execute(text(f'SELECT COALESCE(MAX("{column_name}"), 0) FROM dbo.{table_name}')).scalar()
            mx_int = int(mx or 0)
            if mx_int == 0:
                continue

            last_v = _sequence_last_value(conn, seq)
            assert last_v >= mx_int, f"{table_name}.{column_name}: last_value={last_v} MAX={mx_int}"
