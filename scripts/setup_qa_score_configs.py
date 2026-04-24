"""Register QA eval score configs on the Langfuse project (JIE #247 + Week 9).

One-liner: Langfuse Cloud disables score-config auto-creation at the org
level; this registers the Q&A eval score configs (automated + manual + run-level) so
manual scoring is unblocked in the UI.

Run once per Langfuse instance (local or cloud). Idempotent: skips configs
whose name already exists. Safe to re-run after a new Langfuse instance is
provisioned or after a factory reset.

Full context
------------
The Langfuse scoring tutorial (Stage 3) claims "first score submission
registers the config." That is true on default/local Langfuse, but **not**
on our shared cloud instance, where score-config auto-creation is
disabled at the org level. The practical consequence:

1. ``eval/qa_eval.py`` submits the 5 automated scores per trace via
   ``dataset.run_experiment(evaluators=...)``. Those scores **do** land
   on traces (you can see them in the run's per-trace Scores panel and
   in the aggregate averages). They just don't register a Score Config.
2. The UI's **Settings → Scores** page and every trace's **"Add score"**
   dropdown read from the Score Config registry, not from raw submitted
   scores. Empty registry → empty dropdown → manual scoring
   (correctness / decision_relevance / followup_quality) is blocked.
3. This script closes that gap by explicitly creating all required configs
   (automated per-trace, run-level where applicable, plus 3 manual) as NUMERIC ranged 0.0-1.0, with
   rubric-aware descriptions pulled from ``eval/qa_scoring.py`` and the
   Week 9 scoring tutorial (Stage 5).

Treat this as a one-shot setup step, on par with
``scripts/upload_qa_dataset.py``. After running it, manual scoring in the
Langfuse UI works as the tutorial describes.

Score config inventory (all NUMERIC, range 0.0-1.0):

Automated per-trace (scored by ``eval/qa_scoring.py``):
    * ``intent_accuracy``  — classifier routing, binary (0.0 / 1.0)
    * ``evidence_citation`` — rubric-gated evidence quality
    * ``confidence_flags`` — calibration of low-confidence flag
    * ``latency_sla``      — continuous decay, 45s SLA
    * ``answerability``    — JIE #247; data-backed rows (excluded on infra)
    * ``correct_refusal``  — JIE #269; intent-only refuse vs commit

Run-level (from ``qa_eval`` run evaluators, when applicable):
    * ``refusal_correctness_rate`` — mean of ``correct_refusal`` over the intent-only cohort
    * ``mean_*`` for core metrics

Manual layer (3 — scored in the Langfuse UI per
``docs/Week 9/reading-langfuse-scoring-tutorial.md`` Stage 5):
    * ``correctness``        — hallucination / partial / correct
    * ``decision_relevance`` — actionability for the wfd_archetype
    * ``followup_quality``   — quality of follow-up questions

Usage
-----
::

    python scripts/setup_qa_score_configs.py           # create missing configs
    python scripts/setup_qa_score_configs.py --dry-run # print what would be created

Exit code 0 = success (created or already-present). Non-zero = a config
create call raised; check the Langfuse env vars
(``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_HOST``) in
``.env`` and confirm the keys belong to the project you intend to target
(compare the ``Host`` log line against the URL in your browser).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

from langfuse import Langfuse  # noqa: E402
from langfuse.api.commons.types.score_config_data_type import ScoreConfigDataType  # noqa: E402

# Single source of truth — matches:
#   * eval/qa_scoring.py (per-trace + run-level names used by qa_eval)
#   * docs/Week 9/reading-langfuse-scoring-tutorial.md Stage 5 (manual layer, 3 scores)
CONFIGS: tuple[dict[str, str | float], ...] = (
    {
        "name": "evidence_citation",
        "description": (
            "Automated. Rubric + overlap for committed answers; for refusals, must_include/must_not "
            "on the text (JIE #260). Data-backed + refuse uses a strong ~0.35 rubric scale."
        ),
    },
    {
        "name": "confidence_flags",
        "description": (
            "Automated. 1.0 if confidence-flag calibration is correct "
            "(low-confidence responses include an explanation and flag, "
            "high-confidence responses do not over-flag), else 0.0."
        ),
    },
    {
        "name": "intent_accuracy",
        "description": (
            "Automated. 1.0 if classified intent == expected (normalized); 0.0 otherwise "
            "(JIE #261: binary; related-intent pairs appear in confusion_rows only)."
        ),
    },
    {
        "name": "latency_sla",
        "description": (
            "Automated. Continuous decay curve: min(1.0, sla / latency_seconds), "
            "sla default 45s (overridable via QA_EVAL_LATENCY_SLA_SECONDS). "
            "See DEV-001 in eval/qa_prompt_iteration_log.md."
        ),
    },
    {
        "name": "answerability",
        "description": (
            "Automated (JIE #247 + JIE #269). For data-backed expected intents: "
            "1.0/0.0 on row count per harness rules; Null if not data-backed, "
            "or excluded (not gradable) on infrastructure failure / missing response."
        ),
    },
    {
        "name": "correct_refusal",
        "description": (
            "Automated (JIE #269). For intent-only (non–data-backed) expected intents: "
            "1.0/0.0 on appropriate refuse vs commit. Null (N/A) for data-backed intents "
            "or when the pipeline did not return a response."
        ),
    },
    {
        "name": "refusal_correctness_rate",
        "description": (
            "Run-level. Mean of per-item correct_refusal over the intent-only scored cohort "
            "(JIE #269). See comment on the run evaluation for n_scored / n_excluded."
        ),
    },
    {
        "name": "correctness",
        "description": (
            "Manual. 0.0 hallucinated / 0.5 partial / 1.0 correct. Honest "
            "refusals on no-data-in-scope score 1.0 (correct behavior). "
            "See docs/Week 9/reading-langfuse-scoring-tutorial.md Stage 5."
        ),
    },
    {
        "name": "decision_relevance",
        "description": (
            "Manual. 0.0 no help / 0.5 somewhat / 1.0 actionable for the "
            "wfd_archetype (workforce development director). Regionally anchored "
            "to the Borderplex."
        ),
    },
    {
        "name": "followup_quality",
        "description": (
            "Manual. 0.0 generic or missing / 0.5 somewhat useful / 1.0 "
            "deepens exploration with targeted, on-topic follow-up questions."
        ),
    },
)


def _existing(lf: Langfuse) -> dict[str, str]:
    """Return {name: id} of currently registered score configs, paginating."""
    out: dict[str, str] = {}
    page = 1
    while True:
        resp = lf.api.score_configs.get(page=page, limit=100)
        for c in resp.data:
            out[c.name] = c.id
        meta = getattr(resp, "meta", None)
        total_pages = getattr(meta, "total_pages", 1) if meta else 1
        if page >= total_pages:
            break
        page += 1
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created; do not mutate Langfuse state.",
    )
    args = parser.parse_args()

    lf = Langfuse()

    import os

    host = os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL") or "<unset>"
    print(f"Host: {host}")
    print()

    print("== Current Score Configs ==")
    existing = _existing(lf)
    for name, cid in sorted(existing.items()):
        print(f"  [ok]    {name:22s}  id={cid}")
    if not existing:
        print("  (none)")
    print()

    to_create = [c for c in CONFIGS if c["name"] not in existing]
    skipped = [c for c in CONFIGS if c["name"] in existing]

    print(f"== Plan: create {len(to_create)}, skip {len(skipped)} ==")
    for c in to_create:
        print(f"  [create] {c['name']:22s}  NUMERIC 0.0-1.0")
    for c in skipped:
        print(f"  [skip]   {c['name']:22s}  (already registered)")
    print()

    if args.dry_run:
        print("Dry-run: no changes applied.")
        return 0

    if not to_create:
        print("Nothing to do — all score configs in CONFIGS are already registered.")
        return 0

    print("== Creating ==")
    for c in to_create:
        result = lf.api.score_configs.create(
            name=str(c["name"]),
            data_type=ScoreConfigDataType.NUMERIC,
            min_value=0.0,
            max_value=1.0,
            description=str(c["description"]),
        )
        print(f"  [created] {result.name:22s}  id={result.id}")

    print()
    print(f"Done. {len(to_create)} config(s) created; {len(skipped)} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
