#!/usr/bin/env python3
"""Week 9 — minimal operational refresh for the six Week 7 aggregate tables.

Wraps existing Week 7 computation functions in ``analytics.aggregators`` with
per-table timing, before/after row counts, and isolated ``try/except`` so a
failure in one table does not halt the whole refresh.

Tables refreshed (in dependency order):

1. ``skill_demand_weekly``      (:func:`refresh_skill_demand_weekly`)
2. ``tool_demand_weekly``       (:func:`refresh_tool_demand_weekly`)
3. ``skill_velocity``           (:func:`refresh_skill_velocity` — skipped if step 1 failed)
4. ``skill_co_occurrence``      (:func:`refresh_skill_co_occurrence` — skipped if step 1 failed)
5. ``sector_summary_weekly``    (:func:`compute_sector_summary_weekly`, unless ``--skip-pipeline``)
6. ``geo_demand_weekly``        (:func:`compute_geo_demand_weekly`, unless ``--skip-pipeline``)

Usage (repo root with venv activated)::

    python scripts/refresh_aggregates.py
    python scripts/refresh_aggregates.py --week 2026-04-13       # target a specific Monday (UTC)
    python scripts/refresh_aggregates.py --skip-pipeline          # steps 1-4 only

Deliberate non-goals (Week 12 Deferred Work Log): no job_runs heartbeat,
no pg_cron/host cron wiring, no retry/backoff/alerting, no clustering or
disruption refresh. See ``docs/runbooks/WEEK09_REFRESH_AGGREGATES_FINDINGS.md``.

Exit code: ``0`` when the script runs to completion (regardless of per-table
failures); ``1`` when the database is unreachable at startup or an unhandled
exception escapes the runner. Row counts in the stdout summary are the
authoritative signal for "did the refresh actually work".

Requires ``PYTHON_DATABASE_URL`` in the repo-root ``.env`` and a populated
``dbo.job_postings`` / ``dbo.extracted_intelligence``.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

import structlog  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from analytics.agent import default_analytics_target_week  # noqa: E402
from analytics.aggregators import (  # noqa: E402
    compute_geo_demand_weekly,
    compute_sector_summary_weekly,
    refresh_skill_co_occurrence,
    refresh_skill_demand_weekly,
    refresh_skill_velocity,
    refresh_tool_demand_weekly,
)
from common.data_store.database import check_db_connection, session_scope  # noqa: E402
from common.data_store.models import (  # noqa: E402
    GeoDemandWeekly,
    SectorSummaryWeekly,
    SkillCoOccurrence,
    SkillDemandWeekly,
    SkillVelocity,
    ToolDemandWeekly,
)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=False),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger()


STATUS_SUCCESS = "success"
STATUS_FAILURE = "failure"
STATUS_SKIPPED = "skipped"
_ERROR_TRUNCATE = 240


@dataclass
class StepResult:
    name: str
    status: str
    rows_before: int | None = None
    rows_after: int | None = None
    rows_delta: int | None = None
    duration_ms: float = 0.0
    error: str | None = None
    skip_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _TableSpec:
    name: str
    model: Any
    week_column: Any
    refresh_callable: Callable[..., Any]
    depends_on: str | None = None
    persist_returned_rows: bool = False
    tags: list[str] = field(default_factory=list)


def _count_rows_for_week(model: Any, week_column: Any, target_week: date) -> int:
    """Count rows in ``model`` matching ``week_column == target_week``.

    Runs in its own ``session_scope`` so the read observes data written by the
    prior refresh's commit and is isolated from a failed refresh's rollback.
    """
    with session_scope() as session:
        stmt = select(func.count()).select_from(model).where(week_column == target_week)
        return int(session.execute(stmt).scalar_one())


def _run_refresh(spec: _TableSpec, target_week: date) -> StepResult:
    """Execute one table refresh: count before, compute, count after, record timing.

    Any exception raised by the refresh callable is caught and recorded on the
    returned :class:`StepResult` — it never propagates out of this function, so
    a single failing table does not halt the overall run.
    """
    try:
        rows_before = _count_rows_for_week(spec.model, spec.week_column, target_week)
    except Exception as exc:
        rows_before = None
        log.warning("refresh_count_before_failed", table=spec.name, error=str(exc))

    started = time.perf_counter()
    result = StepResult(name=spec.name, status=STATUS_FAILURE, rows_before=rows_before)

    try:
        with session_scope() as session:
            out = spec.refresh_callable(session, target_week)
            if spec.persist_returned_rows and isinstance(out, list):
                session.add_all(out)
        result.status = STATUS_SUCCESS
    except Exception as exc:
        result.error = str(exc)[:_ERROR_TRUNCATE]
        log.exception(
            "refresh_table_failed",
            table=spec.name,
            target_week=str(target_week),
            error=result.error,
        )
    finally:
        result.duration_ms = round((time.perf_counter() - started) * 1000.0, 2)

    try:
        result.rows_after = _count_rows_for_week(spec.model, spec.week_column, target_week)
    except Exception as exc:
        result.rows_after = None
        log.warning("refresh_count_after_failed", table=spec.name, error=str(exc))

    if result.rows_before is not None and result.rows_after is not None:
        result.rows_delta = result.rows_after - result.rows_before

    log.info(
        "refresh_table_complete",
        table=spec.name,
        status=result.status,
        rows_before=result.rows_before,
        rows_after=result.rows_after,
        rows_delta=result.rows_delta,
        duration_ms=result.duration_ms,
        target_week=str(target_week),
    )
    return result


def _skipped(name: str, reason: str) -> StepResult:
    return StepResult(name=name, status=STATUS_SKIPPED, skip_reason=reason)


def _build_specs() -> list[_TableSpec]:
    """Return the fixed ordered list of six tables and their refresh entry points."""
    return [
        _TableSpec(
            name="skill_demand_weekly",
            model=SkillDemandWeekly,
            week_column=SkillDemandWeekly.week_start,
            refresh_callable=refresh_skill_demand_weekly,
            tags=["aggregate"],
        ),
        _TableSpec(
            name="tool_demand_weekly",
            model=ToolDemandWeekly,
            week_column=ToolDemandWeekly.week_start,
            refresh_callable=refresh_tool_demand_weekly,
            tags=["aggregate"],
        ),
        _TableSpec(
            name="skill_velocity",
            model=SkillVelocity,
            week_column=SkillVelocity.velocity_week,
            refresh_callable=refresh_skill_velocity,
            depends_on="skill_demand_weekly",
            tags=["aggregate"],
        ),
        _TableSpec(
            name="skill_co_occurrence",
            model=SkillCoOccurrence,
            week_column=SkillCoOccurrence.week_start,
            refresh_callable=refresh_skill_co_occurrence,
            depends_on="skill_demand_weekly",
            tags=["aggregate"],
        ),
        _TableSpec(
            name="sector_summary_weekly",
            model=SectorSummaryWeekly,
            week_column=SectorSummaryWeekly.week_start,
            refresh_callable=compute_sector_summary_weekly,
            tags=["pipeline"],
        ),
        _TableSpec(
            name="geo_demand_weekly",
            model=GeoDemandWeekly,
            week_column=GeoDemandWeekly.week_start,
            refresh_callable=compute_geo_demand_weekly,
            persist_returned_rows=True,
            tags=["pipeline"],
        ),
    ]


def _print_summary(results: list[StepResult], total_duration_ms: float, target_week: date) -> None:
    """Human-readable run summary to stdout (the terminal is the Week 9 dashboard)."""
    counts = {
        STATUS_SUCCESS: sum(1 for r in results if r.status == STATUS_SUCCESS),
        STATUS_FAILURE: sum(1 for r in results if r.status == STATUS_FAILURE),
        STATUS_SKIPPED: sum(1 for r in results if r.status == STATUS_SKIPPED),
    }

    print()
    print("=" * 100)
    print(f"refresh_aggregates summary  target_week={target_week}  total_duration_ms={total_duration_ms:.1f}")
    print(f"success={counts[STATUS_SUCCESS]}  failure={counts[STATUS_FAILURE]}  skipped={counts[STATUS_SKIPPED]}")
    print("-" * 100)
    print(f"{'table':26}  {'status':8}  {'before':>10}  {'after':>10}  {'delta':>8}  {'ms':>10}  note")
    print("-" * 100)
    for r in results:
        before = "-" if r.rows_before is None else str(r.rows_before)
        after = "-" if r.rows_after is None else str(r.rows_after)
        delta = "-" if r.rows_delta is None else f"{r.rows_delta:+d}"
        note = r.error or r.skip_reason or ""
        print(f"{r.name:26}  {r.status:8}  {before:>10}  {after:>10}  {delta:>8}  {r.duration_ms:>10.2f}  {note}")
    print("=" * 100)


def _parse_week(raw: str | None) -> date:
    """Parse ``--week`` as an ISO Monday; default to prior Monday UTC."""
    if raw is None:
        return default_analytics_target_week()
    cleaned = raw.strip()
    if cleaned.upper() in ("YYYY-MM-DD", "<YYYY-MM-DD>"):
        raise argparse.ArgumentTypeError(
            "'YYYY-MM-DD' is a documentation placeholder. Pass a real Monday, e.g. --week 2026-04-13"
        )
    try:
        parsed = date.fromisoformat(cleaned[:10])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid --week {raw!r}. Expected ISO date YYYY-MM-DD (Monday), e.g. 2026-04-13"
        ) from exc
    return parsed


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the six Week 7 analytics aggregate tables (minimal orchestration).",
    )
    parser.add_argument(
        "--week",
        default=None,
        help="ISO date of the target Monday UTC (e.g. 2026-04-13). Defaults to prior ISO week Monday.",
    )
    parser.add_argument(
        "--skip-pipeline",
        action="store_true",
        help="Refresh the 4 'aggregate' tables only; skip sector_summary_weekly + geo_demand_weekly.",
    )
    args = parser.parse_args()

    correlation_id = f"refresh-aggregates-{_utc_now_iso()}"

    try:
        target_week = _parse_week(args.week)
    except argparse.ArgumentTypeError as exc:
        log.error("refresh_args_invalid", error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1

    log.info(
        "refresh_run_start",
        correlation_id=correlation_id,
        target_week=str(target_week),
        skip_pipeline=args.skip_pipeline,
    )

    if not check_db_connection():
        log.error(
            "refresh_db_unreachable",
            correlation_id=correlation_id,
            hint="Check PYTHON_DATABASE_URL and that Postgres is running.",
        )
        print("error: database unreachable — see refresh_db_unreachable log", file=sys.stderr)
        return 1

    specs = _build_specs()
    results: list[StepResult] = []
    completed: dict[str, StepResult] = {}

    t0 = time.perf_counter()
    for spec in specs:
        if args.skip_pipeline and "pipeline" in spec.tags:
            result = _skipped(spec.name, "skip-pipeline flag")
        elif spec.depends_on is not None:
            dep = completed.get(spec.depends_on)
            if dep is None or dep.status != STATUS_SUCCESS:
                result = _skipped(
                    spec.name,
                    f"dependency {spec.depends_on} not successful",
                )
            else:
                result = _run_refresh(spec, target_week)
        else:
            result = _run_refresh(spec, target_week)

        results.append(result)
        completed[spec.name] = result

    total_duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    _print_summary(results, total_duration_ms, target_week)

    log.info(
        "refresh_run_complete",
        correlation_id=correlation_id,
        target_week=str(target_week),
        total_duration_ms=total_duration_ms,
        success_count=sum(1 for r in results if r.status == STATUS_SUCCESS),
        failure_count=sum(1 for r in results if r.status == STATUS_FAILURE),
        skipped_count=sum(1 for r in results if r.status == STATUS_SKIPPED),
        results=[r.as_dict() for r in results],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
