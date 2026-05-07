"""Unit tests for analytics.query_engine.router (Week 8 — Query Routing).

All database I/O is mocked — no real PostgreSQL connection is required.

Strategy
--------
* Each intent test calls ``QueryRouter().route(classification, session)`` with a
  mocked ``Session`` whose ``execute()`` is intercepted. JIE #330 adds an optional
  first ``execute()`` for the dbo.skills taxonomy probe when extracted ``skill_names``
  gate applies; use ``_make_session(..., taxonomy_lower_matches=...)`` so the main
  query remains the **last** ``execute`` (see ``_compiled_sql``).
* The SQLAlchemy ``Select`` statement passed into ``execute()`` is compiled
  against the PostgreSQL dialect (in-process, no DB) and the resulting SQL
  string is inspected with ``re`` assertions:

    - Must contain ``SELECT``
    - Must contain ``LIMIT 100``
    - Must NOT contain mutating keywords (``INSERT``, ``UPDATE``, ``DELETE``,
      ``DROP``, ``CREATE``, ``ALTER``, ``TRUNCATE``, ``MERGE``)

* ``RouteResult`` field assertions (``intent``, ``tables_used``, ``routed``,
  ``is_partial``, ``confidence``) verify routing correctness independently of
  the SQL text.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from analytics.query_engine.constants import (
    NO_DATA_GEO_SKILL_SCOPE_REFUSAL,
    NO_DATA_SKILL_TAXONOMY_REFUSAL,
)
from analytics.query_engine.router import (
    ALLOWED_TABLES,
    QueryRouter,
    _is_list_style,
    _parse_weeks_back,
    _resolve_geo_terms,
    _split_geo_term,
    _tokenize_role_name,
    _week_floor,
)
from analytics.tenant_scope import get_tenant_access

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MUTATING_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|MERGE)\b",
    re.IGNORECASE,
)
_SELECT_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_LIMIT_100_RE = re.compile(r"\bLIMIT\s+100\b", re.IGNORECASE)


def _mk_classification(
    intent: str,
    *,
    confidence: float = 0.88,
    skill_names: list[str] | None = None,
    role_names: list[str] | None = None,
    geo_terms: list[str] | None = None,
    time_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Build a mock intent classification dict (output of ``intent.py``)."""
    return {
        "intent": intent,
        "confidence": confidence,
        "needs_clarification": confidence < 0.55,
        "extracted_entities": {
            "skill_names": skill_names or [],
            "role_names": role_names or [],
            "geographic_terms": geo_terms or [],
            "time_references": time_refs or [],
        },
    }


def _make_mock_row(**kwargs: Any) -> MagicMock:
    """Return a mock ORM row whose ``._mapping`` behaves like a dict."""
    row = MagicMock()
    row._mapping = kwargs
    return row


def _make_session(
    rows: list[MagicMock] | None = None,
    *,
    taxonomy_lower_matches: tuple[str, ...] | None = None,
) -> MagicMock:
    """Return a mock ``Session`` whose ``execute()`` yields *rows*.

    When ``taxonomy_lower_matches`` is set (JIE #330), the first ``execute()`` is
    the dbo.skills taxonomy probe (``scalars().all()``); the second yields ORM rows.
    """
    session = MagicMock(spec=Session)
    if taxonomy_lower_matches is not None:
        tax_r = MagicMock()
        tax_r.scalars.return_value.all.return_value = list(taxonomy_lower_matches)
        data_r = MagicMock()
        data_r.__iter__ = MagicMock(return_value=iter(rows or []))
        session.execute.side_effect = [tax_r, data_r]
        return session
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter(rows or []))
    session.execute.return_value = mock_result
    return session


