#!/usr/bin/env python3
"""Week 8 smoke test — run one Q&A end-to-end through the guardrailed pipeline.

Drives :func:`analytics.query_engine.routing.run_guardrailed_analytics_query` so
every leg of the Week 8 Q&A pipeline fires once: intent classification -> SQL
generation -> SQL guardrails -> ``execute_safe`` -> evidence bundle ->
synthesis -> follow-up generation -> audit log write.

Usage (from any shell, any CWD):

    python scripts/smoke/qa_pipeline.py
    python scripts/smoke/qa_pipeline.py --question "Which regions pay the most for data analysts?"
    python scripts/smoke/qa_pipeline.py --correlation-id wk8-demo-1

Requires ``PYTHON_DATABASE_URL``, ``LLM_DEFAULT`` (Haiku-class), and
``LLM_SYNTHESIS`` (Sonnet-class) in the repo-root ``.env``. If the Week 7
analytics aggregates are empty the response will carry ``refused=True`` with a
NO_DATA refusal message and synthesis will be skipped (cost_breakdown only
contains ``intent_classification``).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

# Ensure repo root is on sys.path so analytics.* / common.* imports resolve
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from analytics.query_engine.routing import run_guardrailed_analytics_query  # noqa: E402
from common.data_store.database import session_scope  # noqa: E402
from common.types.query_request import QueryRequest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--question",
        default="What are the top 5 skills by posting count across all weeks?",
        help="Natural-language Q&A input (default is a canonical smoke question).",
    )
    parser.add_argument(
        "--correlation-id",
        default=f"qa-smoke-{uuid.uuid4().hex[:8]}",
        help="Correlation id threaded through traces + orchestration_audit_log.",
    )
    parser.add_argument(
        "--answer-preview-chars",
        type=int,
        default=200,
        help="Truncate the printed answer to N chars (default 200).",
    )
    args = parser.parse_args()

    req = QueryRequest(query=args.question)
    with session_scope() as session:
        resp = run_guardrailed_analytics_query(
            req,
            session=session,
            correlation_id=args.correlation_id,
        )

    print(f"question:        {args.question}")
    print(f"correlation_id:  {args.correlation_id}")
    print()
    if resp.refused:
        print(f"refused:         True")
        print(f"refusal_message: {resp.refusal_message}")
    else:
        print(f"answer:          {resp.answer_text[:args.answer_preview_chars]}")
    print(f"citations:       {len(resp.citations)}")
    print(f"confidence:      {resp.confidence:.3f}  (flagged_low={resp.confidence_flagged_low})")
    print(f"volume_low:      {resp.volume_flagged_low}")
    print(f"follow_ups:      {len(resp.follow_up_questions)}")
    for i, q in enumerate(resp.follow_up_questions[:3], 1):
        print(f"  {i}. {q}")
    print(f"total_cost_usd:  ${resp.total_cost_usd:.6f}")
    print("cost_breakdown_usd:")
    for leg, cost in resp.cost_breakdown_usd.items():
        print(f"  {leg:25} ${cost:.6f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
