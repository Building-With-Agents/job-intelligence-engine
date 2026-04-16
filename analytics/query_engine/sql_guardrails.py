"""SQL guardrails for analytics — Pair C canonical module (sqlglot AST, SELECT-only, allowlist, LIMIT ≤ 100)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import sqlglot
import structlog
from sqlglot import exp

log = structlog.get_logger()

# Approved aggregate / analytics tables only (no raw PII sources).
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
    """Maps to :func:`validate_sql` for callers (routing, triggers, execute_safe)."""

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
    """Maps to :class:`ValidationResult` for routing, triggers, and execute_safe."""
    res = validate_analytics_sql(sql)
    if res.ok and res.sql:
        return ValidationResult(True, None, res.sql)
    return ValidationResult(False, res.error or "invalid_sql", "")


def validate_or_raise(sql_text: str) -> str:
    res = validate_analytics_sql(sql_text)
    if not res.ok or not res.sql:
        raise ValueError(res.error or "invalid_sql")
    return res.sql
