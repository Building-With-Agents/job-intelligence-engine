"""Regression tests for _SCHEMA_HINT column completeness (GitHub #171).

Validates that:
1. Columns listed in _SCHEMA_HINT for job_postings match the known-good set after
   the #170 schema promotion (date_posted, seniority_level, is_remote added).
2. Columns listed in _SCHEMA_HINT do NOT include any deprecated columns that have
   been explicitly removed (publish_date, employer_id, tech_area_id, location_id,
   posted_date, date_posted-as-missing).
3. The CRITICAL block no longer warns that date_posted is absent.
4. The LLM SQL generation path (mocked) only produces SQL referencing columns
   present in the known-good column list for job_postings.

No live database or LLM calls are made.
"""

from __future__ import annotations

import re

import pytest

from analytics.query_engine.routing import _SCHEMA_HINT

# ---------------------------------------------------------------------------
# Known-good column sets (source of truth: docs/planning/QA_DATA_CONTRACT.md)
# ---------------------------------------------------------------------------

JOB_POSTINGS_KNOWN_COLUMNS = frozenset(
    {
        "job_posting_id",
        "company_id",
        "job_title",
        "employment_type",
        "location",
        "salary_range",
        "status",
        "source",
        "external_id",
        "createdat",
        "ingestion_run_id",
        # Week 8 (#170) Q&A-ready promotions
        "date_posted",
        "seniority_level",
        "is_remote",
        # Enrichment columns
        "borderplex_subregion",
        "temporal_period",
        "spam_tier",
        "quality_score",
        "is_spam",
        "soc_code",
        "naics_code",
        "canonical_role_id",
        "employer_profile_id",
        "is_duplicate",
        "zip_code",
    }
)

DEPRECATED_COLUMNS = frozenset(
    {
        "publish_date",
        "employer_id",
        "tech_area_id",
        "start_date",
        "end_date",
        "location_id",
        "posted_date",
        # skill_id and skills are skill-join columns, not job_postings columns
        "skill_id",
    }
)


# ---------------------------------------------------------------------------
# _SCHEMA_HINT structural tests
# ---------------------------------------------------------------------------


def test_schema_hint_contains_date_posted_for_job_postings() -> None:
    """date_posted must appear in the job_postings column list after #170."""
    jp_section = _extract_job_postings_line(_SCHEMA_HINT)
    assert "date_posted" in jp_section, (
        "date_posted is missing from the job_postings column list in _SCHEMA_HINT. "
        "This column was promoted by #170 and must be listed so the LLM can reference it."
    )


def test_schema_hint_contains_seniority_level_for_job_postings() -> None:
    """seniority_level must appear in the job_postings column list after #170."""
    jp_section = _extract_job_postings_line(_SCHEMA_HINT)
    assert "seniority_level" in jp_section, (
        "seniority_level is missing from the job_postings column list in _SCHEMA_HINT."
    )


def test_schema_hint_contains_is_remote_for_job_postings() -> None:
    """is_remote must appear in the job_postings column list after #170."""
    jp_section = _extract_job_postings_line(_SCHEMA_HINT)
    assert "is_remote" in jp_section, (
        "is_remote is missing from the job_postings column list in _SCHEMA_HINT."
    )


def test_schema_hint_critical_block_no_longer_warns_date_posted_missing() -> None:
    """After #171, the CRITICAL block must NOT claim date_posted is absent from job_postings."""
    assert "has NO date_posted column" not in _SCHEMA_HINT, (
        "The CRITICAL block still says 'has NO date_posted column'. "
        "This warning must be removed after #170 added the column."
    )


@pytest.mark.parametrize("col", sorted(DEPRECATED_COLUMNS))
def test_schema_hint_does_not_list_deprecated_column_in_job_postings(col: str) -> None:
    """No deprecated column should appear in the job_postings column declaration."""
    jp_section = _extract_job_postings_line(_SCHEMA_HINT)
    assert col not in jp_section, (
        f"Deprecated column {col!r} appears in the job_postings section of _SCHEMA_HINT. "
        "Remove it to prevent hallucinated SQL."
    )


