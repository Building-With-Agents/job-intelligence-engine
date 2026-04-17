#!/usr/bin/env python3
"""Week 8 smoke test — intent classification in isolation (Pairs B + C).

Calls :func:`analytics.query_engine.intent.classify_workforce_question` with a
single question and prints the structured result as JSON.  This verifies the
Haiku-tier intent classifier independently of SQL generation and synthesis.

Usage (from any shell, any CWD):

    python scripts/smoke/qa_intent_only.py
    python scripts/smoke/qa_intent_only.py --question "What employers hire the most data analysts?"
    python scripts/smoke/qa_intent_only.py --correlation-id wk8-intent-demo

Requires ``LLM_DEFAULT`` (Haiku-class / ``chat-gpt41mini``) in the repo-root
``.env``.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

# Ensure repo root is on sys.path so analytics.* / common.* imports resolve
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from analytics.query_engine.intent import classify_workforce_question  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run intent classification on a single workforce question.",
    )
    parser.add_argument(
        "--question",
        default="Which AI and machine learning skills are growing fastest in El Paso over the last 90 days?",
        help="Natural-language question to classify (default is a canonical smoke question).",
    )
    parser.add_argument(
        "--correlation-id",
        default=f"qa-intent-{uuid.uuid4().hex[:8]}",
        help="Correlation id threaded through traces + orchestration_audit_log.",
    )
    args = parser.parse_args()

    r = classify_workforce_question(
        args.question,
        correlation_id=args.correlation_id,
    )
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