def _compiled_sql(session: MagicMock) -> str:
    """Compile the ``Select`` captured by the last ``session.execute()`` call.

    Uses the PostgreSQL dialect with ``literal_binds=True`` so that numeric
    params (e.g. ``LIMIT 100``) and string params appear inline in the SQL.
    """
    calls = session.execute.call_args_list
    if not calls:
        raise AssertionError("session.execute was never called")
    stmt = calls[-1][0][0]
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _assert_sql_guardrails(sql: str) -> None:
    """Assert the core SQL guardrails on a compiled SQL string."""
    assert _SELECT_RE.search(sql), f"SQL does not contain SELECT:\n{sql}"
    assert _LIMIT_100_RE.search(sql), f"SQL does not contain LIMIT 100:\n{sql}"
    assert not _MUTATING_RE.search(sql), f"SQL contains a mutating keyword ({_MUTATING_RE.pattern}):\n{sql}"


# ---------------------------------------------------------------------------
# One test per intent
# ---------------------------------------------------------------------------


class TestIntentRouting:
    """10 tests — one per intent in the dispatch table."""

    def test_route_trend(self) -> None:
        session = _make_session(taxonomy_lower_matches=("python",))
        cls = _mk_classification("trend", skill_names=["Python"])
        result = QueryRouter().route(cls, session)

        assert result.intent == "trend"
        assert result.routed is True
        assert result.tables_used == ["skill_demand_weekly"]
        assert result.confidence == pytest.approx(0.88)
        assert "skill demand trend" in result.query_label
        assert "Python" in result.query_label

        sql = _compiled_sql(session)
        _assert_sql_guardrails(sql)
        assert "skill_demand_weekly" in sql.lower()
        assert "Python" in sql  # entity filter applied

    def test_route_role_evolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "analytics.query_engine.router._embed_texts_azure",
            lambda *args, **kwargs: None,
        )
        session = _make_session()
        cls = _mk_classification("role_evolution", role_names=["Data Analyst"])
        result = QueryRouter().route(cls, session)

        assert result.intent == "role_evolution"
        assert result.routed is True
        assert result.tables_used == ["job_postings"]
        assert "Data Analyst" in result.query_label

        sql = _compiled_sql(session)
        _assert_sql_guardrails(sql)
        assert "job_postings" in sql.lower()
        assert "temporal_period" in sql.lower()

    def test_route_disruption(self) -> None:
        session = _make_session()
        cls = _mk_classification("disruption", time_refs=["last 90 days"])
        result = QueryRouter().route(cls, session)

        assert result.intent == "disruption"
        assert result.routed is True
        assert result.tables_used == ["skill_velocity"]
        assert "disruption" in result.query_label.lower()

        sql = _compiled_sql(session)
        _assert_sql_guardrails(sql)
        assert "skill_velocity" in sql.lower()
        # Must filter only declining or volatile rows
        assert re.search(r"declining|volatile", sql, re.IGNORECASE), (
            "disruption query must filter declining/volatile trend rows"
        )

    def test_route_emergence(self) -> None:
        session = _make_session(taxonomy_lower_matches=("llm", "genai"))
        cls = _mk_classification("emergence", skill_names=["LLM", "GenAI"])
        result = QueryRouter().route(cls, session)

        assert result.intent == "emergence"
        assert result.routed is True
        assert result.tables_used == ["skill_velocity"]
        assert "emerg" in result.query_label.lower()

        sql = _compiled_sql(session)
        _assert_sql_guardrails(sql)
        assert re.search(r"emerging|accelerating", sql, re.IGNORECASE), (
            "emergence query must filter emerging/accelerating trend rows"
        )
        # Entity filters present
        assert "LLM" in sql or "GenAI" in sql

    def test_route_curriculum(self) -> None:
        session = _make_session(taxonomy_lower_matches=("sql", "tableau"))
        cls = _mk_classification("curriculum", skill_names=["SQL", "Tableau"])
        result = QueryRouter().route(cls, session)

        assert result.intent == "curriculum"
        assert result.routed is True
        assert result.tables_used == ["skill_demand_weekly"]
        assert "curriculum" in result.query_label.lower()

        sql = _compiled_sql(session)
        _assert_sql_guardrails(sql)
        assert "skill_demand_weekly" in sql.lower()
        # Must order by posting_count descending (curriculum = highest-demand skills)
        assert re.search(r"posting_count\s+DESC", sql, re.IGNORECASE), (
            "curriculum query must order by posting_count DESC"
        )

    def test_route_employer(self) -> None:
        session = _make_session()
        cls = _mk_classification("employer", role_names=["Boeing"])
        result = QueryRouter().route(cls, session)

        assert result.intent == "employer"
        assert result.routed is True
        assert "employer_profiles" in result.tables_used
        assert "companies" in result.tables_used
        assert "Boeing" in result.query_label

        sql = _compiled_sql(session)
        _assert_sql_guardrails(sql)
        assert "employer_profiles" in sql.lower()
        assert "companies" in sql.lower()
        # Must JOIN companies
        assert re.search(r"\bJOIN\b", sql, re.IGNORECASE), "employer query must JOIN companies table"

    def test_route_employer_jie346_geo_on_location_not_company_name(self) -> None:
        """JIE #346: Borderplex + kebab role slugs must not ILIKE ``company_name`` (empty evidence)."""
        session = _make_session()
        cls = _mk_classification(
            "employer",
            role_names=["clinical-data-analyst", "health-informatics"],
            geo_terms=["Borderplex"],
        )
        result = QueryRouter().route(cls, session)

        assert result.intent == "employer"
        assert result.routed is True
        sql = _compiled_sql(session).lower()
        assert "borderplex" in sql
        assert "clinical-data-analyst" not in sql
        assert "health-informatics" not in sql
        assert "normalized_location" in sql or "city" in sql or "state" in sql

    def test_route_workflow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "analytics.query_engine.router._embed_texts_azure",
            lambda *args, **kwargs: None,
        )
        session = _make_session()
        cls = _mk_classification("workflow", role_names=["ML Engineer"])
        result = QueryRouter().route(cls, session)

        assert result.intent == "workflow"
        assert result.routed is True
        assert result.tables_used == ["canonical_roles"]
        assert "ML Engineer" in result.query_label

        sql = _compiled_sql(session)
        _assert_sql_guardrails(sql)
        assert "canonical_roles" in sql.lower()

    def test_route_geographic(self) -> None:
        session = _make_session()
        cls = _mk_classification("geographic", geo_terms=["El Paso"], time_refs=["last month"])
        result = QueryRouter().route(cls, session)

        assert result.intent == "geographic"
        assert result.routed is True
        assert result.tables_used == ["geo_demand_weekly"]
        assert "El Paso" in result.query_label

        sql = _compiled_sql(session)
        _assert_sql_guardrails(sql)
        assert "geo_demand_weekly" in sql.lower()
        # Canonical alias — "El Paso" → "el_paso" exact match (no ILIKE)
        assert "el_paso" in sql.lower()
        # JIE #224: tenant-entitled subregions
        assert "dona_ana" in sql.lower() or "ciudad_juarez" in sql.lower()

    def test_route_comparison_with_skills(self) -> None:
        session = _make_session(taxonomy_lower_matches=("python", "r"))
        cls = _mk_classification("comparison", skill_names=["Python", "R"])
        result = QueryRouter().route(cls, session)

        assert result.intent == "comparison"
        assert result.routed is True
        assert result.tables_used == ["skill_demand_weekly"]
        assert "vs" in result.query_label or "comparison" in result.query_label.lower()

        sql = _compiled_sql(session)
        _assert_sql_guardrails(sql)
        assert "skill_demand_weekly" in sql.lower()

    def test_route_other(self) -> None:
        session = _make_session()
        cls = _mk_classification("other", confidence=0.30)
        result = QueryRouter().route(cls, session)

        assert result.intent == "other"
        assert result.routed is False
        assert result.error is None  # not an error — just unrouted
        assert result.confidence == pytest.approx(0.30)
        # The router must NOT touch the database for unrecognised intents
        session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Guardrail sweep — all 4 checks in a single parametrized test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent,extra_entities",
    [
        ("trend", {"skill_names": ["Python"]}),
        ("role_evolution", {"role_names": ["Data Engineer"]}),
        ("disruption", {}),
        ("emergence", {}),
        ("curriculum", {}),
        ("employer", {}),
        ("workflow", {"role_names": ["Analyst"]}),
        ("geographic", {"geo_terms": ["Las Cruces"]}),
        ("comparison", {"skill_names": ["SQL", "NoSQL"]}),
    ],
)
def test_sql_guardrails(
    intent: str,
    extra_entities: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every routable intent must produce SQL that:
    - is routed successfully
    - contains SELECT
    - contains LIMIT 100
    - contains no mutating keywords (INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE/MERGE)
    - only references tables in ALLOWED_TABLES
    """
    monkeypatch.setattr(
        "analytics.query_engine.router._embed_texts_azure",
        lambda *args, **kwargs: None,
    )
    sn = extra_entities.get("skill_names") or []
    taxonomy: tuple[str, ...] | None = None
    if intent in ("trend", "comparison") and sn:
        taxonomy = tuple(str(s).strip().lower() for s in sn)
    session = _make_session(taxonomy_lower_matches=taxonomy) if taxonomy else _make_session()
    result = QueryRouter().route(_mk_classification(intent, **extra_entities), session)

    assert result.routed, f"[{intent}] expected routed=True"

    for table in result.tables_used:
        assert table in ALLOWED_TABLES, f"[{intent}] table '{table}' not in ALLOWED_TABLES"

    sql = _compiled_sql(session)
    _assert_sql_guardrails(sql)


# ---------------------------------------------------------------------------
# Edge-case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_jie330_unknown_skill_taxonomy_blocks_trend_before_aggregate(self) -> None:
        tax_empty = MagicMock()
        tax_empty.scalars.return_value.all.return_value = []
        session = MagicMock(spec=Session)
        session.execute.return_value = tax_empty
        result = QueryRouter().route(
            _mk_classification("trend", skill_names=["quantum computing"]),
            session,
        )
        assert result.routed is True
        assert result.rows == []
        assert result.empty_rows_refusal_reason == NO_DATA_SKILL_TAXONOMY_REFUSAL
        assert session.execute.call_count == 1

    def test_is_partial_when_limit_rows_returned(self) -> None:
        """is_partial must be True when the result hits LIMIT 100."""
        rows = [_make_mock_row(skill_label=f"skill_{i}") for i in range(100)]
        session = _make_session(rows)
        result = QueryRouter().route(_mk_classification("trend"), session)
        assert result.is_partial is True
        assert result.row_count == 100

    def test_exception_in_execute_returns_routed_false(self) -> None:
        """A DB error must not propagate — router returns routed=False with error text."""
        session = _make_session()
        session.execute.side_effect = RuntimeError("DB pool exhausted")
        result = QueryRouter().route(_mk_classification("trend"), session)
        assert result.routed is False
        assert "DB pool exhausted" in (result.error or "")

    def test_comparison_without_skills_routes_to_sector(self) -> None:
        """comparison with no skill_names must branch to sector_summary_weekly."""
        session = _make_session()
        result = QueryRouter().route(_mk_classification("comparison", role_names=["Tech", "Healthcare"]), session)
        assert result.tables_used == ["sector_summary_weekly"]
        assert "sector_summary_weekly" in _compiled_sql(session).lower()

    def test_workflow_skill_fallback_casts_jsonb(self) -> None:
        """workflow with skill_names but no role_names must CAST top_skills JSONB to Text."""
        session = _make_session()
        result = QueryRouter().route(_mk_classification("workflow", skill_names=["Spark"]), session)
        assert result.routed is True
        sql = _compiled_sql(session)
        assert re.search(r"CAST|::\s*TEXT", sql, re.IGNORECASE), (
            "Expected CAST(top_skills AS TEXT) in workflow skill-fallback SQL"
        )
        assert "Spark" in sql

    def test_unknown_intent_never_hits_db(self) -> None:
        """An unrecognised intent string must not execute any SQL."""
        session = _make_session()
        result = QueryRouter().route(_mk_classification("banana_query"), session)
        assert result.routed is False
        session.execute.assert_not_called()

    def test_puget_tenant_does_not_query_market_aggregates(self) -> None:
        """JIE #224: Puget is geo-only; skill/sector/role tables must not be queried."""
        session = _make_session()
        ps = get_tenant_access("puget_sound")
        result = QueryRouter().route(_mk_classification("trend"), session, tenant=ps)
        assert result.row_count == 0
        assert result.tables_used == []
        session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# label_embedding role resolution (issue #229)
# ---------------------------------------------------------------------------


class TestRoleEmbeddingResolution:
    """Embedding path vs ILIKE fallback; _embed_texts_azure is always mocked."""

    def test_workflow_embedding_resolved_role_ids_use_pgvector_then_in_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`workflow` route filters strictly on the resolved canonical_role_ids;
        no token-ILIKE fallback because canonical_roles itself is the queried table.
        """

        def _fake_embed(texts: list[str], audit_agent_name: str = "") -> list[list[float]]:
            return [[0.1] * 1536]

        monkeypatch.setattr("analytics.query_engine.router._embed_texts_azure", _fake_embed)

        resolve_result = MagicMock()
        resolve_result.fetchall.return_value = [("resolved-role-id-aa",)]

        select_result = MagicMock()
        select_result.__iter__ = MagicMock(return_value=iter([]))

        session = MagicMock(spec=Session)
        session.execute.side_effect = [resolve_result, select_result]

        cls = _mk_classification("workflow", role_names=["Some Role Query"])
        result = QueryRouter().route(cls, session)

        assert result.routed is True
        assert session.execute.call_count == 2

        raw_sql = str(session.execute.call_args_list[0].args[0])
        assert "<=>" in raw_sql
        assert "CAST(:vec AS vector)" in raw_sql

        second_stmt = session.execute.call_args_list[1].args[0]
        compiled = str(
            second_stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "resolved-role-id-aa" in compiled
        assert "ILIKE" not in compiled.upper()

    def test_role_evolution_embedding_resolved_combines_with_token_ilike(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`role_evolution` consolidates both fixes (#229 + #294 v2 redesign):

        - Embedding-resolved canonical_role_ids → ``jp.canonical_role_id = ANY(:resolved_role_ids)``
        - Token-ILIKE on ``jp.job_title`` / ``jp.role_classification`` (Pair B's path)
        - The two predicates are OR'd so postings with NULL canonical_role_id (~68% today)
          are still recalled via token-ILIKE.
        """

        def _fake_embed(texts: list[str], audit_agent_name: str = "") -> list[list[float]]:
            return [[0.1] * 1536]

        monkeypatch.setattr("analytics.query_engine.router._embed_texts_azure", _fake_embed)

        resolve_result = MagicMock()
        resolve_result.fetchall.return_value = [("resolved-role-id-aa",), ("resolved-role-id-bb",)]

        # rep_titles_widening fetches representative_titles for the resolved IDs
        # (PR#355 C1). Empty list here keeps test focused on the canonical_role_id +
        # token-ILIKE OR'd predicates this case asserts.
        rep_titles_result = MagicMock()
        rep_titles_result.fetchall.return_value = []

        select_result = MagicMock()
        select_result.__iter__ = MagicMock(return_value=iter([]))

        session = MagicMock(spec=Session)
        session.execute.side_effect = [resolve_result, rep_titles_result, select_result]

        cls = _mk_classification("role_evolution", role_names=["Data Analyst"])
        result = QueryRouter().route(cls, session)

        assert result.routed is True
        assert result.tables_used == ["job_postings"]
        assert session.execute.call_count == 3

        # First call is the pgvector resolution against canonical_roles.
        raw_resolve_sql = str(session.execute.call_args_list[0].args[0])
        assert "<=>" in raw_resolve_sql
        assert "CAST(:vec AS vector)" in raw_resolve_sql

        # Third call is the consolidated role_evolution query against job_postings.
        third_stmt = session.execute.call_args_list[2].args[0]
        raw_sql = str(third_stmt)
        assert "jp.canonical_role_id = ANY(:resolved_role_ids)" in raw_sql
        assert "jp.job_title ILIKE" in raw_sql  # token-ILIKE still present
        # Both predicates OR'd inside a single grouping clause.
        assert " OR " in raw_sql

        # Resolved IDs are passed via execute params, not embedded in the text.
        passed_params = session.execute.call_args_list[2].args[1]
        assert passed_params.get("resolved_role_ids") == [
            "resolved-role-id-aa",
            "resolved-role-id-bb",
        ]

    @pytest.mark.parametrize("intent", ["role_evolution", "workflow"])
    def test_embedding_unavailable_falls_back_to_label_ilike(
        self,
        intent: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "analytics.query_engine.router._embed_texts_azure",
            lambda *args, **kwargs: None,
        )
        session = _make_session()
        role_term = "Data Analyst" if intent == "role_evolution" else "ML Engineer"
        cls = _mk_classification(intent, role_names=[role_term])
        result = QueryRouter().route(cls, session)

        assert result.routed is True
        sql = _compiled_sql(session)
        _assert_sql_guardrails(sql)
        assert "ILIKE" in sql.upper()


# ---------------------------------------------------------------------------
# Helper unit tests (no DB required)
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parse_weeks_back_named_periods(self) -> None:
        assert _parse_weeks_back(["last week"]) == 1
        assert _parse_weeks_back(["last month"]) == 4
        assert _parse_weeks_back(["last year"]) == 52

    def test_parse_weeks_back_n_days_conversion(self) -> None:
        assert _parse_weeks_back(["last 14 days"]) == 2

    def test_parse_weeks_back_default_on_empty(self) -> None:
        assert _parse_weeks_back([]) == 12

    def test_week_floor_is_monday(self) -> None:
        assert _week_floor(0).weekday() == 0  # Monday = 0

    def test_resolve_geo_terms_mixed(self) -> None:
        bp = get_tenant_access("borderplex")
        resolved = _resolve_geo_terms(["Juarez", "Seattle"], bp)
        assert resolved[0] == ("ciudad_juarez", True)  # canonical → exact match
        assert resolved[1] == ("Seattle", False)  # unknown → ILIKE

    def test_resolve_geo_terms_puget_seattle(self) -> None:
        ps = get_tenant_access("puget_sound")
        assert _resolve_geo_terms(["Seattle"], ps) == [("seattle_metro", True)]


# ---------------------------------------------------------------------------
# JIE #306 — list-style geographic routing
# ---------------------------------------------------------------------------


class TestListStyleDetection:
    @pytest.mark.parametrize(
        "question",
        [
            "Show all El Paso, TX postings for AI agent developer roles",
            "List every Las Cruces, NM data engineer posting",
            "Find all El Paso frontend developer postings mentioning React",
            "Pull all El Paso, TX healthcare-IT postings",
            "Retrieve all El Paso entry-level IT postings",
            "Display the Borderplex fintech postings",
            "Give me all Las Cruces AI/ML researcher postings",
        ],
    )
    def test_list_style_keywords_detected(self, question: str) -> None:
        assert _is_list_style(question), f"Should detect list-style: {question!r}"

    @pytest.mark.parametrize(
        "question",
        [
            "How many job postings are in El Paso?",
            "What are the top skills in Las Cruces?",
            "Compare El Paso and Las Cruces demand for data engineers",
            "What's the trend in Borderplex AI hiring?",
            "Which sub-region has the highest growth?",
        ],
    )
    def test_aggregate_style_not_flagged(self, question: str) -> None:
        assert not _is_list_style(question), f"Should NOT detect list-style: {question!r}"

    def test_empty_question(self) -> None:
        assert not _is_list_style("")
        assert not _is_list_style(None)  # type: ignore[arg-type]


class TestTokenizeRoleName:
    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("AI/ML researcher", ["AI", "ML", "researcher"]),
            ("applied-scientist", ["applied", "scientist"]),
            ("AI agent developer", ["AI", "agent", "developer"]),
            ("prompt engineer", ["prompt", "engineer"]),
            ("LLM engineer", ["LLM", "engineer"]),
            ("frontend developer", ["frontend", "developer"]),
            # Stopword and short-token filtering
            ("a researcher of ML", ["researcher", "ML"]),
            ("", []),
        ],
    )
    def test_tokenize(self, role: str, expected: list[str]) -> None:
        assert _tokenize_role_name(role) == expected


