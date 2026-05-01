#!/usr/bin/env python3
"""Merge scoring_criteria for gq-021..gq-030 into the golden questions form file.

Loads eval/qa_golden_questions.json (source of truth for which trend questions exist)
and tools/golden-questions-form/golden_questions.json. For each gq-02X in range,
sets scoring_criteria from eval when non-empty, otherwise from the built-in templates.

Usage:
  python scripts/merge_golden_trend_scoring_criteria.py
  python scripts/merge_golden_trend_scoring_criteria.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_PATH = REPO_ROOT / "eval" / "qa_golden_questions.json"
FORM_PATH = REPO_ROOT / "tools" / "golden-questions-form" / "golden_questions.json"

TREND_IDS = [f"gq-{i:03d}" for i in range(21, 31)]

# Templates for gq-021 .. gq-030 (used when eval has no non-empty scoring_criteria).
DEFAULT_SCORING_CRITERIA: dict[str, str] = {
    "gq-021": (
        "Accuracy: Identifies correct skill categories? "
        "Completeness: Includes posting counts from multiple weeks? "
        "Relevance: Answers the question about skill growth?"
    ),
    "gq-022": (
        "Accuracy: Top skills ranked correctly by posting count? "
        "Completeness: Shows posting counts and trend direction? "
        "Relevance: Answers what skills are most in-demand?"
    ),
    "gq-023": (
        "Accuracy: Growth percentages calculated correctly? "
        "Completeness: Shows both absolute and percentage growth? "
        "Relevance: Identifies fastest-growing skills?"
    ),
    "gq-024": (
        "Accuracy: Employer counts are correct? "
        "Completeness: Shows breadth across employers? "
        "Relevance: Identifies broadly-sought skills?"
    ),
    "gq-025": (
        "Accuracy: Temporal trend is correct? "
        "Completeness: Shows multiple weeks of data? "
        "Relevance: Demonstrates consistent demand?"
    ),
    "gq-026": (
        "Accuracy: Data skills ranked correctly? "
        "Completeness: Includes posting/employer counts? "
        "Relevance: Answers data skills demand?"
    ),
    "gq-027": (
        "Accuracy: AI/ML skills identified correctly? "
        "Completeness: Shows growth trends? "
        "Relevance: Demonstrates AI demand surge?"
    ),
    "gq-028": (
        "Accuracy: Infrastructure skills ranked correctly? "
        "Completeness: Shows employer adoption? "
        "Relevance: Answers infrastructure demand?"
    ),
    "gq-029": (
        "Accuracy: Soft skills identified and ranked? "
        "Completeness: Shows employer breadth? "
        "Relevance: Demonstrates soft skill value?"
    ),
    "gq-030": (
        "Accuracy: API skills ranked correctly? "
        "Completeness: Compared to other tech skills? "
        "Relevance: Answers API skills demand?"
    ),
}


def _index_by_id(records: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in records:
        qid = row.get("id")
        if isinstance(qid, str):
            out[qid] = row
    return out


def _pick_scoring_criteria(eval_row: dict | None, qid: str) -> tuple[str, str]:
    """Returns (criteria, source_label) where source is 'eval' or 'template'."""
    if eval_row:
        raw = eval_row.get("scoring_criteria")
        if isinstance(raw, str) and raw.strip():
            return raw.strip(), "eval"
    return DEFAULT_SCORING_CRITERIA[qid], "template"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned updates without writing the form file.",
    )
    args = parser.parse_args()

    eval_records = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    form_records = json.loads(FORM_PATH.read_text(encoding="utf-8"))

    if not isinstance(eval_records, list) or not isinstance(form_records, list):
        print("error: expected both JSON files to be a list of objects", file=sys.stderr)
        return 1

    eval_by_id = _index_by_id(eval_records)
    form_by_id = _index_by_id(form_records)

    missing_in_eval = [qid for qid in TREND_IDS if qid not in eval_by_id]
    missing_in_form = [qid for qid in TREND_IDS if qid not in form_by_id]

    if missing_in_eval:
        print("warning: IDs missing from eval file:", ", ".join(missing_in_eval), file=sys.stderr)
    if missing_in_form:
        print("error: IDs missing from form file:", ", ".join(missing_in_form), file=sys.stderr)
        return 1

    updates: list[tuple[str, str, str | None, str]] = []
    for qid in TREND_IDS:
        row = form_by_id[qid]
        criteria, source = _pick_scoring_criteria(eval_by_id.get(qid), qid)
        old = row.get("scoring_criteria")
        old_norm = old if isinstance(old, str) else None
        if old_norm != criteria:
            updates.append((qid, criteria, old_norm, source))
            if not args.dry_run:
                row["scoring_criteria"] = criteria

    if args.dry_run:
        print("Dry run — no file written.")
    else:
        FORM_PATH.write_text(
            json.dumps(form_records, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {FORM_PATH.relative_to(REPO_ROOT)}")

    if not updates:
        print("No changes needed (scoring_criteria already matched).")
        return 0

    print("Updated questions:")
    for qid, criteria, old, source in updates:
        print(f"  {qid} (source={source})")
        if old:
            print(f"    replaced previous: {old[:80]}{'...' if len(old) > 80 else ''}")
        print(f"    {criteria}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
