#!/usr/bin/env python3
"""Week 8 smoke test — grounding retry path / hallucination guard (Pairs B + C).

Runs a question designed to stress the evidence grounding path through
:func:`analytics.query_engine.routing.run_guardrailed_analytics_query`.  When
the evidence is thin, the synthesis uses a two-prompt retry and the response
carries ``confidence_flagged_low`` or ``volume_flagged_low`` flags.

Usage (from any shell, any CWD):

    python scripts/smoke/qa_grounding_check.py
    python scripts/smoke/qa_grounding_check.py --question "What is the average salary for nurses in Las Cruces?"
    python scripts/smoke/qa_grounding_check.py --correlation-id wk8-grounding-demo

Requires ``PYTHON_DATABASE_URL``, ``LLM_DEFAULT`` (Haiku-class), and
``LLM_SYNTHESIS`` (Sonnet-class) in the repo-root ``.env``.
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
    parser = argparse.ArgumentParser(
        description="Run a Q&A question that stresses the grounding retry path.",
    )
    parser.add_argument(
        "--question",
        default="What is the median salary for data engineers in El Paso this quarter?",
        help="Question designed to stretch evidence (default probes salary data).",
    )
    parser.add_argument(
        "--correlation-id",
        default=f"qa-grounding-{uuid.uuid4().hex[:8]}",
        help="Correlation id threaded through traces + orchestration_audit_log.",
    )
    args = parser.parse_args()

    req = QueryRequest(query=args.question)
    with session_scope() as s:
        resp = run_guardrailed_analytics_query(
            req,
            session=s,
            correlation_id=args.correlation_id,
        )

    print(f"answer_len:        {len(resp.answer_text)}")
    print(f"refused:           {resp.refused}  msg: {resp.refusal_message}")
    print(f"confidence:        {resp.confidence}  flagged_low: {resp.confidence_flagged_low}")
    print(f"volume_flagged_low: {resp.volume_flagged_low}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
