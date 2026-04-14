"""AST-based SQL guardrails for LLM- or template-generated PostgreSQL."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import sqlglot
import structlog
from sqlglot import exp
from sqlglot.errors import ParseError

log = structlog.get_logger()

# Physical tables allowed in generated SELECTs (dbo schema). Align with models + sql-guardrails.
ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "job_postings",
        "companies",
        "industry_sectors",
        "technology_areas",
        "skills",
        "socc",
        "naics",
        "postal_geo_data",
        "raw_ingested_jobs",
        "normalized_jobs",
        "extracted_intelligence",
        "employer_profiles",
        "canonical_roles",
        "role_snapshot_weekly",
        "posting_freshness",
        "trajectory_map",
        "skill_demand_weekly",
        "tool_demand_weekly",
        "skill_velocity",
        "skill_co_occurrence",
        "sector_summary_weekly",
        "geo_demand_weekly",
        "analytics_pipeline_state",
        "job_ingestion_runs",
        "normalization_quarantine",
    }
)

_DEFAULT_SCHEMA = "dbo"
_MAX_ROWS = 100
_FORBIDDEN_ROOT_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,  # COPY, VACUUM, etc.
    exp.Merge,
    exp.TruncateTable,
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str | None
    sql_for_execution: str


def _hash_sql(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()[:16]


def _log_rejection(sql: str, reason: str) -> None:
    preview = sql.strip().replace("\n", " ")[:120]
    log.warning(
        "sql_validation_rejected",
        sql_hash=_hash_sql(sql),
        sql_preview=preview,
        reason=reason,
    )


def _collect_cte_aliases(expression: exp.Expression) -> set[str]:
    aliases: set[str] = set()
    for with_ in expression.find_all(exp.With):
        for cte in with_.expressions:
            if isinstance(cte, exp.CTE) and cte.alias:
                aliases.add(cte.alias)
    return aliases


def _physical_table_names(expression: exp.Expression) -> set[tuple[str, str]]:
    """Return set of (schema_lower, table_lower) for real table references."""
    cte_names = _collect_cte_aliases(expression)
    out: set[tuple[str, str]] = set()
    for table in expression.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        # WITH alias reference — not a physical table
        if name in cte_names:
            continue
        # sqlglot: catalog.db.name — for dbo.job_postings often db=dbo name=job_postings
        schema = (table.db or table.catalog or _DEFAULT_SCHEMA).lower()
        out.add((schema, name.lower()))
    return out


def _root_select(expression: exp.Expression) -> exp.Select | exp.Union | None:
    if isinstance(expression, (exp.Select, exp.Union)):
        return expression
    if isinstance(expression, exp.Expression):
        for node in expression.args.values():
            if isinstance(node, exp.Expression):
                found = _root_select(node)
                if found is not None:
                    return found
    return None


def _has_forbidden_dml(root: exp.Expression) -> str | None:
    for bad in _FORBIDDEN_ROOT_TYPES:
        for _ in root.find_all(bad):
            return f"forbidden_construct:{bad.__name__}"
    return None


def _outer_limit_value(root: exp.Expression) -> int | None:
    if isinstance(root, exp.Select):
        lim = root.args.get("limit")
        if isinstance(lim, exp.Limit):
            try:
                return int(lim.expression.this)
            except (AttributeError, TypeError, ValueError):
                return None
    if isinstance(root, exp.Union):
        lim = root.args.get("limit")
        if isinstance(lim, exp.Limit):
            try:
                return int(lim.expression.this)
            except (AttributeError, TypeError, ValueError):
                return None
    return None


def validate_sql(sql: str) -> ValidationResult:
    """Validate generated SQL. Returns sql_for_execution with LIMIT applied when valid."""
    raw = sql.strip()
    if not raw:
        _log_rejection(sql, "empty_sql")
        return ValidationResult(False, "empty_sql", "")

    # Single-statement: disallow statement chaining
    without_trailing = raw.rstrip().rstrip(";").strip()
    if ";" in without_trailing:
        _log_rejection(sql, "multiple_statements")
        return ValidationResult(False, "multiple_statements", "")

    try:
        parsed = sqlglot.parse_one(without_trailing, read="postgres")
    except ParseError as exc:
        _log_rejection(sql, f"parse_error:{exc}")
        return ValidationResult(False, f"parse_error:{exc}", "")

    forbidden = _has_forbidden_dml(parsed)
    if forbidden:
        _log_rejection(sql, forbidden)
        return ValidationResult(False, forbidden, "")

    root = _root_select(parsed)
    if root is None:
        _log_rejection(sql, "not_a_select")
        return ValidationResult(False, "not_a_select", "")

    tables = _physical_table_names(parsed)
    for schema, name in tables:
        if schema != "dbo":
            reason = f"schema_not_allowed:{schema}.{name}"
            _log_rejection(sql, reason)
            return ValidationResult(False, reason, "")
        if name not in ALLOWED_TABLES:
            reason = f"table_not_allowed:{name}"
            _log_rejection(sql, reason)
            return ValidationResult(False, reason, "")

    lim = _outer_limit_value(root)
    if lim is not None and lim > _MAX_ROWS:
        _log_rejection(sql, f"limit_too_large:{lim}")
        return ValidationResult(False, f"limit_too_large:{lim}", "")

    # sqlglot Select.limit returns a new expression — assign back onto the outer node.
    if isinstance(parsed, (exp.Select, exp.Union)) and _outer_limit_value(parsed) is None:
        parsed = parsed.limit(_MAX_ROWS)
    elif isinstance(parsed, exp.Subquery):
        inner = parsed.this
        if isinstance(inner, (exp.Select, exp.Union)) and _outer_limit_value(inner) is None:
            parsed.set("this", inner.limit(_MAX_ROWS))

    try:
        final_sql = parsed.sql(dialect="postgres")
    except Exception as exc:  # noqa: BLE001
        _log_rejection(sql, f"serialize_error:{exc}")
        return ValidationResult(False, f"serialize_error:{exc}", "")

    if not re.match(r"^\s*select\b", final_sql, re.IGNORECASE):
        _log_rejection(sql, "emit_not_select")
        return ValidationResult(False, "emit_not_select", "")

    return ValidationResult(True, None, final_sql)


