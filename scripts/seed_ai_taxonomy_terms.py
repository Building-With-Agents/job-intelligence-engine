"""One-shot seed: insert AI-tool / AI-adjacent terms into dbo.skills.

JIE #349 — The skill taxonomy gate in ``analytics/query_engine/router.py``
blocks disruption questions when extracted skill names (e.g. "Copilot",
"ChatGPT", "RPA") are absent from ``dbo.skills``.  This script permanently
adds those terms so they surface in all analytics dimensions (skill demand,
velocity, co-occurrence) once embeddings are backfilled.

Until this script is run, the router's ``_AI_TOOL_SUPPLEMENTAL_TERMS``
frozenset acts as a stop-gap so the gate does not block valid AI-intent
questions.

Usage (repo root, venv, ``PYTHON_DATABASE_URL`` set)::

    python scripts/seed_ai_taxonomy_terms.py --dry-run
    python scripts/seed_ai_taxonomy_terms.py

Refs: #349
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO / ".env")

import structlog  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from analytics.query_engine.router import (  # noqa: E402
    _AI_TOOL_SUPPLEMENTAL_TERMS,
)
from common.data_store.database import session_scope  # noqa: E402
from common.data_store.models import Skill  # noqa: E402

log = structlog.get_logger()

# Subcategory IDs from dbo.skill_subcategories (fixture-seeded)
_AIML_SUBCATEGORY_ID = "F6CD1DF0-8352-4B4E-BE9B-E5291BC95702"  # AI/ML
_AUTOMATION_SUBCATEGORY_ID = "344DEF26-88C4-430F-A7D2-0195A6EFF498"  # IT Automation

# Terms that belong under the Automation subcategory; everything else → AI/ML.
_AUTOMATION_TERMS: frozenset[str] = frozenset(
    {
        "automation",
        "rpa",
        "robotic process automation",
        "workflow automation",
        "process automation",
        "uipath",
        "blue prism",
        "automation anywhere",
        "testim",
        "mabl",
        "applitools",
    }
)


def _subcategory_for(term: str) -> str:
    return _AUTOMATION_SUBCATEGORY_ID if term in _AUTOMATION_TERMS else _AIML_SUBCATEGORY_ID


def _skill_type_for(term: str) -> str:
    """Rough type assignment; "tool" for named products, "knowledge" for concepts."""
    tools = {
        "copilot",
        "github copilot",
        "cursor",
        "cursor ide",
        "claude",
        "claude.ai",
        "chatgpt",
        "gpt-4",
        "gpt4",
        "uipath",
        "blue prism",
        "automation anywhere",
        "testim",
        "mabl",
        "applitools",
        "langchain",
    }
    return "tool" if term in tools else "knowledge"


def run(*, dry_run: bool) -> None:
    now = datetime.now(timezone.utc)

    # Seed every term in the supplemental set.  Alias targets (canonical forms)
    # are already members of the set by construction, so no special casing needed.
    canonical_terms = sorted(_AI_TOOL_SUPPLEMENTAL_TERMS)

    with session_scope() as session:
        # Find which terms already exist (case-insensitive).
        existing: set[str] = set(
            session.execute(
                select(func.lower(Skill.skill_name)).where(
                    func.lower(Skill.skill_name).in_([t.lower() for t in canonical_terms])
                )
            )
            .scalars()
            .all()
        )

        to_insert = [t for t in canonical_terms if t.lower() not in existing]

        log.info(
            "seed_ai_taxonomy_terms_plan",
            total_terms=len(canonical_terms),
            already_present=len(existing),
            to_insert=len(to_insert),
            dry_run=dry_run,
        )

        if not to_insert:
            log.info("seed_ai_taxonomy_terms_noop", reason="all_terms_already_present")
            return

        if dry_run:
            for term in to_insert:
                log.info("seed_ai_taxonomy_terms_would_insert", term=term)
            return

        inserted = 0
        for term in to_insert:
            skill = Skill(
                skill_id=str(uuid.uuid4()).upper(),
                skill_subcategory_id=_subcategory_for(term),
                skill_name=term,
                skill_info_url="",
                skill_type=_skill_type_for(term),
                createdat=now,
                updatedat=now,
            )
            session.add(skill)
            inserted += 1

        session.flush()
        log.info("seed_ai_taxonomy_terms_done", inserted=inserted)

    log.info("seed_ai_taxonomy_terms_committed", inserted=inserted)
    print(f"Inserted {inserted} AI-tool taxonomy terms into dbo.skills.")
    print("Run  python scripts/seed_esco_embeddings.py  to backfill embeddings for the new rows.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the terms that would be inserted without committing.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(dry_run=args.dry_run)
