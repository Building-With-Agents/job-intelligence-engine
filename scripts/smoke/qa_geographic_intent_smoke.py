#!/usr/bin/env python3
"""Regression smoke test for JIE #257 — geographic intent classifier fix.

Runs all 10 geographic golden questions (gq-041–050) through the live intent
classifier and verifies every question returns "geographic".  In v1-baseline all
10 returned "employer" due to the missing city-primacy rule in the prompt.

Exit code: 0 if all questions pass, 1 if any question returns a wrong intent.

Usage:
    python scripts/smoke/qa_geographic_intent_smoke.py
    python scripts/smoke/qa_geographic_intent_smoke.py --verbose

Requires LLM_DEFAULT (chat-gpt41mini) in the repo-root .env.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from analytics.query_engine.intent import classify_workforce_question  # noqa: E402

# ---------------------------------------------------------------------------
# gq-041–050: all geographic golden questions from the Pair C corpus
# ---------------------------------------------------------------------------

_GEO_QUESTIONS: list[tuple[str, str]] = [
    (
        "gq-041",
        "Show all El Paso, TX postings for AI agent developer, prompt engineer, or LLM engineer"
        " roles in the agentic_era period.",
    ),
    (
        "gq-042",
        "List every Las Cruces, NM data engineer posting from the last 90 days with required"
        " skills including Python, SQL, and a cloud platform.",
    ),
    (
        "gq-043",
        "Pull all El Paso, TX healthcare-IT postings — including EHR analyst, health informatics"
        " specialist, and clinical data analyst roles — that require AI or ML skills.",
    ),
    (
        "gq-044",
        "Show all Las Cruces, NM DevOps and site-reliability engineer postings requiring"
        " Kubernetes or Terraform experience.",
    ),
    (
        "gq-045",
        "Find all El Paso, TX frontend developer postings mentioning React or Next.js, posted"
        " in the post_gpt4 or agentic_era periods.",
    ),
    (
        "gq-046",
        "List every Las Cruces, NM cybersecurity posting from the last 12 months requiring a"
        " security clearance or a named industry certification such as CISSP, CISA, or CompTIA"
        " Security+.",
    ),
    (
        "gq-047",
        "Retrieve all El Paso, TX entry-level IT postings that have a published salary range, grouped by job family.",
    ),
    (
        "gq-048",
        "Find all Borderplex (El Paso and Las Cruces combined) fintech or regtech developer"
        " postings from the agentic_era period.",
    ),
    (
        "gq-049",
        "Show all El Paso, TX legal-tech and e-discovery analyst postings from the past 6"
        " months, with any that mention AI workflows flagged.",
    ),
    (
        "gq-050",
        "List all Las Cruces, NM AI/ML researcher and applied-scientist postings, highlighting"
        " any university-affiliated employers such as NMSU, UTEP, or EPCC.",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test geographic intent classification for JIE #257.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Print per-question detail.")
    args = parser.parse_args()

    print(f"Running geographic intent smoke — {len(_GEO_QUESTIONS)} questions\n")

    passed = 0
    failed: list[str] = []

    for gq_id, question in _GEO_QUESTIONS:
        correlation_id = f"{gq_id}-smoke-{uuid.uuid4().hex[:6]}"
        result = classify_workforce_question(question, correlation_id=correlation_id)
        intent = result["intent"]
        confidence = result["confidence"]
        ok = intent == "geographic"

        if ok:
            passed += 1
            status = "PASS"
        else:
            failed.append(gq_id)
            status = "FAIL"

        if args.verbose or not ok:
            print(f"  [{status}] {gq_id}: intent={intent!r}  confidence={confidence:.2f}")
            if args.verbose:
                geo_terms = result.get("extracted_entities", {}).get("geographic_terms", [])
                print(f"         geo_terms={geo_terms}")
                print(f"         question: {question[:80]}...")
                print()
        else:
            print(f"  [{status}] {gq_id}: intent={intent!r}  confidence={confidence:.2f}")

    print()
    print(f"Results: {passed}/{len(_GEO_QUESTIONS)} passed")

    if failed:
        print(f"\nFailed questions: {', '.join(failed)}")
        print(
            "\nIf questions still return 'employer', re-check _SYSTEM_PROMPT in"
            " analytics/query_engine/intent.py — the TIE-BREAKER section may need"
            " stronger wording or additional few-shot examples."
        )
        return 1

    print("\nAll geographic questions correctly classified. Issue #257 fix verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
