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

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import structlog
from sqlalchemy import Text, cast, or_, select
from sqlalchemy.orm import Session

from analytics.tenant_scope import TenantAccess, get_tenant_access_for_pipeline
from common.data_store.models import (
    CanonicalRole,
    Company,
    EmployerProfile,
    GeoDemandWeekly,
    SectorSummaryWeekly,
    SkillDemandWeekly,
    SkillVelocity,
)

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
        confidence:   Forwarded from the intent classifier.
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
    ) -> RouteResult:
        """Dispatch a classification dict to the appropriate query handler.

        Args:
            classification: Output of ``classify_workforce_question`` — keys
                ``intent`` (str), ``confidence`` (float),
                ``extracted_entities`` (dict).
            session: Open SQLAlchemy ``Session`` (read path; the router never
                issues write statements).
            tenant: Entitled subregions + aggregate exposure (JIE #224 / ``X-Tenant-Id``).

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
    ) -> RouteResult:
        """role_evolution → ``canonical_roles`` ordered by posting volume."""
        if not tenant.can_query_borderplex_skill_tables:
            return self._no_borderplex_market_aggregates(intent, confidence)
        cr = CanonicalRole
        stmt = select(
            cr.role_id,
            cr.label,
            cr.description,
            cr.posting_count,
            cr.representative_titles,
            cr.top_skills,
            cr.top_tools,
            cr.computed_at,
        ).order_by(cr.posting_count.desc(), cr.computed_at.desc())

        role_filter = self._ilike_or(cr.label, role_names)
        if role_filter is not None:
            stmt = stmt.where(role_filter)

        label = "canonical role evolution"
        if role_names:
            label += f" — {', '.join(role_names[:3])}"

        return self._execute(
            session,
            stmt,
            intent=intent,
            tables_used=["canonical_roles"],
            query_label=label,
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

        # role_names / geo_terms may carry company name hints (e.g. "Microsoft jobs")
        hints = role_names + geo_terms
        name_filter = self._ilike_or(co.company_name, hints)
        if name_filter is not None:
            stmt = stmt.where(name_filter)

        label = "employer profiles"
        if hints:
            label += f" — {', '.join(hints[:3])}"

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
    ) -> RouteResult:
        """workflow → ``canonical_roles`` (top_skills, top_tools) for day-to-day tasks."""
        if not tenant.can_query_borderplex_skill_tables:
            return self._no_borderplex_market_aggregates(intent, confidence)
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

        if role_names:
            role_filter = self._ilike_or(cr.label, role_names)
            if role_filter is not None:
                stmt = stmt.where(role_filter)
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
    ) -> RouteResult:
        """geographic → ``geo_demand_weekly`` filtered by borderplex_subregion."""
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
