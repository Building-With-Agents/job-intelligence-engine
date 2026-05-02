"""Curriculum synthesis and role-discovery helpers for the Q&A router.

Calls :func:`common.llm_adapter.complete` with ``role="synthesis"`` (``LLM_SYNTHESIS`` /
``chat-gpt41`` by default). Skips the LLM when there is no resolved role or no
top-skill demand signal, per product guardrails.

JIE #298 — no_resolved_role message hardcodes role suggestions that don't exist.

When a curriculum / workflow / role_evolution query specifies a role name that
does not match any cluster label in ``dbo.canonical_roles``, the router used to
return a generic refusal.  This module provides:

- ``fetch_role_suggestions`` — queries the live ``canonical_roles`` table
  (ordered by posting volume) so the suggestion list reflects what the pipeline
  actually clustered, not a hard-coded "software developer / data analyst" string.
- ``no_resolved_role_message`` — formats the user-facing refusal message using
  the live suggestions.

Callers (router handlers) should call ``no_resolved_role_message`` when
``row_count == 0`` after applying a role-name filter so users receive actionable
guidance tied to real data instead of fictional role names.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Final

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from analytics.query_engine.curriculum_path import CurriculumInputs
from common.llm_adapter import complete

log = structlog.get_logger()

_AGENT: Final[str] = "analytics-curriculum-synthesis"

_SYSTEM_PROMPT = """You are generating a training-program outline for a workforce development director
in the Borderplex region (El Paso TX + Las Cruces NM). You must only use the data
provided. Do not reach for outside knowledge.

FORBIDDEN:
- Do NOT invent skill names. Every skill in your outline must appear verbatim in the
  input data below.
- Do NOT produce generic module titles (e.g. "Cloud fundamentals") without a matching
  demand signal in the input data.
- Do NOT reference national, statewide, Austin, or Albuquerque data.
- Do NOT reference any external curriculum catalog, bootcamp, or university program by name.
- If a section has no supporting data, say so explicitly — never invent content to fill it."""

_USER_TEMPLATE = """Role: {canonical_role_label}
Period: {period}
Region: {region}

Top demanded skills: {top_skills_json}
Rising skills (4wk trend > 10%): {rising_skills_json}
Co-occurring tools + responsibilities: {co_occurring_json}
Top employers hiring for this role: {top_employers_json}

Produce a markdown outline with these sections in this order:

## Program scope
Role, estimated duration, regional scope, temporal period.

## Modules
6-8 modules. Each module must include:
- Module title (must match a skill or skill cluster from the data above verbatim)
- Demand signal (cite % of postings, employer count, OR velocity trend — include
  the numeric value and the source table)
- One-paragraph description

## Sequencing rationale
Why modules are ordered this way.

## Evidence
Tables queried, temporal period, regional filter, sample size.

## Recommended follow-up
One concrete next step the director can take."""

_INSUFFICIENT_NO_ROLE: Final[dict[str, Any]] = {
    "answer": (
        "Insufficient data: could not resolve the requested role in the Borderplex dataset. "
        "Try a different role name such as 'software developer', 'data analyst', or "
        "'cybersecurity analyst'."
    ),
    "is_sufficient": False,
    "modules": [],
}

_INSUFFICIENT_NO_TOP_SKILLS: Final[dict[str, Any]] = {
    "answer": (
        "Insufficient data: we resolved a target role, but there are no top demanded skills "
        "in the Borderplex aggregates for the current time window, so a skills-grounded "
        "curriculum outline cannot be generated. Check back after the next analytics refresh, "
        "or try a role with more postings in this region."
    ),
    "is_sufficient": False,
    "modules": [],
}

_INSUFFICIENT_LLM_ERROR: Final[dict[str, Any]] = {
    "answer": (
        "Insufficient data: the curriculum outline could not be generated at this time. "
        "Please retry, or use Ask the Data after confirming analytics aggregates are fresh."
    ),
    "is_sufficient": False,
    "modules": [],
}

_DEFAULT_ROLE_SUGGESTION_LIMIT = 5
_FALLBACK_MESSAGE = (
    "No postings were found matching that role. "
    "The canonical role taxonomy may not yet contain clusters for this title. "
    "Try broadening your search or querying without a specific role filter."
)


def _json_block(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False, indent=2)


def _strip_code_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:markdown|md)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _extract_module_titles(markdown: str) -> list[str]:
    """Parse module titles from the ``## Modules`` section (### headings, bold bullets)."""
    md = markdown or ""
    m = re.search(
        r"(?msi)^##\s+modules\s*\r?\n(.*?)(?=^##\s+)",
        md,
    )
    if not m:
        m = re.search(r"(?msi)^##\s+modules\s*\r?\n(.*)\Z", md)
    section = m.group(1) if m else ""
    if not section.strip():
        return []

    titles: list[str] = []
    for line in section.splitlines():
        line = line.strip()
        h3 = re.match(r"^###\s+(.+)$", line)
        if h3:
            titles.append(h3.group(1).strip())
            continue
        bold = re.match(r"^[-*]\s*\*\*([^*]+)\*\*\s*[:：]?", line)
        if bold:
            titles.append(bold.group(1).strip())
            continue
        mtitle = re.match(r"^[-*]\s*Module title\s*:\s*(.+)$", line, re.I)
        if mtitle:
            titles.append(mtitle.group(1).strip().strip("`*"))
    seen: set[str] = set()
    out: list[str] = []
    for t in titles:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:12]


