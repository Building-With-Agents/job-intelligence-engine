"""Tests for the ON CONFLICT clause builders in pg-seed-data scripts.

Validates the per-table upsert-with-COALESCE behavior added so devs can
re-seed after a schema-adds-columns migration without losing existing data
and without skipping the new column data.

These are pure SQL-string tests — no DB calls.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# pg-seed-data isn't a Python package; import its modules by path.
_SEED_DIR = Path(__file__).resolve().parent.parent / "pg-seed-data"
sys.path.insert(0, str(_SEED_DIR))

seed_agent_data = importlib.import_module("seed_agent_data")
seed_pg_database = importlib.import_module("seed_pg_database")


# ---------------------------------------------------------------------------
# Override-map invariants — same shape in both scripts
# ---------------------------------------------------------------------------


def test_both_scripts_define_upsert_update_columns() -> None:
    """Both seed scripts must define UPSERT_UPDATE_COLUMNS at module scope."""
    assert hasattr(seed_agent_data, "UPSERT_UPDATE_COLUMNS")
    assert hasattr(seed_pg_database, "UPSERT_UPDATE_COLUMNS")


def test_upsert_update_columns_match_between_scripts() -> None:
    """The two scripts must use the same per-table override map (otherwise re-seed
    behavior diverges depending on which entry-point a dev runs)."""
    assert seed_agent_data.UPSERT_UPDATE_COLUMNS == seed_pg_database.UPSERT_UPDATE_COLUMNS


def test_job_postings_upsert_includes_all_eight_qna_columns() -> None:
    """job_postings UPSERT must cover all 8 columns added by #170/#173/#174."""
    expected = {
        "date_posted",
        "seniority_level",
        "is_remote",
        "role_classification",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
    }
    assert set(seed_agent_data.UPSERT_UPDATE_COLUMNS["job_postings"]) == expected


# ---------------------------------------------------------------------------
# seed_agent_data._build_on_conflict_clause
# ---------------------------------------------------------------------------


def test_agent_clause_table_not_in_map_returns_do_nothing() -> None:
    """Tables without an override entry must use DO NOTHING (backward compat)."""
    clause = seed_agent_data._build_on_conflict_clause("companies", ["company_id"], ["company_id", "company_name"])
    assert "DO NOTHING" in clause
    assert "DO UPDATE" not in clause


def test_agent_clause_table_in_map_with_matching_columns_returns_do_update() -> None:
    """Tables in the map with fixture columns intersecting update list use DO UPDATE."""
    fixture_cols = [
        "job_posting_id",
        "company_id",
        "job_title",
        "date_posted",
        "is_remote",
        "role_classification",
    ]
    clause = seed_agent_data._build_on_conflict_clause("job_postings", ["job_posting_id"], fixture_cols)
    assert "DO UPDATE SET" in clause
    # Every overlapping column should appear in the SET clause as COALESCE
    for col in ("date_posted", "is_remote", "role_classification"):
        assert f'"{col}" = COALESCE("dbo"."job_postings"."{col}", EXCLUDED."{col}")' in clause


def test_agent_clause_filters_to_columns_present_in_fixture() -> None:
    """If the fixture lacks a column from the update map, that column is silently dropped
    (backward compat with old fixtures)."""
    # Fixture only has date_posted, not the other 7
    fixture_cols = ["job_posting_id", "date_posted"]
    clause = seed_agent_data._build_on_conflict_clause("job_postings", ["job_posting_id"], fixture_cols)
    assert '"date_posted" = COALESCE("dbo"."job_postings"."date_posted", EXCLUDED."date_posted")' in clause
    # None of the other 7 should appear
    for col in (
        "seniority_level",
        "is_remote",
        "role_classification",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
    ):
        assert col not in clause


def test_agent_clause_no_overlap_falls_back_to_do_nothing() -> None:
    """If none of the update-map columns are in the fixture, DO NOTHING is used."""
    # Fixture has only legacy columns
    fixture_cols = ["job_posting_id", "company_id", "job_title"]
    clause = seed_agent_data._build_on_conflict_clause("job_postings", ["job_posting_id"], fixture_cols)
    assert "DO NOTHING" in clause


