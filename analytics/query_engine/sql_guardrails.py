"""SELECT-only SQL validation for Ask the Data (GitHub #117).

Enforces `.cursor/rules/sql-guardrails.mdc`: allowlisted tables, row cap, no DML/DDL,
and conservative defenses against stacked statements and risky keywords.
"""

from __future__ import annotations

import re
from typing import Final

# Must match `.cursor/rules/sql-guardrails.mdc` allowlist.
ALLOWED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "job_postings",
        "companies",
        "company_addresses",
        "skills",
        "technology_areas",
        "industry_sectors",
        "analytics_aggregates",
        "normalized_jobs",
        "raw_ingested_jobs",
    }
)

_MAX_SQL_CHARS: Final[int] = 20_000
_ROW_LIMIT: Final[int] = 100

# DML/DDL and dangerous primitives (case-insensitive word boundaries).
_FORBIDDEN_DML: Final[re.Pattern[str]] = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|MERGE|EXEC|EXECUTE|CALL|GRANT|REVOKE|COPY|"
    r"INTO\s+OUTFILE|LOAD_FILE|PG_READ_FILE|PG_SLEEP|DBLINK|LISTEN|NOTIFY|SET\s+ROLE|PREPARE)\b",
    re.IGNORECASE | re.DOTALL,
)

# Second statement after semicolon (stacked SQL).
_MULTI_STMT: Final[re.Pattern[str]] = re.compile(r";\s*\S")

_FROM_JOIN_TABLE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:ONLY\s+)?(?!\()(?:(?P<sch>[\w]+)\.)?(?P<tbl>[\w]+)\b",
    re.IGNORECASE,
)


def _cte_aliases(sql: str) -> set[str]:
    """Shallow CTE name set for ``WITH a AS (...), b AS (...) SELECT`` (no nested parens in headers)."""
    s = sql.strip()
    names: set[str] = set()
    if not re.match(r"WITH\s+", s, re.IGNORECASE):
        return names
    m0 = re.match(r"WITH\s+(\w+)\s+AS\s*\(", s, re.IGNORECASE)
    if m0:
        names.add(m0.group(1).lower())
    for m in re.finditer(r"\)\s*,\s*(\w+)\s+AS\s*\(", s, re.IGNORECASE):
        names.add(m.group(1).lower())
    return names


def _strip_sql_comments(sql: str) -> str:
    """Remove `--` and `/* */` comments for analysis only (normalized_sql keeps originals stripped minimally)."""

    def repl_block(m: re.Match[str]) -> str:
        return " "

    s = re.sub(r"/\*[\s\S]*?\*/", repl_block, sql)
    s = re.sub(r"--[^\n]*", " ", s)
    return s


def _normalize_limit(sql: str) -> str:
    """Ensure a single trailing LIMIT <= _ROW_LIMIT (append or clamp)."""
    s = sql.rstrip().rstrip(";").strip()
    lim_re = re.compile(r"\blimit\s+(\d+)\s*$", re.IGNORECASE)
    m = lim_re.search(s)
    if m:
        n = int(m.group(1))
        capped = min(max(n, 1), _ROW_LIMIT)
        s = s[: m.start()] + f"LIMIT {capped}"
        return s
    return f"{s} LIMIT {_ROW_LIMIT}"


def validate_sql(sql: str) -> tuple[bool, str, str | None]:
    """Validate generated SQL before execution.

    Returns ``(ok, reason, normalized_sql)``. On failure ``normalized_sql`` is ``None``.
    """
    if not sql or not str(sql).strip():
        return False, "empty_sql", None

    raw = str(sql).strip()
    if len(raw) > _MAX_SQL_CHARS:
        return False, "sql_too_long", None

    # Stacked statements (conservative: any interior semicolon before more tokens).
    body_for_semicolon = raw.rstrip().rstrip(";").strip()
    if _MULTI_STMT.search(body_for_semicolon):
        return False, "multiple_statements", None

    analyzed = _strip_sql_comments(raw)
    if not analyzed.strip():
        return False, "empty_after_comments", None

    if _FORBIDDEN_DML.search(analyzed):
        return False, "forbidden_keyword", None

    upper = analyzed.upper().strip()
    if not upper.startswith("SELECT") and not upper.startswith("WITH"):
        return False, "not_select", None

    cte_ok = _cte_aliases(analyzed)

    # Table allowlist: every FROM/JOIN target must be allowlisted or a declared CTE name.
    for m in _FROM_JOIN_TABLE.finditer(analyzed):
        tbl = (m.group("tbl") or "").lower()
        sch = (m.group("sch") or "").lower()
        if sch and sch not in ("dbo", "public"):
            return False, f"disallowed_schema:{sch}", None
        if tbl and tbl not in ALLOWED_TABLES and tbl not in cte_ok:
            return False, f"disallowed_table:{tbl}", None

    normalized = _normalize_limit(raw.rstrip().rstrip(";").strip())
    return True, "ok", normalized


def extract_tables_referenced(sql: str) -> list[str]:
    """Return sorted unique table names referenced in FROM/JOIN (allowlist names only)."""
    analyzed = _strip_sql_comments(sql)
    found: set[str] = set()
    for m in _FROM_JOIN_TABLE.finditer(analyzed):
        tbl = (m.group("tbl") or "").lower()
        if tbl in ALLOWED_TABLES:
            found.add(tbl)
    return sorted(found)