def synthesize_curriculum_outline(
    inputs: CurriculumInputs,
    *,
    correlation_id: str | None = None,
    max_tokens: int = 4_000,
) -> dict[str, Any]:
    """Build a markdown curriculum outline from data-grounded :class:`CurriculumInputs`.

    Respects ``data_flags``: skips the LLM when the role did not match or when there are
    no top demanded skills. On LLM failure, returns a non-raising insufficient-data
    structure (``is_sufficient`` false).

    Returns
    -------
    dict
        ``{"answer": str, "is_sufficient": bool, "modules": list[str]}``
    """
    if inputs.data_flags.get("canonical_role", False) or not (inputs.canonical_role or "").strip():
        log.info("curriculum_synthesis_skipped", reason="no_resolved_role")
        return dict(_INSUFFICIENT_NO_ROLE)

    if not inputs.top_skills:
        log.info("curriculum_synthesis_skipped", reason="empty_top_skills")
        return dict(_INSUFFICIENT_NO_TOP_SKILLS)

    user_prompt = _USER_TEMPLATE.format(
        canonical_role_label=(
            inputs.canonical_role_label
            or inputs.canonical_role
            or "Unknown role"
        ),
        period=inputs.period,
        region=inputs.region,
        top_skills_json=_json_block(inputs.top_skills),
        rising_skills_json=_json_block(inputs.rising_skills),
        co_occurring_json=_json_block(inputs.co_occurring),
        top_employers_json=_json_block(inputs.top_employers),
    )

    try:
        result = complete(
            prompt=user_prompt,
            agent_name=_AGENT,
            role="synthesis",
            system=_SYSTEM_PROMPT,
            max_tokens=max_tokens,
            correlation_id=correlation_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("curriculum_synthesis_llm_exception", error_type=type(exc).__name__)
        return dict(_INSUFFICIENT_LLM_ERROR)

    if not result.get("success") or result.get("extraction_failed"):
        log.info(
            "curriculum_synthesis_llm_failed",
            success=result.get("success"),
            extraction_failed=result.get("extraction_failed"),
        )
        return dict(_INSUFFICIENT_LLM_ERROR)

    content = _strip_code_fence((result.get("content") or "").strip())
    if not content:
        return dict(_INSUFFICIENT_LLM_ERROR)

    modules = _extract_module_titles(content)
    return {
        "answer": content,
        "is_sufficient": True,
        "modules": modules,
    }


def fetch_role_suggestions(
    session: Session,
    *,
    limit: int | None = None,
) -> list[str]:
    """Return the top *limit* canonical role labels ordered by posting volume.

    Reads from ``dbo.canonical_roles`` (populated by the analytics clustering
    pipeline).  Falls back to an empty list on any DB error so callers can
    always produce a graceful message even when the table is empty or absent.

    Args:
        session: Open SQLAlchemy session (read-only path; no writes).
        limit: Max number of labels to return.  Defaults to the
               ``CANONICAL_ROLE_SUGGESTION_LIMIT`` env var, then 5.
    """
    raw_env = os.getenv("CANONICAL_ROLE_SUGGESTION_LIMIT", str(_DEFAULT_ROLE_SUGGESTION_LIMIT))
    try:
        effective_limit = limit or int(raw_env)
    except (ValueError, TypeError):
        log.warning("canonical_role_suggestion_limit_invalid", raw_value=raw_env)
        effective_limit = limit or _DEFAULT_ROLE_SUGGESTION_LIMIT
    try:
        result = session.execute(
            text("SELECT label FROM dbo.canonical_roles ORDER BY posting_count DESC NULLS LAST LIMIT :lim"),
            {"lim": effective_limit},
        )
        rows = result.fetchall()
        labels = [str(row[0]).strip() for row in rows if row[0] and str(row[0]).strip()]
        return labels
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "fetch_role_suggestions_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return []


def no_resolved_role_message(
    session: Session,
    *,
    role_query: str = "",
    intent: str = "",
) -> str:
    """Build a user-facing refusal string for the no_resolved_role case.

    Fetches live canonical role labels from the DB instead of using a
    hard-coded list.  When the table is empty or unreachable returns a
    generic fallback message.

    Args:
        session: Open SQLAlchemy session (read path only).
        role_query: The role name(s) the user asked about (for context).
        intent: Intent label (e.g. "workflow", "role_evolution") for logging.
    """
    suggestions = fetch_role_suggestions(session)

    log.info(
        "no_resolved_role_message_built",
        intent=intent,
        role_query=role_query,
        suggestion_count=len(suggestions),
    )

    if not suggestions:
        return _FALLBACK_MESSAGE

    role_part = f" for '{role_query}'" if role_query else ""
    suggestion_str = ", ".join(f"'{s}'" for s in suggestions)
    return (
        f"No postings were found{role_part} in the canonical role taxonomy. "
        f"Available roles include: {suggestion_str}. "
        "Try one of these or query without a role filter to see all clusters."
    )


__all__ = [
    "fetch_role_suggestions",
    "no_resolved_role_message",
    "synthesize_curriculum_outline",
]
