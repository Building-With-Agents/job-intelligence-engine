"""Workforce Intelligence Q&A — intent-to-SQL router (Analytics / Week 8).

Takes the structured output from
:func:`analytics.query_engine.intent.classify_workforce_question` and produces
parameterised ORM queries against the aggregate tables.

Guardrails (always enforced — see ``.cursor/rules/sql-guardrails.mdc``):

- **SELECT-only** ORM queries — no DDL or DML paths.
- **Allowlisted tables only** — :data:`ALLOWED_TABLES`.
- **100-row LIMIT** — configurable via ``ANALYTICS_QUERY_LIMIT`` env var.
- **30-second timeout** — configurable via ``ANALYTICS_QUERY_TIMEOUT_SECONDS``.
- Every routing decision is logged via structlog (no PII).

Usage::

    from analytics.query_engine.intent import classify_workforce_question
    from analytics.tenant_scope import get_tenant_access
    from analytics.query_engine.router import QueryRouter

    classification = classify_workforce_question("What skills are trending?")
    t = get_tenant_access("borderplex")
    with Session(engine) as session:
        result = QueryRouter().route(classification, session, tenant=t)
        for row in result.rows:
            print(row)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import structlog
from sqlalchemy import Text, cast, func, or_, select, text
from sqlalchemy.orm import Session

from analytics.query_engine.constants import (
    NO_DATA_GEO_SKILL_SCOPE_REFUSAL,
    NO_DATA_SKILL_TAXONOMY_REFUSAL,
    SKILL_TAXONOMY_GATE_CONFIDENCE_CAP,
)
from analytics.tenant_scope import TenantAccess, get_tenant_access_for_pipeline
from common.data_store.models import (
    CanonicalRole,
    Company,
    EmployerProfile,
    GeoDemandWeekly,
    SectorSummaryWeekly,
    Skill,
    SkillDemandWeekly,
    SkillVelocity,
)
from skills_extraction.extractors.taxonomy import _embed_texts_azure

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Guardrail constants
# ---------------------------------------------------------------------------

from analytics._config import query_row_limit as _query_row_limit
from analytics._config import query_timeout_seconds as _query_timeout_seconds

_QUERY_LIMIT: int = _query_row_limit()
_QUERY_TIMEOUT_SECONDS: int = _query_timeout_seconds()

#: Tables the router is permitted to query.  Extending this set requires an
#: explicit PR review — do not add raw pipeline tables here.
ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "skills",
        "skill_demand_weekly",
        "tool_demand_weekly",
        "skill_velocity",
        "skill_co_occurrence",
        "canonical_roles",
        "role_snapshot_weekly",
        "geo_demand_weekly",
        "sector_summary_weekly",
        "employer_profiles",
        "companies",
        # JIE #306 — list-style geographic queries join job_postings to
        # postal_geo_data for sub-region filtering on per-posting results.
        "job_postings",
        "postal_geo_data",
    }
)

# ---------------------------------------------------------------------------
# Geo / time helpers
# ---------------------------------------------------------------------------

# Canonical borderplex subregion tokens (locked values from integration-schema.mdc)
_BORDERPLEX_CANONICAL: frozenset[str] = frozenset(
    {"el_paso", "las_cruces", "ciudad_juarez", "dona_ana", "regional"},
)

_BORDERPLEX_ALIASES: dict[str, str] = {
    "el paso": "el_paso",
    "elpaso": "el_paso",
    "el_paso": "el_paso",
    "las cruces": "las_cruces",
    "las_cruces": "las_cruces",
    "dona ana": "dona_ana",
    "doña ana": "dona_ana",
    "dona_ana": "dona_ana",
    "ciudad juarez": "ciudad_juarez",
    "ciudad_juarez": "ciudad_juarez",
    "juarez": "ciudad_juarez",
    "regional": "regional",
}

# Puget Sound demo tenant: tokens aligned with ``tenant_scope.PUGET_ENTITLED_SUBREGIONS`` / geo rows.
_PUGET_GEO_ALIASES: dict[str, str] = {
    "tacoma": "tacoma",
    "seattle": "seattle_metro",
    "seattle metro": "seattle_metro",
    "seattle_metro": "seattle_metro",
    "bremerton": "bremerton",
    "puget sound": "tacoma",
    "puget": "tacoma",
}

# Ordered heuristic patterns for time-reference → weeks-back translation.
# Each tuple is (compiled pattern, weeks_back).  A weeks_back of -1 means
# "extract the day count from the match and convert to weeks".
_TIME_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\blast\s*week\b", re.I), 1),
    (re.compile(r"\blast\s*(?:30|thirty)\s*days?\b", re.I), 4),
    (re.compile(r"\blast\s*month\b", re.I), 4),
    (re.compile(r"\blast\s*(?:90|ninety)\s*days?\b", re.I), 13),
    (re.compile(r"\blast\s*quarter\b|Q[1-4]\b", re.I), 13),
    (re.compile(r"\blast\s*(?:6\s*months?|half\s*year)\b", re.I), 26),
    (re.compile(r"\blast\s*year\b|(?:past|previous)\s*year\b", re.I), 52),
    # Generic "N days" — converted below
    (re.compile(r"\b(\d+)\s*days?\b", re.I), -1),
]

_DEFAULT_WEEKS_BACK: int = 12


# JIE #306 — list-style geographic detection. Matches the verbs that indicate
# the user wants per-posting detail rather than an aggregate count. False
# negatives are safer than false positives here: an aggregate-style question
# wrongly flagged as list-style will still hit a valid SELECT, just with more
# rows than the user asked for; a list-style question routed to
# ``geo_demand_weekly`` returns "No data in scope" and the user gets nothing.
_LIST_STYLE_PATTERN: re.Pattern[str] = re.compile(
    r"\b(show|list|find|pull|retrieve|display|give\s+me|all\s+postings|every\s+posting|"
    r"all\s+(?:el\s+paso|las\s+cruces|borderplex)\s+postings)\b",
    re.IGNORECASE,
)


def _is_list_style(question: str) -> bool:
    """Heuristic: does the question ask for per-posting detail rather than an aggregate?"""
    return bool(question and _LIST_STYLE_PATTERN.search(question))


# Tokens that carry no role signal (filtered out before ILIKE matching).
_ROLE_TOKEN_STOPWORDS: frozenset[str] = frozenset(
    {"and", "or", "the", "of", "for", "with", "at", "in", "to", "a", "an", "any", "all"}
)

_ROLE_CONTEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        "employer",
        "employers",
        "candidate",
        "candidates",
        "hire",
        "hires",
        "recruit",
        "recruits",
        "professional",
        "professionals",
    }
)

_ROLE_CONTEXT_SCOPE_PREFIXES: frozenset[str] = frozenset(
    {
        "borderplex",
        "regional",
        "local",
        "current",
        "active",
    }
)

_ROLE_OCCUPATION_HEADS: frozenset[str] = frozenset(
    {
        "administrator",
        "analyst",
        "architect",
        "consultant",
        "developer",
        "engineer",
        "manager",
        "operator",
        "programmer",
        "researcher",
        "scientist",
        "specialist",
        "technician",
    }
)


def _tokenize_role_name(role: str) -> list[str]:
    """Split a free-text role name into ILIKE-friendly tokens.

    Multi-word role phrases extracted from user questions ("AI/ML researcher",
    "applied-scientist", "AI agent developer") rarely substring-match a single
    posting's ``job_title`` or ``role_classification``. Tokenizing widens the
    net: an "AI/ML researcher" query can match "AI Scientist", "Applied ML
    Researcher", or "Senior Applied AI Researcher".

    Tokens shorter than 2 characters and common English stopwords are dropped.
    """
    if not role:
        return []
    raw_tokens = re.split(r"[\s/,\-_]+", role.strip())
    return [t.strip() for t in raw_tokens if len(t.strip()) >= 2 and t.strip().lower() not in _ROLE_TOKEN_STOPWORDS]


def _clean_workflow_role_names(role_names: list[str]) -> list[str]:
    """Drop workflow role-context phrases that are not actual occupation names.

    LLM entity extraction can return phrases such as ``"Borderplex IT employers"``
    as role names. Those phrases describe the employer/candidate population, not
    a role family, and hard-filtering workflow queries on them can produce empty
    evidence. If a phrase ends in a context suffix but still contains an
    occupation head (for example ``"software developer candidates"``), keep the
    role portion.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in role_names:
        value = str(raw or "").strip()
        if not value:
            continue
        tokens = _tokenize_role_name(value)
        if tokens and tokens[-1].lower() in _ROLE_CONTEXT_SUFFIXES:
            role_tokens = tokens[:-1]
            while role_tokens and role_tokens[0].lower() in _ROLE_CONTEXT_SCOPE_PREFIXES:
                role_tokens = role_tokens[1:]
            if not any(t.lower() in _ROLE_OCCUPATION_HEADS for t in role_tokens):
                continue
            value = " ".join(role_tokens)
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    return cleaned


