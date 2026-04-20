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
        # Week 8 (#173) role_classification promotion
        "role_classification",
        # Week 8 (#174) structured salary promotions
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
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


def test_schema_hint_contains_role_classification_for_job_postings() -> None:
    """role_classification must appear in the job_postings column list after #173."""
    jp_section = _extract_job_postings_line(_SCHEMA_HINT)
    assert "role_classification" in jp_section, (
        "role_classification is missing from the job_postings column list in _SCHEMA_HINT. "
        "This column was promoted by #173 and must be listed so the LLM can group/filter by role."
    )


@pytest.mark.parametrize("col", ["salary_min", "salary_max", "salary_currency", "salary_period"])
def test_schema_hint_contains_structured_salary_column(col: str) -> None:
    """Structured salary columns must appear in the job_postings column list after #174."""
    jp_section = _extract_job_postings_line(_SCHEMA_HINT)
    assert col in jp_section, (
        f"{col} is missing from the job_postings column list in _SCHEMA_HINT. "
        "This column was promoted by #174 and must be listed so the LLM can compute "
        "salary aggregations without text-parsing salary_range."
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
        (
            "How many software engineering postings does each role have? (#173 role_classification)",
            "SELECT role_classification, COUNT(*) AS posting_count FROM dbo.job_postings "
            "WHERE role_classification IS NOT NULL GROUP BY role_classification "
            "ORDER BY posting_count DESC LIMIT 20",
        ),
        (
            "What is the average annual salary in USD across data engineering roles? (#174 structured salary)",
            "SELECT AVG(salary_max) AS avg_salary_max, COUNT(*) AS posting_count "
            "FROM dbo.job_postings "
            "WHERE salary_currency = 'USD' AND salary_period = 'annual' "
            "AND role_classification ILIKE '%data engineer%' AND salary_max IS NOT NULL",
        ),
        (
            "What is the salary range floor for senior remote roles? (#173 + #174 combined)",
            "SELECT seniority_level, AVG(salary_min) AS avg_floor FROM dbo.job_postings "
            "WHERE seniority_level = 'senior' AND is_remote = TRUE "
            "AND salary_min IS NOT NULL GROUP BY seniority_level",
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
        c for c in col_names if c not in JOB_POSTINGS_KNOWN_COLUMNS
        and c not in {"remote_count", "posting_count", "avg_salary_max", "avg_floor"}
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


# ---------------------------------------------------------------------------
# JSONB unnest section (#199) — extracted_intelligence dimensions without aggregates
# ---------------------------------------------------------------------------


def test_schema_hint_contains_extracted_intelligence_section() -> None:
    """The JSONB unnest section must declare extracted_intelligence + its 5 dimensions."""
    assert "extracted_intelligence(" in _SCHEMA_HINT, (
        "extracted_intelligence is missing from _SCHEMA_HINT. The 3 no-aggregate "
        "dimensions (tasks, responsibilities, context) cannot be queried without it."
    )
    for col in ("skills", "tools", "tasks", "responsibilities", "context"):
        assert col in _SCHEMA_HINT, f"extracted_intelligence dimension '{col}' missing from hint"


def test_schema_hint_contains_jsonb_array_elements_pattern() -> None:
    """The hint must show the jsonb_array_elements unnest syntax for at least the 3 no-aggregate dims."""
    assert "jsonb_array_elements" in _SCHEMA_HINT, (
        "_SCHEMA_HINT has no jsonb_array_elements example — LLM has no way to know "
        "how to unnest tasks/responsibilities/context."
    )
    # Each of the 3 no-aggregate dimensions should have an unnest example
    for dim in ("tasks", "responsibilities", "context"):
        assert f"jsonb_array_elements(ei.{dim})" in _SCHEMA_HINT, (
            f"Missing unnest example for ei.{dim} in _SCHEMA_HINT"
        )


def test_schema_hint_documents_two_hop_join_to_extracted_intelligence() -> None:
    """The hint must document the (jp -> nj -> ei) two-hop join path."""
    assert "ei.normalized_job_id = nj.id" in _SCHEMA_HINT, (
        "_SCHEMA_HINT must document the join path: "
        "extracted_intelligence.normalized_job_id = normalized_jobs.id"
    )


def test_schema_hint_critical_block_warns_no_jsonb_columns_on_job_postings() -> None:
    """job_postings.tasks/responsibilities/context don't exist — must be called out as
    a hallucination trap (LLM sometimes assumes JSONB columns are on job_postings)."""
    assert "job_postings has NO tasks" in _SCHEMA_HINT or "NO tasks, responsibilities, or context" in _SCHEMA_HINT, (
        "CRITICAL block must warn that tasks/responsibilities/context don't live on job_postings"
    )


# Mocked-LLM SQL tests for the 3 no-aggregate dimensions
@pytest.mark.parametrize(
    ("question", "mock_sql"),
    [
        (
            "What kinds of tasks do mid-level data engineers typically handle?",
            "SELECT elem->>'task_description' AS task, COUNT(*) AS task_count "
            "FROM dbo.extracted_intelligence ei, jsonb_array_elements(ei.tasks) AS elem "
            "WHERE (elem->>'confidence')::float >= 0.75 "
            "GROUP BY task ORDER BY task_count DESC LIMIT 20",
        ),
        (
            "Which roles require AI competency at team scope?",
            "SELECT elem->>'scope' AS scope, COUNT(*) AS n "
            "FROM dbo.extracted_intelligence ei, jsonb_array_elements(ei.responsibilities) AS elem "
            "WHERE (elem->>'requires_ai_competency')::bool = TRUE "
            "GROUP BY scope ORDER BY n DESC",
        ),
        (
            "What work methodologies show up in postings?",
            "SELECT elem->>'value' AS work_methodology, COUNT(*) AS n "
            "FROM dbo.extracted_intelligence ei, jsonb_array_elements(ei.context) AS elem "
            "WHERE elem->>'signal_type' = 'work_methodology' "
            "GROUP BY work_methodology ORDER BY n DESC LIMIT 10",
        ),
    ],
)
def test_mock_llm_jsonb_unnest_sql_passes_guardrail(question: str, mock_sql: str) -> None:
    """JSONB unnest SQL against extracted_intelligence must pass the guardrail.

    extracted_intelligence is in ASK_THE_DATA_ALLOWED_TABLES, and the guardrail
    does not block jsonb_array_elements() — these queries should be permitted.
    """
    from analytics.query_engine.sql_guardrails import validate_ask_the_data_sql

    ok, reason, _ = validate_ask_the_data_sql(mock_sql)
    assert ok, (
        f"JSONB unnest SQL for {question!r} was rejected by guardrail. "
        f"Reason: {reason!r}. SQL: {mock_sql!r}"
    )


def test_mock_llm_hallucinated_task_column_on_job_postings_passes_guardrail_but_would_fail_at_execute() -> None:
    """Sanity check: the guardrail validates table allowlist + structure, NOT column existence.
    A query referencing job_postings.task_description (hallucinated) passes the guardrail but
    will fail at execute time — the schema hint exists to PREVENT the LLM from generating it
    in the first place."""
    from analytics.query_engine.sql_guardrails import validate_ask_the_data_sql

    bad = "SELECT task_description FROM dbo.job_postings LIMIT 10"
    ok, reason, _ = validate_ask_the_data_sql(bad)
    # Guardrail is structure-only; this check is to document the layered defense:
    # _SCHEMA_HINT prevents generation, guardrail catches table-level violations,
    # actual execute_safe catches column-level violations.
    assert ok, "Sanity check: the guardrail itself is structure-only and accepts this; the schema hint and actual SQL execution are the layers that catch hallucinated columns."
