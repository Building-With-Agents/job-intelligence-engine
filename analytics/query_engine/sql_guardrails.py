"""SQL guardrails for analytics (Pair C sqlglot) and Ask the Data (regex, dbo job schema).

- :func:`validate_sql` returns :class:`ValidationResult` for triggers / ``execute_safe`` /
  aggregate-table SQL (approved weekly / cache tables only).
- :func:`validate_ask_the_data_sql` returns ``(ok, reason, normalized_sql)`` for NL-generated
  SELECTs over ``dbo.job_postings`` and related operational tables (GitHub #117).

See ``.cursor/rules/sql-guardrails.mdc``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final

import sqlglot
import structlog
from sqlglot import exp

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Ask the Data — regex validator (operational / dbo schema)
# ---------------------------------------------------------------------------

ASK_THE_DATA_ALLOWED_TABLES: Final[frozenset[str]] = frozenset(
    {
        # Aggregate tables — preferred for skill/tool/role/sector/geo Q&A
        "skill_demand_weekly",
        "tool_demand_weekly",
        "role_snapshot_weekly",
        "sector_summary_weekly",
        "geo_demand_weekly",
        "skill_velocity",
        "skill_co_occurrence",
        "posting_freshness",
        # Reference / dimension tables
        "canonical_roles",
        "employer_profiles",
        "companies",
        "industry_sectors",
        # Operational tables — join via (source, external_id)
        "job_postings",
        "normalized_jobs",
        "extracted_intelligence",
        # Legacy / reference (kept for backward-compat; _SCHEMA_HINT does not direct LLM here)
        "company_addresses",
        "technology_areas",
        "analytics_aggregates",
        "skills",
        "raw_ingested_jobs",
    }
)

_MAX_SQL_CHARS: Final[int] = 20_000
_ROW_LIMIT: Final[int] = 100

_FORBIDDEN_DML: Final[re.Pattern[str]] = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|MERGE|EXEC|EXECUTE|CALL|GRANT|REVOKE|COPY|"
    r"INTO\s+OUTFILE|LOAD_FILE|PG_READ_FILE|PG_SLEEP|DBLINK|LISTEN|NOTIFY|SET\s+ROLE|PREPARE)\b",
    re.IGNORECASE | re.DOTALL,
)

_MULTI_STMT: Final[re.Pattern[str]] = re.compile(r";\s*\S")

_FROM_JOIN_TABLE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:ONLY\s+)?(?!\()(?:(?P<sch>[\w]+)\.)?(?P<tbl>[\w]+)\b",
    re.IGNORECASE,
)


def _cte_aliases(sql: str) -> set[str]:
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
    def repl_block(m: re.Match[str]) -> str:
        return " "

    s = re.sub(r"/\*[\s\S]*?\*/", repl_block, sql)
    s = re.sub(r"--[^\n]*", " ", s)
    return s


def _normalize_limit(sql: str) -> str:
    s = sql.rstrip().rstrip(";").strip()
    lim_re = re.compile(r"\blimit\s+(\d+)\s*$", re.IGNORECASE)
    m = lim_re.search(s)
    if m:
        n = int(m.group(1))
        capped = min(max(n, 1), _ROW_LIMIT)
        s = s[: m.start()] + f"LIMIT {capped}"
        return s
    return f"{s} LIMIT {_ROW_LIMIT}"


def validate_ask_the_data_sql(sql: str) -> tuple[bool, str, str | None]:
    """Validate NL-generated SQL before execution on Ask the Data path.

    Returns ``(ok, reason, normalized_sql)``. On failure ``normalized_sql`` is ``None``.
    """
    if not sql or not str(sql).strip():
        return False, "empty_sql", None

    raw = str(sql).strip()
    if len(raw) > _MAX_SQL_CHARS:
        return False, "sql_too_long", None

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

    for m in _FROM_JOIN_TABLE.finditer(analyzed):
        tbl = (m.group("tbl") or "").lower()
        sch = (m.group("sch") or "").lower()
        if sch and sch not in ("dbo", "public"):
            return False, f"disallowed_schema:{sch}", None
        if tbl and tbl not in ASK_THE_DATA_ALLOWED_TABLES and tbl not in cte_ok:
            return False, f"disallowed_table:{tbl}", None

    normalized = _normalize_limit(raw.rstrip().rstrip(";").strip())
    return True, "ok", normalized


def extract_tables_referenced(sql: str) -> list[str]:
    """Return sorted unique table names in FROM/JOIN (Ask the Data allowlist names only)."""
    analyzed = _strip_sql_comments(sql)
    found: set[str] = set()
    for m in _FROM_JOIN_TABLE.finditer(analyzed):
        tbl = (m.group("tbl") or "").lower()
        if tbl in ASK_THE_DATA_ALLOWED_TABLES:
            found.add(tbl)
    return sorted(found)


# ---------------------------------------------------------------------------
# Aggregate analytics — sqlglot (Pair C)
# ---------------------------------------------------------------------------

ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "skill_demand_weekly",
        "tool_demand_weekly",
        "role_snapshot_weekly",
        "sector_summary_weekly",
        "geo_demand_weekly",
        "skill_velocity",
        "skill_co_occurrence",
        "posting_freshness",
        "trajectory_map",
        "analytics_pipeline_state",
        "cohort_gap_cache",
        "canonical_roles",
    }
)

MAX_ROWS = 100
DIALECT = "postgres"


@dataclass(frozen=True)
class SqlValidationResult:
    ok: bool
    error: str | None
    sql: str | None


@dataclass(frozen=True)
class ValidationResult:
    """Return type for :func:`validate_sql` (routing, triggers, ``execute_safe``)."""

    ok: bool
    reason: str | None
    sql_for_execution: str


def _sha256(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def log_validation_failure(sql_text: str, reason: str) -> None:
    log.warning("sql_validation_failed", reason=reason, query_sha256=_sha256(sql_text))


def _norm_table_name(t: exp.Table) -> str:
    return (t.name or "").strip('"').lower()


def _collect_physical_tables(expression: exp.Expression) -> set[str]:
    names: set[str] = set()
    for t in expression.find_all(exp.Table):
        if not isinstance(t, exp.Table):
            continue
        if t.args.get("system"):
            continue
        n = _norm_table_name(t)
        if n:
            names.add(n)
    return names


def _forbidden_dml_ddl(expression: exp.Expression) -> str | None:
    checks: list[tuple[type[exp.Expression], str]] = [
        (exp.Insert, "INSERT"),
        (exp.Update, "UPDATE"),
        (exp.Delete, "DELETE"),
        (exp.Drop, "DROP"),
        (exp.Create, "CREATE"),
        (exp.Alter, "ALTER"),
        (exp.Merge, "MERGE"),
        (exp.TruncateTable, "TRUNCATE"),
    ]
    for cls, label in checks:
        if list(expression.find_all(cls)):
            return f"{label} is not allowed"
    for cmd in expression.find_all(exp.Command):
        head = str(cmd.this).strip().split()[0].upper() if cmd.this else ""
        if head in {"COPY", "DO", "CALL"}:
            return f"{head} is not allowed"
    return None


def _enforce_limit_on_root(parsed: exp.Expression) -> None:
    if not isinstance(parsed, (exp.Select, exp.Union)):
        raise ValueError("unsupported root expression")

    lim = parsed.args.get("limit")
    if lim is None:
        parsed.limit(MAX_ROWS, copy=False)
        return

    inner = lim.this if isinstance(lim, exp.Limit) else None
    if isinstance(inner, exp.Literal) and inner.is_int:
        try:
            v = int(inner.this)
        except (TypeError, ValueError):
            parsed.limit(MAX_ROWS, copy=False)
            return
        if v > MAX_ROWS:
            parsed.limit(MAX_ROWS, copy=False)
        return

    parsed.limit(MAX_ROWS, copy=False)


def validate_analytics_sql(sql_text: str) -> SqlValidationResult:
    """Parse with sqlglot (Postgres). Return validated SQL or error."""
    raw = (sql_text or "").strip()
    if not raw:
        log_validation_failure(raw, "empty_sql")
        return SqlValidationResult(ok=False, error="SQL is empty", sql=None)

    try:
        statements = sqlglot.parse(raw, dialect=DIALECT)
    except sqlglot.errors.ParseError:
        log_validation_failure(raw, "parse_error")
        return SqlValidationResult(ok=False, error="SQL parse error", sql=None)

    if len(statements) != 1:
        log_validation_failure(raw, "multiple_statements")
        return SqlValidationResult(ok=False, error="Only a single SELECT statement is allowed", sql=None)

    parsed = statements[0]

    if not isinstance(parsed, (exp.Select, exp.Union)):
        log_validation_failure(raw, f"root_not_select:{type(parsed).__name__}")
        return SqlValidationResult(ok=False, error="Only SELECT queries are allowed", sql=None)

    bad = _forbidden_dml_ddl(parsed)
    if bad:
        log_validation_failure(raw, bad)
        return SqlValidationResult(ok=False, error=bad, sql=None)

    tables = _collect_physical_tables(parsed)
    for t in tables:
        if t not in ALLOWED_TABLES:
            log_validation_failure(raw, f"table_not_allowed:{t}")
            return SqlValidationResult(
                ok=False,
                error=f"Table {t!r} is not in the approved analytics allowlist",
                sql=None,
            )

    try:
        _enforce_limit_on_root(parsed)
    except Exception:
        log_validation_failure(raw, "limit_rewrite_failed")
        return SqlValidationResult(ok=False, error="Could not enforce LIMIT", sql=None)

    try:
        safe_sql = parsed.sql(dialect=DIALECT, pretty=False)
    except Exception:
        log_validation_failure(raw, "serialize_failed")
        return SqlValidationResult(ok=False, error="Could not serialize validated SQL", sql=None)

    return SqlValidationResult(ok=True, error=None, sql=safe_sql)


def validate_sql(sql: str) -> ValidationResult:
    """Validate aggregate-analytics SQL; maps to :class:`ValidationResult`."""
    res = validate_analytics_sql(sql)
    if res.ok and res.sql:
        return ValidationResult(True, None, res.sql)
    return ValidationResult(False, res.error or "invalid_sql", "")


def validate_or_raise(sql_text: str) -> str:
    res = validate_analytics_sql(sql_text)
    if not res.ok or not res.sql:
        raise ValueError(res.error or "invalid_sql")
    return res.sql