# US state-code lookup for the small set we expect in Borderplex / Puget queries.
# Full dictionary lives in ``normalization.mappers.jsearch._STATE_NAME_TO_CODE``;
# the router only needs the handful of forms it sees in geo_terms.
_GEO_STATE_CODES: dict[str, str] = {
    "tx": "TX",
    "texas": "TX",
    "nm": "NM",
    "new mexico": "NM",
    "wa": "WA",
    "washington": "WA",
}


def _split_geo_term(term: str) -> tuple[str | None, str | None]:
    """Parse a geo term like 'El Paso, TX' or 'Las Cruces NM' into (city, state_code).

    Returns ``(None, None)`` if no state hint is found; callers should fall
    back to county / city-ILIKE matching.
    """
    if not term:
        return (None, None)
    cleaned = term.strip().rstrip(".")
    # split on comma first — "El Paso, TX"
    if "," in cleaned:
        city_part, state_part = cleaned.rsplit(",", 1)
        state_code = _GEO_STATE_CODES.get(state_part.strip().lower())
        return (city_part.strip(), state_code)
    # otherwise the last token may be the state — "El Paso TX" / "Las Cruces NM"
    parts = cleaned.split()
    if len(parts) >= 2:
        last = parts[-1].lower()
        state_code = _GEO_STATE_CODES.get(last)
        if state_code:
            return (" ".join(parts[:-1]), state_code)
    # try the whole term as a state name
    state_code = _GEO_STATE_CODES.get(cleaned.lower())
    if state_code:
        return (None, state_code)
    return (cleaned, None)


def _parse_weeks_back(time_refs: list[str]) -> int:
    """Heuristically map free-text time references to a *weeks-back* integer."""
    for ref in time_refs:
        for pattern, weeks in _TIME_PATTERNS:
            m = pattern.search(ref)
            if not m:
                continue
            if weeks >= 0:
                return weeks
            # weeks == -1 → extract day count from the first capture group
            try:
                days = int(m.group(1))
                return max(1, round(days / 7))
            except (IndexError, ValueError):
                pass
    return _DEFAULT_WEEKS_BACK


def _week_floor(weeks_back: int) -> date:
    """Return the Monday-anchored ``week_start`` that is *weeks_back* weeks ago."""
    today = date.today()
    current_monday = today - timedelta(days=today.weekday())
    return current_monday - timedelta(weeks=weeks_back)


def _resolve_geo_terms(geo_terms: list[str], tenant: TenantAccess) -> list[tuple[str, bool]]:
    """Map geo terms to ``(value, is_exact)`` pairs, honoring the tenant’s entitled subregions."""
    if tenant.tenant_id == "puget_sound":
        alias_map: dict[str, str] = _PUGET_GEO_ALIASES
    else:
        alias_map = _BORDERPLEX_ALIASES
    out: list[tuple[str, bool]] = []
    for term in geo_terms:
        key = term.lower().strip()
        canonical = alias_map.get(key)
        if canonical:
            if canonical not in tenant.allowed_subregions:
                continue
            out.append((canonical, True))
        else:
            out.append((term, False))
    return out


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class RouteResult:
    """Structured output from :class:`QueryRouter`.

    Attributes:
        intent:       The intent label that was routed.
        tables_used:  Allowlisted tables queried.
        query_label:  Human-readable description of the query.
        rows:         Serialised result rows (list of plain dicts).
        row_count:    ``len(rows)``; set even when ``routed=False``.
        is_partial:   ``True`` when the result was capped at ``QUERY_LIMIT``.
        routed:       ``False`` if the intent could not be mapped to a query
                      or the query raised an exception.
        error:        Error message when ``routed=False``.
        confidence:   Forwarded from the intent classifier (may be capped by gates).
        empty_rows_refusal_reason: When rows are intentionally empty before aggregate
            SQL (JIE #330), a deterministic user-facing refusal line for evidence.
    """

    intent: str
    tables_used: list[str]
    query_label: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    is_partial: bool = False
    routed: bool = True
    error: str | None = None
    confidence: float = 0.0
    empty_rows_refusal_reason: str | None = None


# JIE #330 — intents that read skill-scoped aggregates; every extracted skill_name must
# exist in dbo.skills before we query (case-insensitive full-name equality only).
_SKILL_AGG_INTENTS_REQUIRING_TAXONOMY: frozenset[str] = frozenset(
    {"trend", "disruption", "emergence", "comparison", "curriculum"},
)


def _skill_terms_all_in_dbo_skills(session: Session, skill_names: list[str], *, max_terms: int = 5) -> bool:
    """True iff each distinct extracted skill term matches ``dbo.skills.skill_name`` (ci exact).

    Matching uses **case-insensitive equality on the full stored skill_name** only.
    Substring or ``ILIKE '%term%'`` is intentionally avoided: short tokens and
    ambiguous fragments would otherwise match unrelated taxonomy rows and let
    broad aggregates pass the gate (the RT-007 failure mode).
    """
    lowered: list[str] = []
    for raw in skill_names[:max_terms]:
        t = str(raw).strip().lower()
        if t:
            lowered.append(t)
    if not lowered:
        return True
    distinct = list(dict.fromkeys(lowered))
    stmt = select(func.lower(Skill.skill_name)).where(func.lower(Skill.skill_name).in_(distinct))
    matched = set(session.execute(stmt).scalars().all())
    return all(term in matched for term in distinct)


