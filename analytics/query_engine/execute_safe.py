"""Execute validated SQL with PostgreSQL statement_timeout (30s default)."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

log = structlog.get_logger()

DEFAULT_TIMEOUT_MS = 30_000


def execute_validated_query(
    session: Session,
    sql: str,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> tuple[list[dict[str, Any]], int]:
    """Run SELECT with SET LOCAL statement_timeout. Returns (rows_as_dicts, row_count).

    Caller must have already run ``validate_sql`` successfully. On timeout or DB error,
    raises ``RuntimeError`` with a safe client-facing message (details logged).
    """
    timeout_val = int(timeout_ms)
    if timeout_val < 1 or timeout_val > 600_000:
        raise ValueError("timeout_ms out of range")
    try:
        # SET LOCAL applies until end of current transaction (same session_scope).
        # Integer milliseconds (validated) — avoid driver quirks with bind params on SET.
        session.execute(text(f"SET LOCAL statement_timeout = {timeout_val}"))
        result = session.execute(text(sql))
        mappings = result.mappings().all()
        rows = [dict(m) for m in mappings]
        return rows, len(rows)
    except (OperationalError, DBAPIError) as exc:
        err = str(exc.orig) if hasattr(exc, "orig") and exc.orig else str(exc)
        log.error("sql_execute_failed", error=err, timeout_ms=timeout_ms)
        if "timeout" in err.lower() or "canceling statement" in err.lower():
            raise RuntimeError("query_timeout") from exc
        raise RuntimeError("query_execution_failed") from exc
