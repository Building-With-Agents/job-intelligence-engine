# ruff: noqa: T201
"""End-to-end smoke: canonical curriculum question through ``run_analytics_qna``.

Loads repo-root ``.env``, verifies DB connectivity, runs the Borderplex
curriculum training-program question, prints API fields, then runs
``build_curriculum_inputs`` + ``synthesize_curriculum_outline`` again to surface
``is_sufficient`` and ``modules`` (a second synthesis LLM call; same pattern as
inspecting synthesis dict outside ``AnalyticsQueryResponse``).

Usage (from repo root, venv active)::

    python scripts/smoke/e2e_curriculum_analytics_qna.py

Requires ``PYTHON_DATABASE_URL`` and working LLM env (``LLM_DEFAULT``, etc.)
for intent classification and curriculum synthesis.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from analytics.query_engine.curriculum_path import build_curriculum_inputs  # noqa: E402
from analytics.query_engine.curriculum_synthesis import synthesize_curriculum_outline  # noqa: E402
from analytics.query_engine.intent import classify_workforce_question  # noqa: E402
from analytics.query_engine.routing import run_analytics_qna  # noqa: E402
from common.data_store.database import check_db_connection_detail, session_scope  # noqa: E402

_CANONICAL_QUESTION = (
    "What should a training program for AI-enabled software developers look like "
    "given what Borderplex employers are hiring for right now?"
)


def _role_names_from_classification(classification: dict) -> list[str] | None:
    ent = classification.get("extracted_entities")
    if not isinstance(ent, dict):
        return None
    rn = ent.get("role_names")
    if not isinstance(rn, list):
        return None
    return [str(x) for x in rn if x]


def main() -> int:
    ok, err = check_db_connection_detail()
    if not ok:
        print("Database is not available — cannot run curriculum Q&A.", file=sys.stderr)
        if err:
            print(f"Connection error: {err}", file=sys.stderr)
        print(
            "Set PYTHON_DATABASE_URL in the repo-root .env, e.g. postgresql+psycopg2://user:pass@host:port/dbname",
            file=sys.stderr,
        )
        return 1

    cid = f"smoke-curriculum-e2e-{uuid.uuid4().hex[:12]}"

    with session_scope() as session:
        api = run_analytics_qna(session, _CANONICAL_QUESTION, cid)

        print("=" * 72)
        print("run_analytics_qna")
        print("=" * 72)
        print()
        print("--- answer (markdown) ---")
        print(api.answer)
        print()
        print("--- fields ---")
        print(f"confidence: {api.confidence}")
        print(f"row_count_returned: {api.row_count_returned}")
        print(f"follow_up_questions: {api.follow_up_questions!r}")
        print(f"classified_intent: {api.classified_intent!r}")
        print()
        print("--- evidence ---")
        for i, ev in enumerate(api.evidence, 1):
            sc = ev.supporting_count
            sc_s = "n/a" if sc is None else str(sc)
            print(f"  [{i}] title={ev.title!r} supporting_count={sc_s}")
            print(f"      source={ev.source!r}")
            if ev.snippet:
                snip = ev.snippet.replace("\n", " ")
                if len(snip) > 200:
                    snip = snip[:197] + "..."
                print(f"      snippet: {snip}")

        print()
        print("=" * 72)
        print("build_curriculum_inputs + synthesize_curriculum_outline (second pass)")
        print("=" * 72)
        print()

        classification = classify_workforce_question(_CANONICAL_QUESTION, correlation_id=cid)
        ins = build_curriculum_inputs(
            session, _CANONICAL_QUESTION, role_names=_role_names_from_classification(classification)
        )
        syn = synthesize_curriculum_outline(ins, correlation_id=cid)

        print(f"is_sufficient: {syn.get('is_sufficient')!r}")
        print(f"modules: {syn.get('modules')!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