def test_schema_hint_all_known_job_postings_columns_present() -> None:
    """Every column in JOB_POSTINGS_KNOWN_COLUMNS must appear in _SCHEMA_HINT."""
    jp_section = _extract_job_postings_line(_SCHEMA_HINT)
    missing = [c for c in sorted(JOB_POSTINGS_KNOWN_COLUMNS) if c not in jp_section]
    assert not missing, (
        f"These known job_postings columns are missing from _SCHEMA_HINT: {missing}. "
        "Update _SCHEMA_HINT to reflect the current schema."
    )


# ---------------------------------------------------------------------------
# Mocked LLM SQL generation — column validation
# ---------------------------------------------------------------------------


def _extract_column_names(sql: str) -> list[str]:
    """Best-effort extraction of bare column identifiers from a SELECT clause."""
    # Strip WITH ... AS (...) CTEs for simplicity
    sql_upper = sql.upper()
    select_pos = sql_upper.find("SELECT")
    from_pos = sql_upper.find("FROM", select_pos)
    if select_pos == -1 or from_pos == -1:
        return []
    select_clause = sql[select_pos + 6 : from_pos]
    # Extract word-like tokens that look like column names (no dots, no parens)
    tokens = re.findall(r"\b([a-z_][a-z0-9_]*)\b", select_clause.lower())
    # Filter out SQL keywords and aggregate function names
    sql_keywords = {
        "as",
        "distinct",
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "case",
        "when",
        "then",
        "else",
        "end",
        "and",
        "or",
        "not",
        "null",
        "true",
        "false",
        "desc",
        "asc",
        "limit",
        "offset",
    }
    return [t for t in tokens if t not in sql_keywords]


@pytest.mark.parametrize(
    ("question", "mock_sql"),
    [
        (
            "What are the most recent job postings?",
            "SELECT job_posting_id, job_title, date_posted FROM dbo.job_postings "
            "ORDER BY date_posted DESC LIMIT 10",
        ),
        (
            "Show me senior roles posted this month",
            "SELECT job_posting_id, job_title, seniority_level, date_posted "
            "FROM dbo.job_postings WHERE seniority_level = 'senior' LIMIT 20",
        ),
        (
            "How many remote jobs were posted in Texas?",
            "SELECT COUNT(*) AS remote_count FROM dbo.job_postings "
            "WHERE is_remote = TRUE AND location ILIKE '%Texas%'",
        ),
    ],
)
def test_mock_llm_sql_references_valid_job_postings_columns(question: str, mock_sql: str) -> None:
    """SQL referencing promoted columns must pass guardrail validation."""
    from analytics.query_engine.sql_guardrails import validate_ask_the_data_sql

    ok, reason, normalized = validate_ask_the_data_sql(mock_sql)
    assert ok, (
        f"SQL for question {question!r} was rejected by guardrail. "
        f"Reason: {reason!r}. SQL: {mock_sql!r}"
    )

    col_names = _extract_column_names(mock_sql)
    unknown = [
        c for c in col_names if c not in JOB_POSTINGS_KNOWN_COLUMNS and c not in {"remote_count"}
    ]
    assert not unknown, (
        f"SQL for {question!r} references unknown job_postings columns: {unknown}. "
        f"Add them to JOB_POSTINGS_KNOWN_COLUMNS or remove from SQL."
    )


def test_schema_hint_does_not_list_posted_date_in_job_postings_declaration() -> None:
    """posted_date (never a real column) must not appear in the job_postings column declaration.

    It may appear in the CRITICAL block as a negative example — that is correct. This
    test checks only the positive column list section.
    """
    jp_section = _extract_job_postings_line(_SCHEMA_HINT)
    assert "posted_date" not in jp_section, (
        "posted_date appears in the job_postings column declaration in _SCHEMA_HINT. "
        "This column never existed; remove it from the positive column list."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_job_postings_line(hint: str) -> str:
    """Return the multi-line block that declares job_postings columns."""
    lines = hint.splitlines()
    inside = False
    block: list[str] = []
    for line in lines:
        if "job_postings(" in line:
            inside = True
        if inside:
            block.append(line)
            if line.strip().endswith(")"):
                break
    return " ".join(block)
