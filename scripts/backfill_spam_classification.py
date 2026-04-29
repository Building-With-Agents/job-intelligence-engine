# ruff: noqa: T201
"""One-shot backfill: classify ~4,683 ``dbo.job_postings`` rows missing spam_tier — JIE #308.

Background
----------
PR-1 of #308 (this branch) closed the structural defects in the live
spam-classification path — the three UPDATE constants now persist
``spam_tier``, and the sweeper at ``scripts/sweep_unclassified_spam.py``
catches any new rows that reach ``job_postings`` without a tier.

This script handles the **existing residual** — ~4,683 rows that landed
in ``dbo.job_postings`` before #308's structural fix shipped. They have
``spam_tier IS NULL`` (and most have ``is_spam IS NULL`` + ``spam_score
IS NULL``). The sweeper would eventually drain this backlog at 200 rows
per run, but a one-shot is faster: ~10 minutes of LLM time vs ~24 hourly
sweeper runs.

The classifier internals are reused from
``scripts/sweep_unclassified_spam.py`` — same SELECT, same dedup,
same ``score_spam_preview`` call, same ``_UPDATE_SPAM_ONLY_SQL`` write.
The differences are operational:

* ``--grace-minutes 0`` by default (no grace window — the residual is
  historical, not in flight).
* ``--limit 5000`` by default (covers the full backlog in one pass).
* Optional ``--max-cost-usd`` ceiling (defaults to $30) — stops gracefully
  when reached and prints a partial-completion summary. At ~$0.001-0.005
  per row × ~4,683 rows = $5-25 expected, so $30 is generous.

Usage
-----
.. code-block:: bash

    # Dry run (default — counts candidates, makes no LLM calls, no writes)
    python scripts/backfill_spam_classification.py

    # Apply: classify the full backlog up to the cost cap
    python scripts/backfill_spam_classification.py --apply

    # Apply with a tighter cost cap and smaller batch
    python scripts/backfill_spam_classification.py --apply --max-cost-usd 5 --limit 1000

Idempotent — only touches rows where ``spam_tier IS NULL``. Reads
``PYTHON_DATABASE_URL`` from ``.env``.

**Important:** export fixtures before running with ``--apply``. Per the
JIE database protection rules (``~/.claude/CLAUDE.md``), ``job_postings``
mutations require a fixture snapshot first.

.. code-block:: bash

    python scripts/pg-seed-data/export_fixtures.py --scope all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.data_store.database import get_engine, session_scope  # noqa: E402
from common.env import load_repo_root_dotenv  # noqa: E402
from enrichment.classifiers.spam_preview import (  # noqa: E402
    apply_spam_tiers,
    score_spam_preview,
)
from enrichment.job_postings_promotion import _UPDATE_SPAM_ONLY_SQL  # noqa: E402
from scripts.sweep_unclassified_spam import (  # noqa: E402
    _FETCH_CANDIDATES_SQL,
    _extraction_dict,
)

log = structlog.get_logger()


# Default cost ceiling for ``--apply`` mode. The script aborts gracefully
# when accumulated LLM cost (read from `result.llm_cost_usd` if present, or
# estimated as a flat per-row figure if not) crosses this. Override via CLI.
DEFAULT_MAX_COST_USD = 30.0
# Conservative per-row cost estimate when the classifier doesn't expose its
# actual spend in the SpamPreviewResult. Used as a fallback only.
_FALLBACK_ROW_COST_USD = 0.005


def _row_cost(result: Any) -> float:
    """Best-effort per-row cost extraction from the classifier result."""
    cost = getattr(result, "llm_cost_usd", None)
    if isinstance(cost, (int, float)) and cost >= 0:
        return float(cost)
    cost = getattr(result, "cost_usd", None)
    if isinstance(cost, (int, float)) and cost >= 0:
        return float(cost)
    return _FALLBACK_ROW_COST_USD


def backfill(
    *,
    limit: int = 5000,
    grace_minutes: int = 0,
    apply: bool = False,
    max_cost_usd: float = DEFAULT_MAX_COST_USD,
) -> dict[str, Any]:
    """Classify all rows where ``spam_tier IS NULL`` (subject to ``limit`` and cost cap).

    Returns counts: ``{"candidates": N, "classified_clean": N,
    "classified_flagged": N, "classified_uncertain": N, "failed": N,
    "cost_usd": $..., "stopped_on_cost_cap": bool}``.

    When ``apply=False`` (default), counts only — no LLM calls, no writes.
    """
    engine = get_engine()
    counts: dict[str, Any] = {
        "candidates": 0,
        "classified_clean": 0,
        "classified_flagged": 0,
        "classified_uncertain": 0,
        "failed": 0,
        "cost_usd": 0.0,
        "stopped_on_cost_cap": False,
    }

    with engine.connect() as conn:
        rows = (
            conn.execute(
                _FETCH_CANDIDATES_SQL,
                {"lim": limit, "grace_minutes": grace_minutes},
            )
            .mappings()
            .all()
        )

    counts["candidates"] = len(rows)
    if not rows:
        log.info("spam_backfill_no_candidates", limit=limit, grace_minutes=grace_minutes)
        return counts

    log.info(
        "spam_backfill_candidates_found",
        n=len(rows),
        apply=apply,
        max_cost_usd=max_cost_usd,
    )

    if not apply:
        for row in rows[:10]:
            print(f"  [dry-run] job_posting_id={row['job_posting_id']} title={(row.get('job_title') or '')[:60]!r}")
        if len(rows) > 10:
            print(f"  [dry-run] ... and {len(rows) - 10} more")
        return counts

    accumulated_cost = 0.0
    for row in rows:
        if accumulated_cost >= max_cost_usd:
            counts["stopped_on_cost_cap"] = True
            log.warning(
                "spam_backfill_cost_cap_reached",
                cost_usd=accumulated_cost,
                max_cost_usd=max_cost_usd,
                processed=(counts["classified_clean"] + counts["classified_flagged"] + counts["classified_uncertain"]),
                remaining=len(rows)
                - (
                    counts["classified_clean"]
                    + counts["classified_flagged"]
                    + counts["classified_uncertain"]
                    + counts["failed"]
                ),
            )
            break

        jp_id = row["job_posting_id"]
        try:
            extraction, extraction_failed, extraction_empty = _extraction_dict(dict(row))
            result = score_spam_preview(
                job_title=row.get("job_title") or "",
                job_description=row.get("job_description") or "",
                extraction=extraction,
                extraction_failed=extraction_failed,
                extraction_empty=extraction_empty,
            )
            accumulated_cost += _row_cost(result)

            if result.spam_score is None:
                tier = "uncertain"
                is_spam: bool | None = None
                spam_score: float | None = None
            else:
                is_spam, tier = apply_spam_tiers(float(result.spam_score))
                spam_score = float(result.spam_score)

            with session_scope() as session:
                session.execute(
                    _UPDATE_SPAM_ONLY_SQL,
                    {
                        "job_posting_id": jp_id,
                        "is_spam": is_spam,
                        "spam_score": spam_score,
                        "spam_tier": tier,
                    },
                )

            if tier == "clean":
                counts["classified_clean"] += 1
            elif tier == "flagged":
                counts["classified_flagged"] += 1
            else:
                counts["classified_uncertain"] += 1
            log.info(
                "spam_backfill_classified",
                job_posting_id=jp_id,
                tier=tier,
                spam_score=spam_score,
                cost_running=round(accumulated_cost, 4),
            )
        except Exception as exc:
            counts["failed"] += 1
            log.warning(
                "spam_backfill_failed",
                job_posting_id=jp_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )

    counts["cost_usd"] = round(accumulated_cost, 4)
    log.info("spam_backfill_complete", **counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot backfill: classify job_postings rows missing spam_tier (JIE #308). "
            "Default is dry-run; pass --apply to run the classifier and write. "
            "Export fixtures BEFORE --apply per JIE database protection rules."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually run the classifier and write results.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum rows to attempt per run. Default 5000 covers full backlog.",
    )
    parser.add_argument(
        "--grace-minutes",
        type=int,
        default=0,
        help="Skip rows newer than this many minutes. Default 0 — backfill is historical.",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=DEFAULT_MAX_COST_USD,
        help=f"Soft ceiling on accumulated LLM cost (default ${DEFAULT_MAX_COST_USD:g}).",
    )
    args = parser.parse_args()

    load_repo_root_dotenv()
    counts = backfill(
        limit=args.limit,
        grace_minutes=args.grace_minutes,
        apply=args.apply,
        max_cost_usd=args.max_cost_usd,
    )

    print()
    print("=" * 60)
    print(f"Spam backfill summary ({'APPLY' if args.apply else 'DRY-RUN'}):")
    print(f"  candidates found       : {counts['candidates']}")
    if args.apply:
        print(f"  classified clean       : {counts['classified_clean']}")
        print(f"  classified flagged     : {counts['classified_flagged']}")
        print(f"  classified uncertain   : {counts['classified_uncertain']}")
        print(f"  failed                 : {counts['failed']}")
        print(f"  accumulated cost (USD) : ${counts['cost_usd']:.4f}")
        if counts["stopped_on_cost_cap"]:
            print(f"  ⚠ STOPPED at --max-cost-usd ${args.max_cost_usd:g} — re-run to continue")
    print("=" * 60)
    if args.apply and counts["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