def test_agent_clause_pk_quoting_handles_compound_key() -> None:
    """Multi-column primary key must be properly quoted in the conflict target.

    Uses fixture columns that overlap UPSERT_UPDATE_COLUMNS["job_postings"] so
    the clause goes through the DO UPDATE branch (which is the path that needs
    explicit pk quoting). Tables NOT in UPSERT_UPDATE_COLUMNS use the
    target-less ``ON CONFLICT DO NOTHING`` form (JIE#182/#183), so a quoted
    target only appears for the UPSERT-update path.
    """
    clause = seed_agent_data._build_on_conflict_clause(
        "job_postings",
        ["source", "external_id"],
        ["source", "external_id", "date_posted", "is_remote"],
    )
    assert 'CONFLICT ("source", "external_id")' in clause
    assert "DO UPDATE SET" in clause


def test_agent_clause_qualifies_target_columns_with_schema_and_table() -> None:
    """The COALESCE must reference dbo.<table>.<col>, not bare <col>, to avoid
    ambiguity when a column with the same name exists in EXCLUDED."""
    clause = seed_agent_data._build_on_conflict_clause(
        "job_postings", ["job_posting_id"], ["job_posting_id", "date_posted"]
    )
    assert '"dbo"."job_postings"."date_posted"' in clause


# ---------------------------------------------------------------------------
# seed_pg_database._build_on_conflict_clause (mirror behavior)
# ---------------------------------------------------------------------------


def test_pg_clause_table_not_in_map_returns_do_nothing() -> None:
    clause = seed_pg_database._build_on_conflict_clause("companies", ["company_id"], ["company_id", "company_name"])
    assert "DO NOTHING" in clause
    assert "DO UPDATE" not in clause


def test_pg_clause_table_in_map_with_matching_columns_returns_do_update() -> None:
    fixture_cols = ["job_posting_id", "date_posted", "is_remote"]
    clause = seed_pg_database._build_on_conflict_clause("job_postings", ["job_posting_id"], fixture_cols)
    assert "DO UPDATE SET" in clause
    assert '"date_posted" = COALESCE("dbo"."job_postings"."date_posted", EXCLUDED."date_posted")' in clause
    assert '"is_remote" = COALESCE("dbo"."job_postings"."is_remote", EXCLUDED."is_remote")' in clause


def test_pg_clause_no_overlap_falls_back_to_do_nothing() -> None:
    fixture_cols = ["job_posting_id", "company_id", "job_title"]
    clause = seed_pg_database._build_on_conflict_clause("job_postings", ["job_posting_id"], fixture_cols)
    assert "DO NOTHING" in clause


def test_pg_clause_qualifies_target_columns_with_schema_and_table() -> None:
    clause = seed_pg_database._build_on_conflict_clause(
        "job_postings", ["job_posting_id"], ["job_posting_id", "date_posted"]
    )
    assert '"dbo"."job_postings"."date_posted"' in clause


# ---------------------------------------------------------------------------
# Cross-script clause equivalence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "pk", "fixture_cols"),
    [
        ("companies", ["company_id"], ["company_id", "company_name"]),  # not in map
        ("job_postings", ["job_posting_id"], ["job_posting_id", "date_posted", "is_remote"]),
        (
            "job_postings",
            ["source", "external_id"],
            ["source", "external_id", "salary_min", "salary_max", "salary_currency", "salary_period"],
        ),
    ],
)
def test_clauses_match_between_scripts(table: str, pk: list[str], fixture_cols: list[str]) -> None:
    """The two scripts must produce identical ON CONFLICT clauses for the same inputs."""
    a = seed_agent_data._build_on_conflict_clause(table, pk, fixture_cols)
    p = seed_pg_database._build_on_conflict_clause(table, pk, fixture_cols)
    assert a == p
