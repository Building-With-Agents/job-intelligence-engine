# ruff: noqa: T201
"""Backfill dbo.canonical_roles.role_family from cluster labels (JIE #362).

Rule-based classification runs first (``analytics.canonical_roles.role_family``);
optional ``--use-llm`` batch for rows still unmapped.

Idempotent: only rows with ``role_family IS NULL`` are updated unless ``--force``.

Exit behavior:
  - Default: print stats and warnings; exit 0 (partial dev DBs stay unblocked).
  - ``--strict``: exit 1 when unmapped rate exceeds ``UNMAPPED_THRESHOLD`` (10%).

Prerequisites:
    python scripts/db_check.py migrate

Usage (from repo root, venv activated):

    python scripts/backfill_role_family.py
    python scripts/backfill_role_family.py --use-llm
    python scripts/backfill_role_family.py --strict
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from analytics.canonical_roles.role_family import (  # noqa: E402
    classify_canonical_role,
    classify_canonical_role_llm_batch,
)
from common.data_store.database import get_engine  # noqa: E402
from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

UNMAPPED_THRESHOLD = 0.10

_COLUMN_CHECK = """
SELECT 1
FROM information_schema.columns
WHERE table_schema = 'dbo'
  AND table_name = 'canonical_roles'
  AND column_name = 'role_family'
LIMIT 1
"""

_SELECT_ROWS = """
SELECT role_id, label, representative_titles, top_skills
FROM dbo.canonical_roles
WHERE (:force OR role_family IS NULL)
ORDER BY role_id
"""

_STATS = """
SELECT
    COUNT(*) AS total,
    COUNT(role_family) AS with_family,
    COUNT(*) FILTER (WHERE role_family IS NULL) AS still_unmapped
FROM dbo.canonical_roles
"""

_FAMILY_DIST = """
SELECT role_family, COUNT(*) AS n
FROM dbo.canonical_roles
WHERE role_family IS NOT NULL
GROUP BY role_family
ORDER BY n DESC
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill canonical_roles.role_family")
    parser.add_argument("--force", action="store_true", help="Reclassify rows that already have role_family")
    parser.add_argument("--use-llm", action="store_true", help="LLM batch for labels still unmapped after rules")
    parser.add_argument(
        "--strict",
        action="store_true",
        help=f"Exit 1 when unmapped rate > {UNMAPPED_THRESHOLD:.0%} (CI/smoke)",
    )
    args = parser.parse_args()

    engine = get_engine()
    with engine.connect() as conn:
        if not conn.execute(text(_COLUMN_CHECK)).scalar():
            print(
                "ERROR: dbo.canonical_roles.role_family does not exist.\nRun: python scripts/db_check.py migrate",
                file=sys.stderr,
            )
            sys.exit(1)

    mapped = 0
    unmapped_after_rules = 0
    llm_mapped = 0

    with engine.begin() as conn:
        rows = conn.execute(
            text(_SELECT_ROWS),
            {"force": args.force},
        ).fetchall()

        pending_llm: list[tuple[str, str]] = []
        for row in rows:
            role_id = str(row.role_id)
            label = str(row.label or "")
            rep = row.representative_titles
            skills = row.top_skills
            rep_list = list(rep) if isinstance(rep, list) else []
            skills_list = list(skills) if isinstance(skills, list) else None
            family = classify_canonical_role(
                label,
                representative_titles=rep_list,
                top_skills=skills_list,
            )
            if family:
                conn.execute(
                    text("UPDATE dbo.canonical_roles SET role_family = :fam WHERE role_id = :rid"),
                    {"fam": family, "rid": role_id},
                )
                mapped += 1
            else:
                pending_llm.append((role_id, label))
                unmapped_after_rules += 1

        if args.use_llm and pending_llm:
            batch_size = 25
            for i in range(0, len(pending_llm), batch_size):
                chunk = pending_llm[i : i + batch_size]
                assignments = classify_canonical_role_llm_batch(chunk)
                for rid, fam in assignments.items():
                    conn.execute(
                        text("UPDATE dbo.canonical_roles SET role_family = :fam WHERE role_id = :rid"),
                        {"fam": fam, "rid": rid},
                    )
                    llm_mapped += 1

    with engine.connect() as conn:
        stats = conn.execute(text(_STATS)).one()
        total = int(stats.total or 0)
        with_family = int(stats.with_family or 0)
        still_unmapped = int(stats.still_unmapped or 0)
        dist = conn.execute(text(_FAMILY_DIST)).fetchall()

    print("backfill_role_family complete")
    print(f"  rows considered: {len(rows)}")
    print(f"  rule-mapped this run: {mapped}")
    print(f"  unmapped after rules: {unmapped_after_rules}")
    print(f"  llm-mapped this run: {llm_mapped}")
    print(f"  total canonical_roles: {total}")
    print(f"  with role_family: {with_family}")
    print(f"  still unmapped: {still_unmapped}")
    print("  per-family distribution:")
    for row in dist:
        print(f"    {row.role_family}: {row.n}")

    if total > 0:
        unmapped_rate = still_unmapped / total
        if unmapped_rate > UNMAPPED_THRESHOLD:
            msg = (
                f"WARNING: unmapped rate {unmapped_rate:.1%} exceeds threshold "
                f"{UNMAPPED_THRESHOLD:.0%} ({still_unmapped}/{total})"
            )
            print(msg, file=sys.stderr)
            if args.strict:
                sys.exit(1)
    print("  exit: 0 (use --strict for non-zero exit on high unmapped rate)")


if __name__ == "__main__":
    main()
