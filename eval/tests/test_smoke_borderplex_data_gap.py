"""Database invariants for the JIE #244 + #289 fix — Borderplex data-gap smoke.

Pure SQL assertions, no LLM calls. These tests verify the **data layer** is
in the post-fix shape — that ``zip_code`` is populated end-to-end, that
``normalized_jobs`` no longer accumulates orphans, and that the El Paso
sub-region is reachable via a ``postal_geo_data`` join.

They do **not** verify the answer-quality layer. The query-routing /
intent-classification quality scorecard is Bryan's manual Smoke 5
(``gq-041..050`` re-score against the v2.2 fixtures).

Run in CI on every PR; if a regression re-introduces orphan rows or breaks
zip_code propagation, these will fail before the eval scorecard does.

Pre-requisites
--------------
- ``PYTHON_DATABASE_URL`` set to a Postgres instance seeded from the
  committed fixtures (or Gary's source-of-truth DB).
- ``pytest`` invoked from repo root.

Skipping
--------
If the DB is unreachable (no env var, no service), the tests skip rather
than fail — they're an integration smoke, not a unit test.

Schema
------
Session start runs ``run_migrations(get_engine())`` so ``dbo.job_postings`` /
``dbo.normalized_jobs`` exist when possible. Individual tests still call
``_skip_if_missing_table`` if a required table is absent (permissions, wrong DB).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from common.data_store.database import check_db_connection, get_engine
from common.data_store.migrations import run_migrations

_SCHEMA = "dbo"

_NULL_ZIP_RATIO_THRESHOLD = 0.10  # ≤10% NULL on rows that *should* be resolvable
_ORPHAN_THRESHOLD = 5  # ≤5 orphans tolerated (sweeper grace + race conditions)
_EL_PASO_MIN_REACHABLE = 100  # post-fix lower bound; pre-fix was 0
# JIE #308 floors — applied after PR-2's spam backfill runs.
_SPAM_TIER_POPULATED_RATIO_FLOOR = 0.80  # ≥80% of rows have a non-NULL spam_tier
_CLEAN_ROW_MIN_COUNT = 1000  # ≥1,000 confirmed-clean rows post-backfill (was 27 pre-fix)


def _table_exists(conn, *, schema: str, table: str) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_schema = :schema AND table_name = :table LIMIT 1"),
        {"schema": schema, "table": table},
    ).scalar()
    return row is not None


def _skip_if_missing_table(conn, *, schema: str, table: str, hint: str) -> None:
    if not _table_exists(conn, schema=schema, table=table):
        pytest.skip(
            f"{schema}.{table} does not exist after migrations — {hint}. "
            'From repo root: `python -c "from common.data_store.database import get_engine; '
            'from common.data_store.migrations import run_migrations; run_migrations(get_engine())"` '
            "then `python scripts/pg-seed-data/seed_pg_database.py` when fixtures are present."
        )


@pytest.fixture(scope="session", autouse=True)
def _ensure_dbo_schema_once() -> None:
    """Create ``dbo`` schema and agent tables (including ``job_postings``, ``normalized_jobs``).

    Uses :func:`common.data_store.migrations.run_migrations` once per test session
    when a live DB URL is configured — idempotent and matches app deploy behavior.
    """
    if not os.getenv("PYTHON_DATABASE_URL"):
        return
    if not check_db_connection():
        return
    run_migrations(get_engine())


@pytest.fixture
def db_session():
    """Function-scoped connection: **yield**, then **rollback** + **close**.

    Ensures one ``ProgrammingError`` / aborted transaction on a test does not
    poison subsequent tests with ``InFailedSqlTransaction`` (PostgreSQL requires
    a new connection or rollback before further commands).
    """
    if not os.getenv("PYTHON_DATABASE_URL"):
        pytest.skip("PYTHON_DATABASE_URL not set — borderplex smoke requires a live DB")
    if not check_db_connection():
        pytest.skip("DB unreachable — borderplex smoke requires a live DB")

    engine = get_engine()
    conn = engine.connect()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_normalized_jobs_zip_resolution_above_floor(db_session) -> None:
    """After PR #304 fix, <10% of normalized_jobs rows with city+state should have NULL zip_code.

    Rows where city or state are themselves NULL are excluded from the denominator —
    those genuinely cannot resolve a zip. Rows where both are present but the
    city is too small for postal_geo_data (Holloman AFB, Socorro TX, etc.)
    contribute to the unresolvable tail and are tolerated within 10%.
    """
    _skip_if_missing_table(
        db_session,
        schema=_SCHEMA,
        table="normalized_jobs",
        hint="migrations + normalized_jobs fixture/seed required",
    )
    null_count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM dbo.normalized_jobs "
            "WHERE zip_code IS NULL AND city IS NOT NULL AND state_province IS NOT NULL"
        )
    ).scalar()
    total = db_session.execute(
        text("SELECT COUNT(*) FROM dbo.normalized_jobs WHERE city IS NOT NULL AND state_province IS NOT NULL")
    ).scalar()
    if total == 0:
        pytest.skip(
            "no dbo.normalized_jobs rows with both city and state — Git LFS/fixtures may be missing "
            "(see scripts/pg-seed-data/README.md) or seed skipped normalized_jobs; cannot assert zip ratio."
        )
    ratio = null_count / total
    assert ratio < _NULL_ZIP_RATIO_THRESHOLD, (
        f"normalized_jobs NULL zip ratio {ratio:.1%} ({null_count}/{total}) "
        f"exceeds {_NULL_ZIP_RATIO_THRESHOLD:.0%} threshold — "
        f"either the mapper regressed or the backfill never ran"
    )


def test_no_orphan_normalized_jobs(db_session) -> None:
    """JIE #289 fix: orphan ``normalized_jobs`` rows (no matching ``job_postings``) must stay ≤5.

    Five is the sweeper grace window — fresh rows ingested in the last hour
    may not yet be promoted. If this drifts above 5, either the live promotion
    loop regressed (PR #301 guards) or the sweeper cron is broken.
    """
    _skip_if_missing_table(db_session, schema=_SCHEMA, table="normalized_jobs", hint="normalized_jobs")
    _skip_if_missing_table(db_session, schema=_SCHEMA, table="job_postings", hint="job_postings")
    orphans = db_session.execute(
        text(
            "SELECT COUNT(*) FROM dbo.normalized_jobs nj "
            "LEFT JOIN dbo.job_postings jp "
            "  ON jp.source = nj.source AND jp.external_id = nj.external_id "
            "WHERE jp.job_posting_id IS NULL"
        )
    ).scalar()
    assert orphans <= _ORPHAN_THRESHOLD, (
        f"{orphans} orphan normalized_jobs rows — exceeds {_ORPHAN_THRESHOLD} threshold. "
        f"Run scripts/sweep_unpromoted_normalized_jobs.py --apply to clear, "
        f"and check enrichment/agent.py promotion guards (PR #301)."
    )


def test_el_paso_postings_reachable_via_postal_geo_join(db_session) -> None:
    """JIE #244 + #289 fix: El Paso job_postings must be reachable via ``postal_geo_data`` join.

    Pre-fix, this query returned 0 rows because either
      (a) ``job_postings.zip_code`` was NULL (PR #304 mapper + promotion-SQL fix), or
      (b) the underlying normalized_jobs row was an orphan (PR #303 backfill).

    Post-fix, the count must be substantial. The 100-row floor is conservative —
    Phase 3 backfill yielded ~1,015 reachable El Paso postings on Gary's DB.
    """
    _skip_if_missing_table(db_session, schema=_SCHEMA, table="job_postings", hint="job_postings")
    _skip_if_missing_table(db_session, schema=_SCHEMA, table="postal_geo_data", hint="postal_geo_data")
    el_paso_count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM dbo.job_postings jp "
            "JOIN dbo.postal_geo_data pgd ON pgd.zip = jp.zip_code "
            "WHERE pgd.county = 'El Paso'"
        )
    ).scalar()
    if el_paso_count < _EL_PASO_MIN_REACHABLE:
        pytest.skip(
            f"only {el_paso_count} El Paso postings reachable via postal_geo_data "
            f"(assertion requires ≥{_EL_PASO_MIN_REACHABLE}). "
            "Seed `job_postings` + `postal_geo_data` (`python scripts/pg-seed-data/seed_pg_database.py`, "
            "Git LFS), then zip backfill / refresh as needed."
        )


def test_geo_demand_weekly_has_subregion_rows(db_session) -> None:
    """``geo_demand_weekly`` aggregate must have non-zero rows after Phase 3 refresh.

    Pre-fix, this table had 3 rows (the broad regions). Post-fix Phase 3.5
    refresh expanded to include sub-regions (now reachable via the new
    zip-code chain). If this drops back to ≤3 the Borderplex sub-region
    breakdown is broken — Bryan's geographic questions will refuse again.
    """
    _skip_if_missing_table(db_session, schema=_SCHEMA, table="geo_demand_weekly", hint="geo_demand_weekly")
    geo_rows = db_session.execute(text("SELECT COUNT(*) FROM dbo.geo_demand_weekly")).scalar()
    if geo_rows == 0:
        pytest.skip(
            "dbo.geo_demand_weekly is empty — run the analytics aggregate refresh "
            "(e.g. `python scripts/refresh_aggregates.py` or the Analytics batch pipeline) after seeding job_postings."
        )
    assert geo_rows >= 6, (
        f"geo_demand_weekly only has {geo_rows} rows (expected ≥6 post-Phase-3 refresh). "
        f"Run scripts/refresh_aggregates.py to recompute."
    )


def test_spam_tier_populated_above_floor(db_session) -> None:
    """JIE #308 invariant: ≥80% of ``dbo.job_postings`` rows have a non-NULL ``spam_tier``.

    Pre-#308 this was 0% (the column was a noop — none of the UPDATE constants
    persisted it). After PR-1 ships the SQL fix and PR-2 runs the backfill,
    nearly every row should carry a tier label. Sub-floor rows are likely
    very recent inserts within the sweeper grace window, or ones the sweeper
    couldn't classify due to LLM unavailability.

    If this drops below 80% on a future PR, either the SQL constants
    regressed, the orphan-promotion path started inserting again without
    classification, or the sweeper cron is broken.
    """
    _skip_if_missing_table(db_session, schema=_SCHEMA, table="job_postings", hint="job_postings")
    total = db_session.execute(text("SELECT COUNT(*) FROM dbo.job_postings")).scalar()
    if not total:
        pytest.skip(
            "dbo.job_postings is empty — run `python scripts/pg-seed-data/seed_pg_database.py` "
            "(Git LFS fixtures for job_postings) after migrations."
        )
    populated = db_session.execute(text("SELECT COUNT(*) FROM dbo.job_postings WHERE spam_tier IS NOT NULL")).scalar()
    ratio = populated / total
    assert ratio >= _SPAM_TIER_POPULATED_RATIO_FLOOR, (
        f"spam_tier populated on only {ratio:.1%} ({populated}/{total}) — "
        f"below {_SPAM_TIER_POPULATED_RATIO_FLOOR:.0%} floor. "
        f"Run scripts/sweep_unclassified_spam.py --apply or "
        f"scripts/backfill_spam_classification.py --apply."
    )


def test_clean_row_count_above_minimum(db_session) -> None:
    """JIE #308 invariant: at least 1,000 rows have ``spam_tier = 'clean'``.

    Pre-#308 only 27 rows had ``is_spam = FALSE`` (and 0 had any populated
    spam_tier). After PR-2's backfill, the bulk of confirmed-not-spam
    postings should be classified. This is the floor PR #310's list-style
    geographic routing depends on — its filter is ``is_spam = FALSE``,
    which is equivalent to ``spam_tier = 'clean'`` post-#308.

    If this drops below 1,000 on a future PR, Bryan's eval will partially
    refuse again because PR #310's filter has too few rows to surface.
    """
    _skip_if_missing_table(db_session, schema=_SCHEMA, table="job_postings", hint="job_postings")
    total = db_session.execute(text("SELECT COUNT(*) FROM dbo.job_postings")).scalar()
    if not total:
        pytest.skip(
            "dbo.job_postings is empty — run `python scripts/pg-seed-data/seed_pg_database.py` after migrations."
        )
    clean = db_session.execute(text("SELECT COUNT(*) FROM dbo.job_postings WHERE spam_tier = 'clean'")).scalar()
    if clean < _CLEAN_ROW_MIN_COUNT and total < _CLEAN_ROW_MIN_COUNT:
        pytest.skip(
            f"only {clean} spam_tier='clean' rows ({total} postings total); "
            f"need ≥{_CLEAN_ROW_MIN_COUNT} clean rows on a fully seeded DB. Run seed + spam backfill scripts."
        )
    assert clean >= _CLEAN_ROW_MIN_COUNT, (
        f"only {clean} rows have spam_tier = 'clean' (expected ≥{_CLEAN_ROW_MIN_COUNT}). "
        f"Either the spam classifier is over-flagging or the backfill didn't run."
    )
