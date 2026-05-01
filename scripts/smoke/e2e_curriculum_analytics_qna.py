# ruff: noqa: T201
"""End-to-end smoke: three Borderplex curriculum questions through curriculum Q&A path.

Loads repo-root ``.env``, verifies DB connectivity, runs each training-program question
through the same flow as ``run_analytics_qna`` for ``intent == curriculum`` (classify →
entitlement check → ``build_curriculum_inputs`` → ``synthesize_curriculum_outline`` →
API-shaped response), printing structured fields for findings capture.

Usage (from repo root, venv active)::

    python scripts/smoke/e2e_curriculum_analytics_qna.py

Requires ``PYTHON_DATABASE_URL`` and working LLM env (``LLM_DEFAULT``, ``LLM_SYNTHESIS``, etc.)
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

import analytics.query_engine.routing as _qe_routing  # noqa: E402
from analytics.query_engine.curriculum_path import CurriculumInputs, build_curriculum_inputs  # noqa: E402
from analytics.query_engine.curriculum_synthesis import synthesize_curriculum_outline  # noqa: E402
from analytics.query_engine.intent import classify_workforce_question  # noqa: E402
from analytics.tenant_scope import check_region_entitled, get_tenant_access_for_pipeline  # noqa: E402
from common.data_store.database import check_db_connection_detail, session_scope  # noqa: E402

CURRICULUM_QUESTIONS: tuple[str, ...] = (
    "What should a training program for AI-enabled software developers look like "
    "given what Borderplex employers are hiring for right now?",
    "What should a training program for data analysts look like "
    "given what Borderplex employers are hiring for right now?",
    "What should a training program for cybersecurity analysts look like "
    "given what Borderplex employers are hiring for right now?",
)


def _role_names_from_classification(classification: dict) -> list[str] | None:
    ent = classification.get("extracted_entities")
    if not isinstance(ent, dict):
        return None
    rn = ent.get("role_names")
    if not isinstance(rn, list):
        return None
    out = [str(x) for x in rn if x]
    return out or None


def _verbatim_vocab(ins: CurriculumInputs) -> set[str]:
    out: set[str] = set()
    for row in ins.top_skills:
        lab = row.get("skill_label")
        if lab:
            out.add(str(lab).strip().lower())
    for row in ins.rising_skills:
        lab = row.get("skill_label")
        if lab:
            out.add(str(lab).strip().lower())
    for row in ins.co_occurring:
        t = row.get("tool")
        if t:
            out.add(str(t).strip().lower())
        r = row.get("responsibility")
        if r:
            out.add(str(r).strip().lower()[:300])
    for row in ins.top_employers:
        cn = row.get("company_name")
        if cn:
            out.add(str(cn).strip().lower())
    return out


def _module_title_verified(title: str, vocab: set[str]) -> bool:
    t = title.strip().lower()
    if not t:
        return True
    for a in vocab:
        if len(a) < 2:
            continue
        if t == a or t in a or a in t:
            return True
    return False


def unverified_module_titles(modules: list[str], ins: CurriculumInputs) -> list[str]:
    """Module titles with no substring overlap against grounded curriculum input strings."""
    vocab = _verbatim_vocab(ins)
    return [m for m in modules if m and not _module_title_verified(m, vocab)]


def _print_question_block(
    idx: int,
    question: str,
    *,
    api,
    syn: dict,
    ins: CurriculumInputs,
) -> None:
    print("=" * 72)
    print(f"Question {idx}")
    print("=" * 72)
    print()
    print(question)
    print()
    print("--- full markdown answer ---")
    print(api.answer)
    print()
    print("--- metrics ---")
    print(f"confidence: {api.confidence}")
    print(f"row_count_returned: {api.row_count_returned}")
    print(f"modules: {syn.get('modules')!r}")
    print(f"follow_up_questions: {api.follow_up_questions!r}")
    print(f"is_sufficient: {syn.get('is_sufficient')!r}")
    print()
    print("--- evidence (row counts = supporting_count) ---")
    for i, ev in enumerate(api.evidence, 1):
        sc = ev.supporting_count
        sc_s = "n/a" if sc is None else str(sc)
        print(f"  [{i}] title={ev.title!r} supporting_count={sc_s} source={ev.source!r}")
        if ev.time_period:
            print(f"      time_period={ev.time_period!r}")
        if ev.snippet:
            snip = ev.snippet.replace("\n", " ")
            if len(snip) > 240:
                snip = snip[:237] + "..."
            print(f"      snippet: {snip}")
    print()


def run_curriculum_smoke(session, question: str, correlation_id: str):
    """Mirror ``run_analytics_qna`` curriculum branch (single synthesis call)."""
    taccess = get_tenant_access_for_pipeline(None)
    classification = classify_workforce_question(question, correlation_id=correlation_id)
    intent_label = str(classification.get("intent") or "other")
    conf = float(classification.get("confidence") or 0.0)

    if intent_label in _qe_routing._ISSUE197_INTENTS:
        classification = {
            **classification,
            "issue197_sql_guard_hint": _qe_routing._ISSUE197_SQL_GUARD_HINT,
        }

    ent = classification.get("extracted_entities")
    if isinstance(ent, dict):
        check_region_entitled(taccess, question, ent)
    else:
        check_region_entitled(taccess, question, {})

    if intent_label != "curriculum":
        raise RuntimeError(
            f"Expected intent curriculum for smoke question; got {intent_label!r}. Check classifier / question wording."
        )

    role_names_for_ci: list[str] | None = None
    if isinstance(classification.get("extracted_entities"), dict):
        rn = classification["extracted_entities"].get("role_names")
        if isinstance(rn, list):
            role_names_for_ci = [str(x) for x in rn if x]

    ins = build_curriculum_inputs(session, question, role_names=role_names_for_ci)
    syn = synthesize_curriculum_outline(ins, correlation_id=correlation_id)
    api = _qe_routing._curriculum_to_analytics_response(
        ins=ins,
        synthesis=syn,
        intent_label=intent_label,
        classification_confidence=conf,
    )
    return api, syn, ins


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

    base = f"smoke-curriculum-e2e-{uuid.uuid4().hex[:8]}"
    run_rows: list[tuple[bool, float, list[str]]] = []

    with session_scope() as session:
        for i, q in enumerate(CURRICULUM_QUESTIONS, start=1):
            cid = f"{base}-q{i}"
            api, syn, ins = run_curriculum_smoke(session, q, cid)
            mods = list(syn.get("modules") or [])
            unv = unverified_module_titles(mods, ins)
            run_rows.append((bool(syn.get("is_sufficient")), float(api.confidence), unv))
            _print_question_block(i, q, api=api, syn=syn, ins=ins)

    n_suff = sum(1 for suff, _, __ in run_rows if suff)
    n_conf = sum(1 for _, conf, __ in run_rows if conf >= 0.85)
    all_unverified = [x for _, __, unv in run_rows for x in unv]
    uniq_bad = sorted({x for x in all_unverified if x})

    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"Questions run: {len(CURRICULUM_QUESTIONS)}")
    print(f"Questions with is_sufficient = True: {n_suff}")
    print(f"Questions with confidence >= 0.85: {n_conf}")
    print(
        "Modules with unverified skill names (heuristic vs curriculum inputs): "
        + (", ".join(uniq_bad) if uniq_bad else "none found")
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
