"""Unit tests for scripts/backfill_qna_columns.py.

Mocks the SQLAlchemy session — no live DB. Validates argument parsing,
column-set semantics, and the experience_level → seniority mapping.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts.backfill_qna_columns import (
    ALL_COLUMNS,
    BULK_COLUMNS_FROM_NORMALIZED,
    EXPERIENCE_LEVEL_TO_SENIORITY,
    PER_ROW_COLUMNS,
    _parse_columns,
    backfill_bulk_columns,
    backfill_role_classification,
    backfill_seniority,
)

# ---------------------------------------------------------------------------
# Column-set invariants
# ---------------------------------------------------------------------------


def test_all_columns_is_union_of_bulk_and_per_row() -> None:
    assert set(ALL_COLUMNS) == set(BULK_COLUMNS_FROM_NORMALIZED) | set(PER_ROW_COLUMNS)


def test_all_columns_has_eight_unique_entries() -> None:
    assert len(ALL_COLUMNS) == 8
    assert len(set(ALL_COLUMNS)) == 8


def test_bulk_columns_match_normalized_jobs_source_set() -> None:
    """Bulk-pass columns must be exactly the 6 with deterministic normalized_jobs sources."""
    assert set(BULK_COLUMNS_FROM_NORMALIZED) == {
        "date_posted",
        "is_remote",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
    }


def test_per_row_columns_are_classifier_derived() -> None:
    """Per-row columns must be the two that require regex classifier execution."""
    assert set(PER_ROW_COLUMNS) == {"seniority_level", "role_classification"}


# ---------------------------------------------------------------------------
# experience_level → seniority mapping (closed-set sanity)
# ---------------------------------------------------------------------------


def test_experience_level_mapping_covers_jsearch_canonical_values() -> None:
    """Every JSearch canonical experience_level must map to a known seniority label."""
    canonical_jsearch = ["INTERNSHIP", "ENTRY_LEVEL", "MID_SENIOR_LEVEL", "SENIOR_LEVEL", "EXECUTIVE"]
    for level in canonical_jsearch:
        assert level in EXPERIENCE_LEVEL_TO_SENIORITY, f"{level} missing from mapping"


@pytest.mark.parametrize(
    ("experience_level", "expected_seniority"),
    [
        ("INTERNSHIP", "intern"),
        ("INTERN", "intern"),
        ("ENTRY_LEVEL", "junior"),
        ("JUNIOR", "junior"),
        ("MID_LEVEL", "mid"),
        ("MID_SENIOR_LEVEL", "mid"),
        ("SENIOR_LEVEL", "senior"),
        ("LEAD", "lead"),
        ("PRINCIPAL", "lead"),
        ("STAFF", "lead"),
        ("EXECUTIVE", "executive"),
        ("DIRECTOR", "executive"),
        ("VP", "executive"),
    ],
)
def test_experience_level_mapping_returns_closed_set_label(experience_level: str, expected_seniority: str) -> None:
    """Mapped values must be from the closed seniority set the regex classifier produces."""
    assert EXPERIENCE_LEVEL_TO_SENIORITY[experience_level] == expected_seniority


def test_experience_level_mapping_target_set_matches_classifier() -> None:
    """All mapped-to values must be from {intern, junior, mid, senior, lead, executive}."""
    closed_set = {"intern", "junior", "mid", "senior", "lead", "executive"}
    assert set(EXPERIENCE_LEVEL_TO_SENIORITY.values()).issubset(closed_set)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def test_parse_columns_default_returns_all() -> None:
    assert _parse_columns(None) == ALL_COLUMNS


def test_parse_columns_empty_string_returns_all() -> None:
    assert _parse_columns("") == ALL_COLUMNS


def test_parse_columns_subset_preserves_order_of_input() -> None:
    assert _parse_columns("is_remote,date_posted") == ("is_remote", "date_posted")


def test_parse_columns_strips_whitespace() -> None:
    assert _parse_columns(" is_remote , date_posted ") == ("is_remote", "date_posted")


def test_parse_columns_rejects_unknown_column() -> None:
    with pytest.raises(SystemExit, match="Unknown columns"):
        _parse_columns("date_posted,bogus_column")


def test_parse_columns_accepts_per_row_only() -> None:
    assert _parse_columns("seniority_level,role_classification") == ("seniority_level", "role_classification")


# ---------------------------------------------------------------------------
# Bulk pass — dry-run vs live distinction
# ---------------------------------------------------------------------------


def _mapping_first(row: dict) -> MagicMock:
    m = MagicMock()
    m.mappings.return_value.first.return_value = row
    return m


def test_backfill_bulk_columns_dry_run_does_not_execute_update() -> None:
    """Dry run must call only the count-SQL, never the UPDATE."""
    session = MagicMock()
    session.execute.return_value = _mapping_first({col: 100 for col in BULK_COLUMNS_FROM_NORMALIZED})
    counts = backfill_bulk_columns(session, dry_run=True)

    assert counts == {col: 100 for col in BULK_COLUMNS_FROM_NORMALIZED}
    # Only one execute call (the dryrun count); no commit.
    assert session.execute.call_count == 1
    session.commit.assert_not_called()


def test_backfill_bulk_columns_live_runs_update_and_commits() -> None:
    """Live mode must call the count-SQL THEN the UPDATE, and commit."""
    session = MagicMock()
    pre_count = _mapping_first({col: 50 for col in BULK_COLUMNS_FROM_NORMALIZED})
    update_result = MagicMock()
    update_result.rowcount = 50
    session.execute.side_effect = [pre_count, update_result]

    counts = backfill_bulk_columns(session, dry_run=False)

    assert counts == {col: 50 for col in BULK_COLUMNS_FROM_NORMALIZED}
    assert session.execute.call_count == 2
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Seniority pass — pass A SQL execution (mocked)
# ---------------------------------------------------------------------------


def test_backfill_seniority_pass_a_executes_in_live_mode() -> None:
    """Live mode must run the experience_level mapping UPDATE before per-row pass."""
    session = MagicMock()
    pass_a_result = MagicMock()
    pass_a_result.rowcount = 1500
    candidates_result = MagicMock()
    candidates_result.mappings.return_value.all.return_value = []  # no remaining
    remaining_result = _mapping_first({"n": 0})
    session.execute.side_effect = [pass_a_result, candidates_result, remaining_result]

    counts = backfill_seniority(session, dry_run=False, batch_size=500)

    assert counts["experience_level_mapped"] == 1500
    assert counts["regex_classified"] == 0
    assert counts["still_null"] == 0
    # First execute call should be the bulk SQL UPDATE; commit fired after.
    assert session.commit.called


def test_backfill_seniority_dry_run_uses_preview_count() -> None:
    """Dry run must use the SELECT preview, never the UPDATE."""
    session = MagicMock()
    preview_result = _mapping_first({"n": 1234})
    candidates_result = MagicMock()
    candidates_result.mappings.return_value.all.return_value = []
    remaining_result = _mapping_first({"n": 100})
    session.execute.side_effect = [preview_result, candidates_result, remaining_result]

    counts = backfill_seniority(session, dry_run=True, batch_size=500)

    assert counts["experience_level_mapped"] == 1234
    session.commit.assert_not_called()


def test_backfill_seniority_per_row_pass_skips_unknown_classifications() -> None:
    """When classify_seniority returns 'unknown', the row is left NULL (no UPDATE issued)."""
    session = MagicMock()
    pass_a_result = MagicMock()
    pass_a_result.rowcount = 0
    # Two candidates: one with no signal at all (→ "unknown"), one with "Senior" in the title.
    candidates_result = MagicMock()
    candidates_result.mappings.return_value.all.return_value = [
        {
            "job_posting_id": "11111111-1111-1111-1111-111111111111",
            "job_title": "Job Posting",  # no seniority signal
            "job_description": "Generic description without level keywords",
            "skills": None,
            "tools": None,
            "tasks": None,
            "responsibilities": None,
            "context": None,
        },
        {
            "job_posting_id": "22222222-2222-2222-2222-222222222222",
            "job_title": "Senior Software Engineer",
            "job_description": None,
            "skills": None,
            "tools": None,
            "tasks": None,
            "responsibilities": None,
            "context": None,
        },
    ]
    update_result = MagicMock()
    remaining_result = _mapping_first({"n": 1})  # unknown one stays NULL
    session.execute.side_effect = [pass_a_result, candidates_result, update_result, remaining_result]

    counts = backfill_seniority(session, dry_run=False, batch_size=500)

    # Only the senior row gets classified; the unknown one is skipped.
    assert counts["regex_classified"] == 1
    assert counts["still_null"] == 1


# ---------------------------------------------------------------------------
# Role classification pass — taxonomy load + classify_role flow
# ---------------------------------------------------------------------------


def test_backfill_role_classification_loads_taxonomies_then_processes_candidates() -> None:
    """Reference taxonomies must be loaded before per-row classification."""
    session = MagicMock()
    tech_areas = MagicMock()
    tech_areas.mappings.return_value.all.return_value = [
        {"id": "tech-1", "title": "software"},
    ]
    industry_sectors = MagicMock()
    industry_sectors.mappings.return_value.all.return_value = [
        {"id": "sec-1", "title": "technology"},
    ]
    candidates = MagicMock()
    candidates.mappings.return_value.all.return_value = []  # no candidates → no classification work
    remaining = _mapping_first({"n": 0})
    session.execute.side_effect = [tech_areas, industry_sectors, candidates, remaining]

    counts = backfill_role_classification(session, dry_run=False, batch_size=500)

    # First two SELECTs load taxonomies; third loads candidates; fourth is the final remaining count.
    assert session.execute.call_count == 4
    assert counts["classified"] == 0
    assert counts["unclassified_written"] == 0
    assert counts["still_null"] == 0


def test_backfill_role_classification_writes_unclassified_when_no_match() -> None:
    """classify_role returning 'unclassified' still writes (matches enrichment semantics)."""
    session = MagicMock()
    tech_areas = MagicMock()
    tech_areas.mappings.return_value.all.return_value = [
        {"id": "tech-1", "title": "software"},
    ]
    industry_sectors = MagicMock()
    industry_sectors.mappings.return_value.all.return_value = [
        {"id": "sec-1", "title": "technology"},
    ]
    # A candidate whose title doesn't hint and whose corpus has no overlap with taxonomies.
    candidates = MagicMock()
    candidates.mappings.return_value.all.return_value = [
        {
            "job_posting_id": "33333333-3333-3333-3333-333333333333",
            "job_title": "Mystery Role",
            "job_description": "xyz qrs nothing relevant here",
            "skills": None,
            "tools": None,
            "tasks": None,
            "responsibilities": None,
            "context": None,
        },
    ]
    update_result = MagicMock()
    remaining = _mapping_first({"n": 0})
    session.execute.side_effect = [tech_areas, industry_sectors, candidates, update_result, remaining]

    counts = backfill_role_classification(session, dry_run=False, batch_size=500)

    # Even unclassified rows get written so they leave the candidate set.
    total_written = counts["classified"] + counts["unclassified_written"]
    assert total_written == 1