class TestSplitGeoTerm:
    @pytest.mark.parametrize(
        ("term", "expected"),
        [
            ("El Paso, TX", ("El Paso", "TX")),
            ("Las Cruces, NM", ("Las Cruces", "NM")),
            ("El Paso TX", ("El Paso", "TX")),
            ("Las Cruces NM", ("Las Cruces", "NM")),
            ("El Paso", ("El Paso", None)),
            ("Texas", (None, "TX")),
            ("New Mexico", (None, "NM")),
            ("", (None, None)),
        ],
    )
    def test_split_geo_term(self, term: str, expected: tuple[str | None, str | None]) -> None:
        assert _split_geo_term(term) == expected


class TestRouteGeographicListStyle:
    def test_list_style_question_routes_to_per_posting_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """JIE #306: list-style geographic question must hit job_postings, not geo_demand_weekly.

        Force embedding resolution to return no IDs so this test exercises the
        JIE #309 token ILIKE fallback (deterministic without Azure embeddings).
        """
        monkeypatch.setattr(
            "analytics.query_engine.router._embed_texts_azure",
            lambda *args, **kwargs: None,
        )
        session = _make_session(rows=[])
        cls = _mk_classification(
            "geographic",
            geo_terms=["El Paso, TX"],
            role_names=["frontend developer"],
            time_refs=["last 90 days"],
        )
        result = QueryRouter().route(
            cls,
            session,
            question="Show all El Paso, TX frontend developer postings posted in the last 90 days.",
        )

        assert result.intent == "geographic"
        assert result.routed is True
        # Per-posting path uses job_postings + postal_geo_data, NOT geo_demand_weekly.
        assert "job_postings" in result.tables_used
        assert "postal_geo_data" in result.tables_used
        assert "geo_demand_weekly" not in result.tables_used
        assert "per-posting" in result.query_label

        # The execute() call should have been made with bind params dict
        call_args = session.execute.call_args
        # Args: (statement, params_dict)
        assert len(call_args.args) >= 2
        params = call_args.args[1]
        assert params.get("city") == "El Paso"
        assert params.get("state_code") == "TX"
        # Role names are tokenized — "frontend developer" -> tokens
        # ["frontend", "developer"] each bound as %tok% on job_title AND
        # role_classification. Verify at least one token landed.
        title_keys = [k for k in params if k.startswith("role_title_")]
        cls_keys = [k for k in params if k.startswith("role_cls_")]
        assert title_keys, "expected at least one role_title bind param"
        assert cls_keys, "expected at least one role_cls bind param"
        title_values = {params[k].strip("%") for k in title_keys}
        assert {"frontend", "developer"}.issubset(title_values)

    def test_list_style_geographic_uses_canonical_role_id_when_embedding_resolves(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """JIE #309: geographic list-style matches roles via canonical_role_id when pgvector resolves."""

        def _fake_embed(texts: list[str], audit_agent_name: str = "") -> list[list[float]]:
            return [[0.1] * 1536]

        monkeypatch.setattr("analytics.query_engine.router._embed_texts_azure", _fake_embed)

        resolve_result = MagicMock()
        resolve_result.fetchall.return_value = [("resolved-canonical-role-id",)]

        # rep_titles_widening fetches representative_titles for the resolved IDs
        # (PR#355 C1). Empty list keeps the assertion surface unchanged.
        rep_titles_result = MagicMock()
        rep_titles_result.fetchall.return_value = []

        list_result = MagicMock()
        list_result.__iter__ = MagicMock(return_value=iter([]))

        session = MagicMock(spec=Session)
        session.execute.side_effect = [resolve_result, rep_titles_result, list_result]

        cls = _mk_classification(
            "geographic",
            geo_terms=["El Paso, TX"],
            role_names=["software developer"],
            time_refs=["last 90 days"],
        )
        result = QueryRouter().route(
            cls,
            session,
            question="Show all El Paso, TX postings for software developer roles posted in the last 90 days.",
        )

        assert result.routed is True
        assert session.execute.call_count == 3

        raw_resolve = str(session.execute.call_args_list[0].args[0])
        assert "<=>" in raw_resolve

        raw_list = str(session.execute.call_args_list[2].args[0])
        assert "canonical_role_id IN" in raw_list
        assert "resolved-canonical-role-id" not in raw_list

        list_params = session.execute.call_args_list[2].args[1]
        assert list_params.get("crid0") == "resolved-canonical-role-id"
        assert list_params.get("city") == "El Paso"
        title_keys = [k for k in list_params if k.startswith("role_title_")]
        assert not title_keys

    def test_jie330_geographic_aggregate_unknown_skill_taxonomy_blocks(self) -> None:
        """RT-007: unknown skill + aggregate geo must not query geo_demand_weekly."""
        tax_empty = MagicMock()
        tax_empty.scalars.return_value.all.return_value = []
        session = MagicMock(spec=Session)
        session.execute.return_value = tax_empty
        cls = _mk_classification(
            "geographic",
            geo_terms=["El Paso"],
            skill_names=["quantum computing"],
            time_refs=["last month"],
        )
        result = QueryRouter().route(
            cls,
            session,
            question="How many quantum computing jobs were posted in El Paso last month?",
        )
        assert result.routed is True
        assert result.rows == []
        assert result.tables_used == ["skills"]
        assert result.confidence <= 0.35
        assert result.empty_rows_refusal_reason == NO_DATA_SKILL_TAXONOMY_REFUSAL
        assert session.execute.call_count == 1

    def test_jie330_geographic_aggregate_known_skill_blocks_geo_scope(self) -> None:
        """Skill in taxonomy but geo_demand_weekly has no skill dimension — refuse pre-geo."""
        session = _make_session(taxonomy_lower_matches=("python",))
        cls = _mk_classification(
            "geographic",
            geo_terms=["El Paso"],
            skill_names=["Python"],
            time_refs=["last month"],
        )
        result = QueryRouter().route(
            cls,
            session,
            question="How many Python developer jobs were posted in El Paso last month?",
        )
        assert result.routed is True
        assert result.rows == []
        assert result.empty_rows_refusal_reason == NO_DATA_GEO_SKILL_SCOPE_REFUSAL
        assert session.execute.call_count == 1

    def test_aggregate_style_question_keeps_geo_demand_routing(self) -> None:
        """Non-list-style geographic question must continue routing to geo_demand_weekly."""
        session = _make_session()
        cls = _mk_classification("geographic", geo_terms=["El Paso"], time_refs=["last month"])
        result = QueryRouter().route(
            cls,
            session,
            question="How many job postings are in El Paso this month?",
        )

        assert result.intent == "geographic"
        assert result.routed is True
        assert result.tables_used == ["geo_demand_weekly"]

    def test_route_signature_question_kwarg_is_optional(self) -> None:
        """Backwards compat: callers that don't pass ``question`` still work
        (handlers default ``question=""``, list-style detection defaults to False).
        """
        session = _make_session()
        cls = _mk_classification("geographic", geo_terms=["Las Cruces"])
        result = QueryRouter().route(cls, session)  # no question= kwarg
        assert result.intent == "geographic"
        # Without question text, list-style is not detected → aggregate path.
        assert result.tables_used == ["geo_demand_weekly"]