def _skill_taxonomy_block_result(
    intent: str,
    classification_confidence: float,
    *,
    refusal_reason: str,
) -> RouteResult:
    cap = min(float(classification_confidence), SKILL_TAXONOMY_GATE_CONFIDENCE_CAP)
    return RouteResult(
        intent=intent,
        tables_used=["skills"],
        query_label="skill taxonomy gate",
        rows=[],
        row_count=0,
        routed=True,
        confidence=cap,
        empty_rows_refusal_reason=refusal_reason,
    )


def _apply_skill_taxonomy_and_geo_scope_gates(
    session: Session,
    intent: str,
    classification_confidence: float,
    skill_names: list[str],
    question: str,
) -> RouteResult | None:
    """Pre-handler gate for JIE #330: taxonomy match + geo/skill scope honesty."""
    qtext = question or ""
    if intent == "geographic" and skill_names and not _is_list_style(qtext):
        if not _skill_terms_all_in_dbo_skills(session, skill_names):
            log.info(
                "skill_taxonomy_gate_blocked",
                intent=intent,
                reason="taxonomy_miss",
                skill_preview=skill_names[:5],
            )
            return _skill_taxonomy_block_result(
                intent,
                classification_confidence,
                refusal_reason=NO_DATA_SKILL_TAXONOMY_REFUSAL,
            )
        log.info(
            "geo_aggregate_skill_scope_blocked",
            intent=intent,
            reason="geo_not_scoped_by_skill",
            skill_preview=skill_names[:5],
        )
        return _skill_taxonomy_block_result(
            intent,
            classification_confidence,
            refusal_reason=NO_DATA_GEO_SKILL_SCOPE_REFUSAL,
        )

    if (
        intent in _SKILL_AGG_INTENTS_REQUIRING_TAXONOMY
        and skill_names
        and not _skill_terms_all_in_dbo_skills(session, skill_names)
    ):
        log.info(
            "skill_taxonomy_gate_blocked",
            intent=intent,
            reason="taxonomy_miss",
            skill_preview=skill_names[:5],
        )
        return _skill_taxonomy_block_result(
            intent,
            classification_confidence,
            refusal_reason=NO_DATA_SKILL_TAXONOMY_REFUSAL,
        )
    return None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class QueryRouter:
    """Routes intent classifications to parameterised ORM queries.

    Each of the 10 intents defined in ``intent.py`` maps to a private handler
    that builds a SQLAlchemy ``select()`` statement, applies guardrails, and
    returns a :class:`RouteResult`.

    The router is stateless — instantiate once and call :meth:`route` for
    every incoming classification.
    """

    # Public entry point -------------------------------------------------------

    def route(
        self,
        classification: dict[str, Any],
        session: Session,
        *,
        tenant: TenantAccess | None = None,
        question: str = "",
    ) -> RouteResult:
        """Dispatch a classification dict to the appropriate query handler.

        Args:
            classification: Output of ``classify_workforce_question`` — keys
                ``intent`` (str), ``confidence`` (float),
                ``extracted_entities`` (dict).
            session: Open SQLAlchemy ``Session`` (read path; the router never
                issues write statements).
            tenant: Entitled subregions + aggregate exposure (JIE #224 / ``X-Tenant-Id``).
            question: Original user question text. Forwarded to handlers that
                need to inspect user wording (e.g. ``_route_geographic`` uses
                this to detect list-style vs aggregate-style — JIE #306).

        Returns:
            :class:`RouteResult` with result rows or structured error details.
        """
        taccess: TenantAccess = tenant or get_tenant_access_for_pipeline(None)
        intent: str = str(classification.get("intent") or "other")
        confidence: float = float(classification.get("confidence") or 0.0)
        entities: dict[str, list[str]] = classification.get("extracted_entities") or {}

        issue197_hint = classification.get("issue197_sql_guard_hint")
        if isinstance(issue197_hint, str) and issue197_hint.strip():
            log.info(
                "issue197_sql_guard_hint_in_router_context",
                intent=intent,
                hint_preview=issue197_hint.strip()[:200],
            )

        skill_names: list[str] = entities.get("skill_names") or []
        role_names: list[str] = entities.get("role_names") or []
        geo_terms: list[str] = entities.get("geographic_terms") or []
        time_refs: list[str] = entities.get("time_references") or []

        weeks_back = _parse_weeks_back(time_refs)
        wfloor = _week_floor(weeks_back)

        log.info(
            "query_router_dispatch",
            intent=intent,
            confidence=confidence,
            weeks_back=weeks_back,
            skill_names=skill_names[:5],
            role_names=role_names[:3],
            geo_terms=geo_terms[:3],
        )

        blocked = _apply_skill_taxonomy_and_geo_scope_gates(session, intent, confidence, skill_names, question)
        if blocked is not None:
            return blocked

        handler: Callable[..., RouteResult] | None = _INTENT_HANDLERS.get(intent)
        if handler is None:
            return self._route_other(
                intent=intent,
                confidence=confidence,
                skill_names=skill_names,
                role_names=role_names,
                geo_terms=geo_terms,
                week_floor=wfloor,
                session=session,
                tenant=taccess,
                question=question,
            )

        try:
            return handler(
                self,
                intent=intent,
                confidence=confidence,
                skill_names=skill_names,
                role_names=role_names,
                geo_terms=geo_terms,
                week_floor=wfloor,
                session=session,
                tenant=taccess,
                question=question,
            )
        except Exception as exc:
            log.error(
                "query_router_handler_error",
                intent=intent,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return RouteResult(
                intent=intent,
                tables_used=[],
                query_label=f"{intent} (handler error)",
                routed=False,
                error=str(exc),
                confidence=confidence,
            )

    # Internal helpers ---------------------------------------------------------

    def _execute(
        self,
        session: Session,
        stmt: Any,
        *,
        intent: str,
        tables_used: list[str],
        query_label: str,
        confidence: float,
    ) -> RouteResult:
        """Apply LIMIT + timeout, execute, serialise rows, return RouteResult."""
        try:
            bounded = stmt.limit(_QUERY_LIMIT)
            result = session.execute(
                bounded,
                execution_options={"timeout": _QUERY_TIMEOUT_SECONDS},
            )
            rows = [dict(row._mapping) for row in result]
            is_partial = len(rows) >= _QUERY_LIMIT
            log.info(
                "query_router_result",
                intent=intent,
                query_label=query_label,
                row_count=len(rows),
                is_partial=is_partial,
                tables_used=tables_used,
            )
            return RouteResult(
                intent=intent,
                tables_used=tables_used,
                query_label=query_label,
                rows=rows,
                row_count=len(rows),
                is_partial=is_partial,
                confidence=confidence,
            )
        except Exception as exc:
            log.error(
                "query_router_execute_error",
                intent=intent,
                query_label=query_label,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return RouteResult(
                intent=intent,
                tables_used=tables_used,
                query_label=query_label,
                routed=False,
                error=str(exc),
                confidence=confidence,
            )

    @staticmethod
    def _ilike_or(column: Any, names: list[str], max_terms: int = 5) -> Any | None:
        """Build an ``OR ILIKE`` filter clause for *names* against *column*.

        Returns ``None`` when *names* is empty so callers can skip the
        ``.where()`` call cleanly.
        """
        if not names:
            return None
        clauses = [column.ilike(f"%{n}%") for n in names[:max_terms]]
        return or_(*clauses) if len(clauses) > 1 else clauses[0]

    @staticmethod
    def _ilike_company_location_or(co: Any, geo_terms: list[str], max_terms: int = 5) -> Any | None:
        """Match geo hints against company HQ columns — not ``company_name`` (JIE #346)."""
        terms = [str(t).strip() for t in geo_terms[:max_terms] if t and str(t).strip()]
        if not terms:
            return None
        per_term: list[Any] = []
        for term in terms:
            pat = f"%{term}%"
            per_term.append(
                or_(
                    co.city.ilike(pat),
                    co.state.ilike(pat),
                    co.normalized_location.ilike(pat),
                )
            )
        return or_(*per_term) if len(per_term) > 1 else per_term[0]

    @staticmethod
    def _employer_company_name_hints(role_names: list[str], *, max_terms: int = 5) -> list[str]:
        """Role tokens safe for ``company_name`` ILIKE — drop kebab-cased occupational slugs (JIE #346)."""
        hints: list[str] = []
        for raw in role_names:
            if len(hints) >= max_terms:
                break
            s = str(raw).strip()
            if not s or len(s) > 80:
                continue
            parts = [p for p in s.split("-") if p]
            if len(parts) >= 3 and all(p.isalnum() for p in parts):
                continue
            if (
                len(parts) == 2
                and s == s.lower()
                and all(p.isalnum() for p in parts)
                and min(len(p) for p in parts) >= 4
            ):
                continue
            hints.append(s)
        return hints

    @staticmethod
    def _resolve_role_names_to_canonical_ids(
        role_names: list[str],
        session: Session,
        *,
        top_k: int = 5,
    ) -> list[str]:
        """Resolve free-text role names to canonical role_ids via label_embedding similarity.

        Returns an empty list when ``role_names`` is empty, embedding env/API is
        unavailable, or no rows have ``label_embedding`` populated. Callers
        should fall back to ILIKE on ``CanonicalRole.label``.
        """
        if not role_names:
            return []
        joined = " ".join(role_names).strip()
        if not joined:
            return []
        vectors = _embed_texts_azure([joined], audit_agent_name="analytics-query-router")
        if vectors is None or not vectors or not vectors[0]:
            return []
        embedding = vectors[0]
        if not embedding:
            return []
        vec_str = str([float(v) for v in embedding])
        rows = session.execute(
            text(
                """
                SELECT role_id
                FROM dbo.canonical_roles
                WHERE label_embedding IS NOT NULL
                ORDER BY label_embedding <=> CAST(:vec AS vector)
                LIMIT :top_k
                """
            ),
            {"vec": vec_str, "top_k": top_k},
        ).fetchall()
        return [str(row[0]) for row in rows]

    @staticmethod
    def _resolve_representative_titles(
        canonical_role_ids: list[str],
        session: Session,
        *,
        max_titles: int = 25,
    ) -> list[str]:
        """Return the union of ``representative_titles`` for the given canonical
        roles, deduplicated and capped at ``max_titles``.

        Used by routes that query ``dbo.job_postings`` directly to widen recall
        onto postings whose ``canonical_role_id`` is NULL (~48% of the corpus
        as of 2026-05-02 re-cluster). The clustering pipeline persists the
        member titles per canonical role; ILIKE-matching those titles against
        ``jp.job_title`` catches NULL-canonical_role_id postings that genuinely
        belong to the resolved role family but missed direct assignment.

        Returns an empty list when ``canonical_role_ids`` is empty or no rows
        have populated ``representative_titles``. Cap of 25 keeps the OR'd
        ILIKE clause tractable at top_k=5 × ~5 rep titles per role.
        """
        if not canonical_role_ids:
            return []
        rows = session.execute(
            text(
                """
                SELECT representative_titles
                FROM dbo.canonical_roles
                WHERE role_id = ANY(:ids)
                  AND representative_titles IS NOT NULL
                """
            ),
            {"ids": canonical_role_ids},
        ).fetchall()
        seen: set[str] = set()
        titles: list[str] = []
        for row in rows:
            rt = row[0] or []
            if not isinstance(rt, list):
                continue
            for t in rt:
                if not t or not isinstance(t, str):
                    continue
                norm = t.strip()
                if not norm or norm.lower() in seen:
                    continue
                seen.add(norm.lower())
                titles.append(norm)
                if len(titles) >= max_titles:
                    return titles
        return titles

    @staticmethod
    def _build_role_token_ilike_clauses(role_names: list[str], params: dict[str, Any]) -> list[str]:
        """Build OR'd token-ILIKE clauses against ``jp.job_title``/``jp.role_classification``.

        Tokenises up to 5 role names (max 10 unique tokens), populates ``params``
        with bind values keyed ``rt_<i>`` / ``rc_<i>``, and returns the list of
        clause fragments. Caller is responsible for OR-joining the fragments.
        Used by ``_route_role_evolution`` to widen recall on postings whose
        ``canonical_role_id`` is NULL.
        """
        clauses: list[str] = []
        seen_tokens: set[str] = set()
        token_index = 0
        for role in role_names[:5]:
            for tok in _tokenize_role_name(role):
                norm = tok.lower()
                if norm in seen_tokens or token_index >= 10:
                    continue
                seen_tokens.add(norm)
                t_key = f"rt_{token_index}"
                c_key = f"rc_{token_index}"
                clauses.append(f"(jp.job_title ILIKE :{t_key} OR jp.role_classification ILIKE :{c_key})")
                params[t_key] = f"%{tok}%"
                params[c_key] = f"%{tok}%"
                token_index += 1
        return clauses

    @staticmethod
    def _no_borderplex_market_aggregates(intent: str, confidence: float) -> RouteResult:
        """Subregion-only tenant: no Borderplex-wide skill/role/sector tables (JIE #224)."""
        return RouteResult(
            intent=intent,
            tables_used=[],
            query_label="no market-wide aggregate for this tenant (subregion geo only)",
            rows=[],
            row_count=0,
            confidence=confidence,
        )

    # Intent handlers ----------------------------------------------------------

    def _route_trend(
        self,
        *,
        intent: str,
        confidence: float,
        skill_names: list[str],
        role_names: list[str],
        geo_terms: list[str],
        week_floor: date,
        session: Session,
        tenant: TenantAccess,
        question: str = "",
    ) -> RouteResult:
        """trend → ``skill_demand_weekly`` ordered by demand, optionally filtered by skill."""
        if not tenant.can_query_borderplex_skill_tables:
            return self._no_borderplex_market_aggregates(intent, confidence)
        t = SkillDemandWeekly
        stmt = (
            select(
                t.skill_label,
                t.esco_uri,
                t.week_start,
                t.posting_count,
                t.employer_count,
                t.computed_at,
            )
            .where(t.week_start >= week_floor)
            .order_by(t.week_start.desc(), t.posting_count.desc())
        )

        skill_filter = self._ilike_or(t.skill_label, skill_names)
        if skill_filter is not None:
            stmt = stmt.where(skill_filter)

        label = "skill demand trend"
        if skill_names:
            label += f" — {', '.join(skill_names[:3])}"

        return self._execute(
            session,
            stmt,
            intent=intent,
            tables_used=["skill_demand_weekly"],
            query_label=label,
            confidence=confidence,
        )

    def _route_role_evolution(
        self,
        *,
        intent: str,
        confidence: float,
        skill_names: list[str],
        role_names: list[str],
        geo_terms: list[str],
        week_floor: date,
        session: Session,
        tenant: TenantAccess,
        question: str = "",
    ) -> RouteResult:
        """role_evolution → ``job_postings`` grouped by ``temporal_period``.

        Prompt-iteration v2 fix (2026-04-30): the previous implementation queried
        ``canonical_roles``, a static snapshot with no ``temporal_period`` dimension.
        Every role-evolution question asks for a before/after comparison across
        temporal periods (pre_chatgpt → early_genai → post_gpt4 → agentic_era).
        Without temporal data the synthesis layer correctly refused to fabricate
        trends, producing ``decision_relevance = 0`` across all annotated questions.

        This version queries ``job_postings`` which carries the authoritative
        ``temporal_period`` value set by the enrichment classifier, grouped by
        period and seniority level so the synthesis can report posting volume
        trajectories per temporal era.  A secondary JOIN to ``canonical_roles``
        via ``canonical_role_id`` is included when available to surface top_skills
        and top_tools for the role cluster.
        """
        if not tenant.can_query_borderplex_skill_tables:
            return self._no_borderplex_market_aggregates(intent, confidence)

        allowed_subregions = list(tenant.allowed_subregions)

        # Consolidated role-name resolution (#229 + #294 v2 redesign):
        #   - Pair A's pgvector resolver (drift-proof, paraphrase-tolerant) returns
        #     canonical_role_ids when role_names match a cluster centroid.
        #   - Pair B's token-ILIKE on jp.job_title / jp.role_classification catches
        #     postings whose canonical_role_id is still NULL (audit found ~68% of
        #     dbo.job_postings have NULL canonical_role_id today).
        # The two predicates are OR'd so we don't lose recall on either side.
        params: dict[str, Any] = {
            "subregions": allowed_subregions,
        }
        where_parts: list[str] = [
            "jp.temporal_period IS NOT NULL",
            "jp.is_spam = FALSE",
        ]
        if allowed_subregions:
            where_parts.append("jp.borderplex_subregion = ANY(:subregions)")

        if role_names:
            resolved_ids = self._resolve_role_names_to_canonical_ids(role_names, session)
            role_clauses = self._build_role_token_ilike_clauses(role_names, params)

            predicates: list[str] = []
            if resolved_ids:
                params["resolved_role_ids"] = resolved_ids
                predicates.append("jp.canonical_role_id = ANY(:resolved_role_ids)")
                # Widen recall onto NULL-canonical_role_id postings whose title
                # matches a representative_title of any resolved canonical role.
                rep_titles = self._resolve_representative_titles(resolved_ids, session)
                rep_clauses: list[str] = []
                for i, rep_title in enumerate(rep_titles):
                    key = f"rep_title_{i}"
                    params[key] = f"%{rep_title}%"
                    rep_clauses.append(f"jp.job_title ILIKE :{key}")
                if rep_clauses:
                    predicates.append("(" + " OR ".join(rep_clauses) + ")")
                log.info(
                    "query_router_role_evolution_embedding_route",
                    resolved_count=len(resolved_ids),
                    rep_titles_count=len(rep_titles),
                )
            if role_clauses:
                predicates.append("(" + " OR ".join(role_clauses) + ")" if len(role_clauses) > 1 else role_clauses[0])
                if not resolved_ids:
                    log.info(
                        "query_router_role_evolution_ilike_fallback",
                        role_names_count=len(role_names),
                    )
            if predicates:
                where_parts.append("(" + " OR ".join(predicates) + ")" if len(predicates) > 1 else predicates[0])
                # Issue #197 — exclude the known mis-bucketed placeholder.
                where_parts.append("(jp.role_classification IS NULL OR jp.role_classification <> 'N/A Not an IT role')")

        sql = text(
            f"""
            SELECT
                jp.temporal_period,
                jp.borderplex_subregion,
                jp.seniority_level,
                COUNT(DISTINCT jp.job_posting_id) AS posting_count,
                COUNT(DISTINCT jp.company_id)      AS employer_count
            FROM dbo.job_postings AS jp
            WHERE {" AND ".join(where_parts)}
            GROUP BY jp.temporal_period, jp.borderplex_subregion, jp.seniority_level
            ORDER BY jp.temporal_period, posting_count DESC
            LIMIT {_QUERY_LIMIT}
            """
        )

        label = "role evolution by temporal period"
        if role_names:
            label += f" — {', '.join(role_names[:3])}"

        try:
            result = session.execute(
                sql,
                params,
                execution_options={"timeout": _QUERY_TIMEOUT_SECONDS},
            )
            rows = [dict(row._mapping) for row in result]
            is_partial = len(rows) >= _QUERY_LIMIT
            log.info(
                "query_router_result",
                intent=intent,
                query_label=label,
                row_count=len(rows),
                is_partial=is_partial,
                tables_used=["job_postings"],
            )
            return RouteResult(
                intent=intent,
                tables_used=["job_postings"],
                query_label=label,
                rows=rows,
                row_count=len(rows),
                is_partial=is_partial,
                confidence=confidence,
            )
        except Exception as exc:
            log.error(
                "query_router_execute_error",
                intent=intent,
                query_label=label,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return RouteResult(
                intent=intent,
                tables_used=["job_postings"],
                query_label=label,
                routed=False,
                error=str(exc),
                confidence=confidence,
            )

    def _route_disruption(
        self,
        *,
        intent: str,
        confidence: float,
        skill_names: list[str],
        role_names: list[str],
        geo_terms: list[str],
        week_floor: date,
        session: Session,
        tenant: TenantAccess,
        question: str = "",
    ) -> RouteResult:
        """disruption → ``skill_velocity`` rows with declining or volatile trends."""
        if not tenant.can_query_borderplex_skill_tables:
            return self._no_borderplex_market_aggregates(intent, confidence)
        sv = SkillVelocity
        stmt = (
            select(
                sv.skill_label,
                sv.esco_uri,
                sv.velocity_week,
                sv.demand_count,
                sv.week_over_week_change,
                sv.four_week_trend,
                sv.trend_confidence,
            )
            .where(
                sv.four_week_trend.in_(["declining", "volatile"]),
                sv.velocity_week >= week_floor,
            )
            .order_by(sv.week_over_week_change.asc(), sv.velocity_week.desc())
        )

        skill_filter = self._ilike_or(sv.skill_label, skill_names)
        if skill_filter is not None:
            stmt = stmt.where(skill_filter)

        label = "disruption — declining / volatile skill trends"
        if skill_names:
            label += f" — {', '.join(skill_names[:3])}"

        return self._execute(
            session,
            stmt,
            intent=intent,
            tables_used=["skill_velocity"],
            query_label=label,
            confidence=confidence,
        )

    def _route_emergence(
        self,
        *,
        intent: str,
        confidence: float,
        skill_names: list[str],
        role_names: list[str],
        geo_terms: list[str],
        week_floor: date,
        session: Session,
        tenant: TenantAccess,
        question: str = "",
    ) -> RouteResult:
        """emergence → ``skill_velocity`` rows with emerging or accelerating trends."""
        if not tenant.can_query_borderplex_skill_tables:
            return self._no_borderplex_market_aggregates(intent, confidence)
        sv = SkillVelocity
        stmt = (
            select(
                sv.skill_label,
                sv.esco_uri,
                sv.velocity_week,
                sv.demand_count,
                sv.week_over_week_change,
                sv.four_week_trend,
                sv.trend_confidence,
            )
            .where(
                sv.four_week_trend.in_(["emerging", "accelerating"]),
                sv.velocity_week >= week_floor,
            )
            .order_by(sv.week_over_week_change.desc(), sv.velocity_week.desc())
        )

        skill_filter = self._ilike_or(sv.skill_label, skill_names)
        if skill_filter is not None:
            stmt = stmt.where(skill_filter)

        label = "emerging / accelerating skills"
        if skill_names:
            label += f" — {', '.join(skill_names[:3])}"

        return self._execute(
            session,
            stmt,
            intent=intent,
            tables_used=["skill_velocity"],
            query_label=label,
            confidence=confidence,
        )

    def _route_curriculum(
        self,
        *,
        intent: str,
        confidence: float,
        skill_names: list[str],
        role_names: list[str],
        geo_terms: list[str],
        week_floor: date,
        session: Session,
        tenant: TenantAccess,
        question: str = "",
    ) -> RouteResult:
        """curriculum → top demanded skills from ``skill_demand_weekly`` for upskilling."""
        if not tenant.can_query_borderplex_skill_tables:
            return self._no_borderplex_market_aggregates(intent, confidence)
        t = SkillDemandWeekly
        stmt = (
            select(
                t.skill_label,
                t.esco_uri,
                t.week_start,
                t.posting_count,
                t.employer_count,
                t.computed_at,
            )
            .where(t.week_start >= week_floor)
            .order_by(t.posting_count.desc(), t.week_start.desc())
        )

        skill_filter = self._ilike_or(t.skill_label, skill_names)
        if skill_filter is not None:
            stmt = stmt.where(skill_filter)

        label = "top demanded skills for curriculum"
        if skill_names:
            label += f" — {', '.join(skill_names[:3])}"

        return self._execute(
            session,
            stmt,
            intent=intent,
            tables_used=["skill_demand_weekly"],
            query_label=label,
            confidence=confidence,
        )

    def _route_employer(
        self,
        *,
        intent: str,
        confidence: float,
        skill_names: list[str],
        role_names: list[str],
        geo_terms: list[str],
        week_floor: date,
        session: Session,
        tenant: TenantAccess,
        question: str = "",
    ) -> RouteResult:
        """employer → ``employer_profiles`` JOIN ``companies``."""
        if not tenant.can_query_borderplex_skill_tables:
            return self._no_borderplex_market_aggregates(intent, confidence)
        ep = EmployerProfile
        co = Company
        stmt = (
            select(
                ep.company_id,
                co.company_name,
                ep.company_size,
                ep.ai_maturity_signal,
                ep.sector,
                ep.is_known_employer,
                ep.created_at,
            )
            .join(co, co.company_id == ep.company_id)
            .order_by(ep.is_known_employer.desc(), co.company_name.asc())
        )

        company_hints = self._employer_company_name_hints(role_names)
        name_filter = self._ilike_or(co.company_name, company_hints)
        if name_filter is not None:
            stmt = stmt.where(name_filter)

        loc_filter = self._ilike_company_location_or(co, geo_terms)
        if loc_filter is not None:
            stmt = stmt.where(loc_filter)

        label = "employer profiles"
        label_bits = company_hints + geo_terms
        if label_bits:
            label += f" — {', '.join(label_bits[:3])}"

        return self._execute(
            session,
            stmt,
            intent=intent,
            tables_used=["employer_profiles", "companies"],
            query_label=label,
            confidence=confidence,
        )

    def _route_workflow(
        self,
        *,
        intent: str,
        confidence: float,
        skill_names: list[str],
        role_names: list[str],
        geo_terms: list[str],
        week_floor: date,
        session: Session,
        tenant: TenantAccess,
        question: str = "",
    ) -> RouteResult:
        """workflow → ``canonical_roles`` (top_skills, top_tools) for day-to-day tasks."""
        if not tenant.can_query_borderplex_skill_tables:
            return self._no_borderplex_market_aggregates(intent, confidence)
        role_names = _clean_workflow_role_names(role_names)
        cr = CanonicalRole
        stmt = select(
            cr.role_id,
            cr.label,
            cr.description,
            cr.top_skills,
            cr.top_tools,
            cr.posting_count,
            cr.representative_titles,
        ).order_by(cr.posting_count.desc())

        # Embedding resolution first; ILIKE only when resolved_ids is empty (no prior ILIKE).
        if role_names:
            resolved_ids = self._resolve_role_names_to_canonical_ids(role_names, session)
            if resolved_ids:
                stmt = stmt.where(cr.role_id.in_(resolved_ids))
                log.info(
                    "query_router_workflow_embedding_route",
                    resolved_count=len(resolved_ids),
                )
            else:
                role_filter = self._ilike_or(cr.label, role_names)
                if role_filter is not None:
                    stmt = stmt.where(role_filter)
                log.info(
                    "query_router_workflow_ilike_fallback",
                    role_names_count=len(role_names),
                )
            label = f"role workflow — {', '.join(role_names[:3])}"
        elif skill_names:
            # Best-effort: cast JSONB top_skills to text and ILIKE-search skill names
            skill_clauses = [cast(cr.top_skills, Text).ilike(f"%{s}%") for s in skill_names[:3]]
            stmt = stmt.where(or_(*skill_clauses) if len(skill_clauses) > 1 else skill_clauses[0])
            label = f"roles using {', '.join(skill_names[:3])}"
        else:
            label = "role workflow — skills and tools"

        return self._execute(
            session,
            stmt,
            intent=intent,
            tables_used=["canonical_roles"],
            query_label=label,
            confidence=confidence,
        )

    def _route_geographic(
        self,
        *,
        intent: str,
        confidence: float,
        skill_names: list[str],
        role_names: list[str],
        geo_terms: list[str],
        week_floor: date,
        session: Session,
        tenant: TenantAccess,
        question: str = "",
    ) -> RouteResult:
        """geographic — list-style → ``job_postings JOIN postal_geo_data``;
        aggregate-style → ``geo_demand_weekly`` (JIE #306)."""
        allowed = list(tenant.allowed_subregions)
        if not allowed:
            return RouteResult(
                intent=intent,
                tables_used=[],
                query_label="geographic — no subregions configured for tenant",
                rows=[],
                row_count=0,
                confidence=confidence,
            )

        if _is_list_style(question):
            return self._route_geographic_list(
                intent=intent,
                confidence=confidence,
                skill_names=skill_names,
                role_names=role_names,
                geo_terms=geo_terms,
                week_floor=week_floor,
                session=session,
                tenant=tenant,
            )

        gd = GeoDemandWeekly
        stmt = (
            select(
                gd.week_start,
                gd.borderplex_subregion,
                gd.posting_count,
            )
            .where(
                gd.week_start >= week_floor,
                gd.borderplex_subregion.in_(allowed),
            )
            .order_by(gd.week_start.desc(), gd.posting_count.desc())
        )

        resolved = _resolve_geo_terms(geo_terms, tenant)
        if resolved:
            geo_clauses = [
                gd.borderplex_subregion == value if is_exact else gd.borderplex_subregion.ilike(f"%{value}%")
                for value, is_exact in resolved
            ]
            stmt = stmt.where(or_(*geo_clauses) if len(geo_clauses) > 1 else geo_clauses[0])

        label = "geographic demand"
        if geo_terms:
            label += f" — {', '.join(geo_terms[:3])}"

        return self._execute(
            session,
            stmt,
            intent=intent,
            tables_used=["geo_demand_weekly"],
            query_label=label,
            confidence=confidence,
        )

    def _route_geographic_list(
        self,
        *,
        intent: str,
        confidence: float,
        skill_names: list[str],
        role_names: list[str],
        geo_terms: list[str],
        week_floor: date,
        session: Session,
        tenant: TenantAccess,
    ) -> RouteResult:
        """List-style geographic — per-posting query against ``job_postings``
        joined to ``postal_geo_data`` (JIE #306).

        ``job_postings`` is Prisma-managed (no SQLAlchemy ORM model), so this
        path uses ``text()`` with bind parameters. Filtering: tenant subregion
        via ``borderplex_subregion`` allowlist + city/state derived from
        ``geo_terms`` + role match via ``canonical_role_id`` (pgvector on
        ``dbo.canonical_roles.label_embedding``, same as
        ``_resolve_role_names_to_canonical_ids``) with tokenized ILIKE fallback
        on ``job_title`` / ``role_classification`` when no IDs resolve (JIE
        #309). Skill filtering is intentionally not wired — the JSONB unnest
        path is more invasive and belongs in a follow-up.
        """
        # Parse geo_terms for (city, state_code). The first parseable term wins;
        # downstream callers can re-issue with a refined term if needed.
        parsed_city: str | None = None
        parsed_state: str | None = None
        for term in geo_terms:
            city, state_code = _split_geo_term(term)
            if city or state_code:
                parsed_city = city
                parsed_state = state_code
                break

        # tenant subregion allowlist enforcement (JIE #224 carry-over).
        # job_postings.borderplex_subregion is canonical (el_paso / las_cruces / ...).
        allowed_subregions = list(tenant.allowed_subregions)

        params: dict[str, Any] = {
            "week_floor": week_floor,
            "subregions": allowed_subregions,
        }
        # ``is_spam = FALSE`` is the strict, intentional filter. Rows with
        # ``is_spam IS NULL`` are *unclassified* — they need HITL or batch
        # spam-tier verification before they're safe to surface in a
        # user-facing answer. Rows with ``is_spam = TRUE`` are confirmed
        # spam. Only confirmed-clean rows flow through the natural list-style
        # path. Subregions whose postings are largely unclassified (e.g.
        # ~1,400 Las Cruces rows as of 2026-04-28) will return 0 rows from
        # this path until the spam classifier backfill runs — that data gap
        # is tracked separately and is the correct UX, not a router bug.
        where_parts: list[str] = [
            "jp.is_spam = FALSE",
            "jp.date_posted >= :week_floor",
            "jp.borderplex_subregion = ANY(:subregions)",
            "jp.zip_code IS NOT NULL",
        ]

        if parsed_city:
            where_parts.append("pgd.city ILIKE :city")
            params["city"] = parsed_city
        if parsed_state:
            where_parts.append("pgd.state_code = :state_code")
            params["state_code"] = parsed_state

        if role_names:
            # JIE #309 — prefer pgvector resolution to canonical_roles (same pattern as
            # role_evolution / workflow). Filter job_postings by canonical_role_id when
            # we get hits; fall back to token ILIKE when embeddings or DB rows are
            # unavailable (sparse jp.canonical_role_id coverage per #229).
            resolved_ids = self._resolve_role_names_to_canonical_ids(
                role_names[:5],
                session,
                top_k=5,
            )
            if resolved_ids:
                placeholders = ", ".join(f":crid{i}" for i in range(len(resolved_ids)))
                for i, rid in enumerate(resolved_ids):
                    params[f"crid{i}"] = rid
                # Widen recall onto NULL-canonical_role_id postings whose title
                # matches a representative_title of any resolved canonical role.
                rep_titles = self._resolve_representative_titles(resolved_ids, session)
                role_predicates: list[str] = [
                    f"jp.canonical_role_id IN ({placeholders})",
                ]
                for i, rep_title in enumerate(rep_titles):
                    key = f"rep_title_{i}"
                    params[key] = f"%{rep_title}%"
                    role_predicates.append(f"jp.job_title ILIKE :{key}")
                where_parts.append("(" + " OR ".join(role_predicates) + ")")
                where_parts.append("(jp.role_classification IS NULL OR jp.role_classification <> 'N/A Not an IT role')")
                log.info(
                    "query_router_geographic_list_embedding_route",
                    resolved_count=len(resolved_ids),
                    rep_titles_count=len(rep_titles),
                )
            else:
                # Tokenize each role name and OR-match every token against EITHER
                # job_title OR role_classification. role_classification is
                # coarse-grained ("Artificial Intelligence" / "Software Engineering")
                # while job_title carries niche detail ("AI Scientist", "Prompt
                # Engineer"). Tokenization handles the semantic gap between the
                # user's phrasing ("AI/ML researcher") and the data's phrasing
                # ("AI Scientist", "Applied ML Researcher").
                role_clauses: list[str] = []
                seen: set[str] = set()
                token_index = 0
                for role in role_names[:5]:
                    for tok in _tokenize_role_name(role):
                        norm = tok.lower()
                        if norm in seen:
                            continue
                        seen.add(norm)
                        title_key = f"role_title_{token_index}"
                        cls_key = f"role_cls_{token_index}"
                        role_clauses.append(
                            f"(jp.job_title ILIKE :{title_key} OR jp.role_classification ILIKE :{cls_key})"
                        )
                        params[title_key] = f"%{tok}%"
                        params[cls_key] = f"%{tok}%"
                        token_index += 1
                        if token_index >= 12:  # cap parameter explosion
                            break
                    if token_index >= 12:
                        break
                if role_clauses:
                    where_parts.append(
                        "(" + " OR ".join(role_clauses) + ")" if len(role_clauses) > 1 else role_clauses[0]
                    )
                    # Issue #197 — exclude misbucketed placeholder when filtering on role.
                    where_parts.append(
                        "(jp.role_classification IS NULL OR jp.role_classification <> 'N/A Not an IT role')"
                    )
                    log.info(
                        "query_router_geographic_list_token_fallback",
                        role_names_count=len(role_names),
                    )

        sql = text(
            f"""
            SELECT jp.job_posting_id,
                   jp.job_title,
                   jp.role_classification,
                   jp.borderplex_subregion,
                   jp.date_posted,
                   jp.is_remote,
                   jp.salary_min,
                   jp.salary_max,
                   jp.salary_currency,
                   pgd.city,
                   pgd.state_code,
                   pgd.county,
                   c.company_name
            FROM dbo.job_postings AS jp
            JOIN dbo.postal_geo_data AS pgd ON pgd.zip = jp.zip_code
            LEFT JOIN dbo.companies AS c ON c.company_id = jp.company_id
            WHERE {" AND ".join(where_parts)}
            ORDER BY jp.date_posted DESC NULLS LAST
            LIMIT :row_limit
            """
        )
        params["row_limit"] = _QUERY_LIMIT

        label_parts = ["per-posting list"]
        if geo_terms:
            label_parts.append(", ".join(geo_terms[:2]))
        if role_names:
            label_parts.append(f"role={', '.join(role_names[:2])}")
        label = "geographic demand — " + " · ".join(label_parts)

        try:
            result = session.execute(
                sql,
                params,
                execution_options={"timeout": _QUERY_TIMEOUT_SECONDS},
            )
            rows = [dict(row._mapping) for row in result]
            is_partial = len(rows) >= _QUERY_LIMIT
            log.info(
                "query_router_result",
                intent=intent,
                query_label=label,
                row_count=len(rows),
                is_partial=is_partial,
                tables_used=["job_postings", "postal_geo_data", "companies"],
                routing_path="list_style",
            )
            return RouteResult(
                intent=intent,
                tables_used=["job_postings", "postal_geo_data", "companies"],
                query_label=label,
                rows=rows,
                row_count=len(rows),
                is_partial=is_partial,
                confidence=confidence,
            )
        except Exception as exc:
            log.error(
                "query_router_execute_error",
                intent=intent,
                query_label=label,
                error_type=type(exc).__name__,
                error=str(exc),
                routing_path="list_style",
            )
            return RouteResult(
                intent=intent,
                tables_used=["job_postings", "postal_geo_data"],
                query_label=label,
                routed=False,
                error=str(exc),
                confidence=confidence,
            )

    def _route_comparison(
        self,
        *,
        intent: str,
        confidence: float,
        skill_names: list[str],
        role_names: list[str],
        geo_terms: list[str],
        week_floor: date,
        session: Session,
        tenant: TenantAccess,
        question: str = "",
    ) -> RouteResult:
        """comparison → ``skill_demand_weekly`` for skill vs skill; ``sector_summary_weekly`` otherwise."""
        if not tenant.can_query_borderplex_skill_tables:
            return self._no_borderplex_market_aggregates(intent, confidence)
        if skill_names:
            t = SkillDemandWeekly
            skill_filter = self._ilike_or(t.skill_label, skill_names)
            stmt = (
                select(
                    t.skill_label,
                    t.week_start,
                    t.posting_count,
                    t.employer_count,
                    t.esco_uri,
                )
                .where(t.week_start >= week_floor)
                .order_by(t.skill_label, t.week_start.desc())
            )
            if skill_filter is not None:
                stmt = stmt.where(skill_filter)

            label = f"skill comparison — {' vs '.join(skill_names[:5])}"
            tables_used = ["skill_demand_weekly"]
        else:
            ss = SectorSummaryWeekly
            stmt = (
                select(
                    ss.sector,
                    ss.week_start,
                    ss.posting_count,
                    ss.employer_count,
                    ss.avg_salary,
                    ss.top_skills,
                )
                .where(ss.week_start >= week_floor)
                .order_by(ss.posting_count.desc(), ss.week_start.desc())
            )

            sector_filter = self._ilike_or(ss.sector, role_names)
            if sector_filter is not None:
                stmt = stmt.where(sector_filter)

            label = "sector comparison"
            if role_names:
                label += f" — {', '.join(role_names[:3])}"
            tables_used = ["sector_summary_weekly"]

        return self._execute(
            session,
            stmt,
            intent=intent,
            tables_used=tables_used,
            query_label=label,
            confidence=confidence,
        )

    def _route_other(
        self,
        *,
        intent: str,
        confidence: float,
        skill_names: list[str],
        role_names: list[str],
        geo_terms: list[str],
        week_floor: date,
        session: Session,
        tenant: TenantAccess,
        question: str = "",
    ) -> RouteResult:
        """other / unrecognised intent — return an unrouted result without executing SQL."""
        log.info(
            "query_router_unrouted",
            intent=intent,
            confidence=confidence,
        )
        return RouteResult(
            intent=intent,
            tables_used=[],
            query_label="unrouted — intent could not be mapped to a query",
            routed=False,
            confidence=confidence,
        )


# ---------------------------------------------------------------------------
# Dispatch table (populated after class definition to avoid forward-reference
# issues while still keeping handlers as bound methods)
# ---------------------------------------------------------------------------

_INTENT_HANDLERS: dict[str, Callable[..., RouteResult]] = {
    "trend": QueryRouter._route_trend,
    "role_evolution": QueryRouter._route_role_evolution,
    "disruption": QueryRouter._route_disruption,
    "emergence": QueryRouter._route_emergence,
    "curriculum": QueryRouter._route_curriculum,
    "employer": QueryRouter._route_employer,
    "workflow": QueryRouter._route_workflow,
    "geographic": QueryRouter._route_geographic,
    "comparison": QueryRouter._route_comparison,
    "other": QueryRouter._route_other,
}
