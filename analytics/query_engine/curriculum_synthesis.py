"""Curriculum and role-discovery helpers for the Q&A router.

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

import os

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

log = structlog.get_logger()

_DEFAULT_ROLE_SUGGESTION_LIMIT = 5
_FALLBACK_MESSAGE = (
    "No postings were found matching that role. "
    "The canonical role taxonomy may not yet contain clusters for this title. "
    "Try broadening your search or querying without a specific role filter."
)


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
    try:
        raw_env = os.getenv("CANONICAL_ROLE_SUGGESTION_LIMIT", str(_DEFAULT_ROLE_SUGGESTION_LIMIT))
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

    role_part = f" for \u2018{role_query}\u2019" if role_query else ""
    suggestion_str = ", ".join(f"\u2018{s}\u2019" for s in suggestions)
    return (
        f"No postings were found{role_part} in the canonical role taxonomy. "
        f"Available roles include: {suggestion_str}. "
        "Try one of these or query without a role filter to see all clusters."
    )
