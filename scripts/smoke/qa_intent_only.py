#!/usr/bin/env python3
"""Week 8 smoke test — intent classification in isolation (Pairs B + C).

Calls :func:`analytics.query_engine.intent.classify_workforce_question` with a
single question and prints the structured result as JSON.  This verifies the
Haiku-tier intent classifier independently of SQL generation and synthesis.

Usage (from any shell, any CWD):

    python scripts/smoke/qa_intent_only.py
    python scripts/smoke/qa_intent_only.py --question "What employers hire the most data analysts?"
    python scripts/smoke/qa_intent_only.py --correlation-id wk8-intent-demo

JIE #359 — curriculum heuristic + role resolution diagnosis (gq-072 / gq-073):

    QA_EVAL_INTENT_HEURISTIC_LEVEL=3 python scripts/smoke/qa_intent_only.py

    # Same behavior without env (explicit flag):
    python scripts/smoke/qa_intent_only.py --issue359

When ``QA_EVAL_INTENT_HEURISTIC_LEVEL=3`` or ``--issue359`` is used, prints
``intent``, ``confidence``, ``extracted_entities``, ``role_names``, and (if
``PYTHON_DATABASE_URL`` is available) ``build_curriculum_inputs`` resolution:
``canonical_role``, ``canonical_role_label``, ``_extract_role_phrase`` input phrase.

Requires ``LLM_DEFAULT`` (Haiku-class / ``chat-gpt41mini``) in the repo-root
``.env`` for the generic single-question path. gq-072 matches the curriculum-generation
**heuristic** in ``intent.py`` (no LLM). gq-073 does not match that regex today
(``training program for`` vs ``cybersecurity training program with``) and falls through
to the LLM unless patterns change.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

# Ensure repo root is on sys.path so analytics.* / common.* imports resolve
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

import analytics.query_engine.intent as _intent_mod  # noqa: E402

classify_workforce_question = _intent_mod.classify_workforce_question

_ISSUE359_QUESTIONS: tuple[tuple[str, str], ...] = (
    (
        "gq-072",
        "What should a training program for AI agent developers using LangChain look like "
        "given what Borderplex employers are hiring for right now?",
    ),
    (
        "gq-073",
        "What should a cybersecurity training program with certifications look like given current Borderplex demand?",
    ),
)


def _run_issue359() -> int:
    level = os.getenv("QA_EVAL_INTENT_HEURISTIC_LEVEL", "")
    print(f"QA_EVAL_INTENT_HEURISTIC_LEVEL={level!r}\n")

    db_ok = False
    try:
        from common.data_store.database import check_db_connection_detail  # noqa: E402

        db_ok, db_err = check_db_connection_detail()
        if not db_ok:
            print(f"DB: unavailable ({db_err}) — skipping build_curriculum_inputs.\n")
    except Exception as exc:  # noqa: BLE001
        print(f"DB: check failed ({type(exc).__name__}: {exc}) — skipping build_curriculum_inputs.\n")

    for gq_id, question in _ISSUE359_QUESTIONS:
        print("=" * 72)
        print(f"{gq_id}")
        print(question)
        print("-" * 72)
        heuristic_hit = bool(_intent_mod._matches_curriculum_generation_shape(question))
        print("curriculum_generation_shape_heuristic (pre-LLM):", heuristic_hit)
        cid = f"{gq_id}-intent-smoke"
        r = classify_workforce_question(question, correlation_id=cid)
        ent = r.get("extracted_entities") or {}
        role_names = ent.get("role_names") if isinstance(ent, dict) else None
        print("intent:", r.get("intent"))
        print("confidence:", r.get("confidence"))
        print("extracted_entities:", json.dumps(ent, indent=2))
        print("role_names:", role_names)

        if db_ok:
            from analytics.query_engine.curriculum_path import build_curriculum_inputs  # noqa: E402
            from common.data_store.database import session_scope  # noqa: E402

            with session_scope() as session:
                rn_list = role_names if isinstance(role_names, list) else None
                ins = build_curriculum_inputs(session, question, role_names=rn_list)
            print(
                "curriculum_path (same role_names as classification): "
                f"canonical_role={ins.canonical_role!r} "
                f"canonical_role_label={ins.canonical_role_label!r} "
                f"role_matched={ins.role_matched}"
            )
        print()
    return 0


def main() -> int:
    heuristic_level = (os.getenv("QA_EVAL_INTENT_HEURISTIC_LEVEL") or "").strip()
    parser = argparse.ArgumentParser(
        description="Run intent classification on a single workforce question.",
    )
    parser.add_argument(
        "--issue359",
        action="store_true",
        help="Run gq-072 / gq-073 #359 diagnosis (same as QA_EVAL_INTENT_HEURISTIC_LEVEL=3).",
    )
    parser.add_argument(
        "--question",
        default=None,
        help="Natural-language question to classify (default: canonical smoke question).",
    )
    parser.add_argument(
        "--correlation-id",
        default=f"qa-intent-{uuid.uuid4().hex[:8]}",
        help="Correlation id threaded through traces + orchestration_audit_log.",
    )
    args = parser.parse_args()

    if args.issue359 or heuristic_level == "3":
        return _run_issue359()

    q = args.question or ("Which AI and machine learning skills are growing fastest in El Paso over the last 90 days?")

    r = classify_workforce_question(
        q,
        correlation_id=args.correlation_id,
    )
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
