# ruff: noqa: T201
"""Backfill ``dbo.role_snapshot_weekly`` across every populated posted week.

The existing writer :func:`refresh_role_snapshot_weekly` is correct but is only
invoked for the **current** ISO Monday by ``scripts/run_clustering.py`` and
``analytics/agent.py``. When ``date_posted`` lags the calendar week, that
single-week call inserts 0 rows even though many roled postings exist.

This script discovers every ``week_start`` that has at least one
``job_postings`` row with ``canonical_role_id IS NOT NULL`` and calls the
existing writer once per week, in its own ``session_scope()``. Per-week
``try/except`` so one bad week does not block the rest, mirroring
``scripts/refresh_aggregates.py`` style.

Usage (repo root with venv activated)::

    python scripts/backfill_role_snapshot_weekly.py            # backfill all weeks
    python scripts/backfill_role_snapshot_weekly.py --dry-run  # show plan, write nothing
    python scripts/backfill_role_snapshot_weekly.py --week 2026-04-06  # one week only

Exit code: ``0`` when the script runs to completion (regardless of per-week
failures); ``1`` when the database is unreachable at startup or ``--week`` is
malformed.

Requires ``PYTHON_DATABASE_URL`` in the repo-root ``.env`` and a populated
``dbo.canonical_roles`` / ``dbo.job_postings.canonical_role_id``.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

import structlog  # noqa: E402
from sqlalchemy import text  # noqa: E402

from analytics.canonical_roles.snapshots import refresh_role_snapshot_weekly  # noqa: E402
from common.data_store.database import check_db_connection, session_scope  # noqa: E402

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
_ERROR_TRUNCATE = 240

_DISCOVER_WEEKS_SQL = text(
    """
    SELECT DISTINCT DATE_TRUNC('week', date_posted AT TIME ZONE 'UTC')::date AS week_start
    FROM dbo.job_postings
    WHERE canonical_role_id IS NOT NULL
      AND date_posted IS NOT NULL
    ORDER BY 1
    """
)


@dataclass
class WeekResult:
    week_start: date
    status: str
    rows_inserted: int | None = None
    duration_ms: float = 0.0
    error: str | None = None


def _discover_weeks() -> list[date]:
    with session_scope() as session:
        rows = session.execute(_DISCOVER_WEEKS_SQL).all()
        return [row[0] for row in rows]


def _refresh_one(week_start: date) -> WeekResult:
    started = time.perf_counter()
    result = WeekResult(week_start=week_start, status=STATUS_FAILURE)
    try:
        with session_scope() as session:
            inserted = refresh_role_snapshot_weekly(session, week_start=week_start)
        result.status = STATUS_SUCCESS
        result.rows_inserted = int(inserted)
    except Exception as exc:
        result.error = str(exc)[:_ERROR_TRUNCATE]
        log.exception(
            "backfill_week_failed",
            week_start=str(week_start),
            error=result.error,
        )
    finally:
        result.duration_ms = round((time.perf_counter() - started) * 1000.0, 2)

    log.info(
        "backfill_week_complete",
        week_start=str(week_start),
        status=result.status,
        rows_inserted=result.rows_inserted,
        duration_ms=result.duration_ms,
    )
    return result


def _print_plan(weeks: list[date]) -> None:
    print()
    print("=" * 70)
    print(f"backfill_role_snapshot_weekly  dry_run=True  weeks={len(weeks)}")
    print("-" * 70)
    if not weeks:
        print("(no populated weeks discovered — nothing to backfill)")
    else:
        for w in weeks:
            print(f"  {w}")
    print("=" * 70)


def _print_summary(results: list[WeekResult], total_duration_ms: float) -> None:
    counts = {
        STATUS_SUCCESS: sum(1 for r in results if r.status == STATUS_SUCCESS),
        STATUS_FAILURE: sum(1 for r in results if r.status == STATUS_FAILURE),
    }
    total_rows = sum((r.rows_inserted or 0) for r in results if r.status == STATUS_SUCCESS)

    print()
    print("=" * 80)
    print(
        f"backfill_role_snapshot_weekly summary  weeks={len(results)}  "
        f"total_rows_inserted={total_rows}  total_duration_ms={total_duration_ms:.1f}"
    )
    print(f"success={counts[STATUS_SUCCESS]}  failure={counts[STATUS_FAILURE]}")
    print("-" * 80)
    print(f"{'week_start':12}  {'status':8}  {'rows':>6}  {'ms':>10}  note")
    print("-" * 80)
    for r in results:
        rows = "-" if r.rows_inserted is None else str(r.rows_inserted)
        note = r.error or ""
        print(f"{str(r.week_start):12}  {r.status:8}  {rows:>6}  {r.duration_ms:>10.2f}  {note}")
    print("=" * 80)


def _parse_week(raw: str) -> date:
    cleaned = raw.strip()
    if cleaned.upper() in ("YYYY-MM-DD", "<YYYY-MM-DD>"):
        raise argparse.ArgumentTypeError(
            "'YYYY-MM-DD' is a documentation placeholder. Pass a real Monday, e.g. --week 2026-04-13"
        )
    try:
        return date.fromisoformat(cleaned[:10])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid --week {raw!r}. Expected ISO date YYYY-MM-DD (Monday), e.g. 2026-04-13"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill dbo.role_snapshot_weekly across every week_start that has "
            "job_postings with canonical_role_id."
        ),
    )
    parser.add_argument(
        "--week",
        default=None,
        help="ISO date of the target Monday UTC (e.g. 2026-04-13). When omitted, "
        "all populated weeks are backfilled.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the discovered weeks without writing.",
    )
    args = parser.parse_args()

    if not check_db_connection():
        log.error(
            "backfill_db_unreachable",
            hint="Check PYTHON_DATABASE_URL and that Postgres is running.",
        )
        print("error: database unreachable", file=sys.stderr)
        return 1

    if args.week is not None:
        try:
            weeks = [_parse_week(args.week)]
        except argparse.ArgumentTypeError as exc:
            log.error("backfill_args_invalid", error=str(exc))
            print(f"error: {exc}", file=sys.stderr)
            return 1
    else:
        weeks = _discover_weeks()

    if args.dry_run:
        _print_plan(weeks)
        return 0

    if not weeks:
        print("(no populated weeks discovered — nothing to backfill)")
        return 0

    results: list[WeekResult] = []
    t0 = time.perf_counter()
    for week_start in weeks:
        results.append(_refresh_one(week_start))
    total_duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    _print_summary(results, total_duration_ms)

    log.info(
        "backfill_run_complete",
        weeks=len(results),
        success_count=sum(1 for r in results if r.status == STATUS_SUCCESS),
        failure_count=sum(1 for r in results if r.status == STATUS_FAILURE),
        total_rows_inserted=sum((r.rows_inserted or 0) for r in results if r.status == STATUS_SUCCESS),
        total_duration_ms=total_duration_ms,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
