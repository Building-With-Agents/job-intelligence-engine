"""Live demo benchmark — intent classification + query routing.

Runs 32 workforce questions through the full pipeline:

  classify_workforce_question()  →  Azure OpenAI (LLM_DEFAULT)
                                 →  writes to dbo.llm_audit_log
  QueryRouter().route()          →  reads from aggregate tables
                                 →  returns RouteResult

Prints one line per question, then a summary block, then the last 32 rows
from dbo.llm_audit_log to show exactly what was written to the database.

Usage:
    .venv\\Scripts\\Activate.ps1
    python scripts/demo_intent_router.py

Prerequisites:
    PYTHON_DATABASE_URL  — PostgreSQL connection string
    LLM_DEFAULT          — deployment name (e.g. chat-gpt41mini)
    AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from common.data_store.database import session_scope
from common.data_store.models import LLMAuditLog
from analytics.query_engine.intent import classify_workforce_question
from analytics.query_engine.router import QueryRouter
from sqlalchemy import select, desc

# ---------------------------------------------------------------------------
# 32 benchmark questions with expected intents
# (20 from test suite + 12 new edge-case entries)
# ---------------------------------------------------------------------------

BENCHMARK: list[tuple[str, str]] = [
    # --- trend (4) ---
    ("How fast is demand for registered nurses growing this quarter?", "trend"),
    ("Which cloud skills show the strongest week-over-week posting velocity?", "trend"),
    ("Is Python demand still accelerating or has growth flattened this year?", "trend"),
    ("Show me the top 5 fastest-growing tech skills in the last 30 days.", "trend"),
    # --- role_evolution (3) ---
    ("How has the software engineer job family changed in the past five years?", "role_evolution"),
    ("Are data analyst titles shifting toward analytics engineer in our postings?", "role_evolution"),
    ("What new responsibilities have appeared in product manager job descriptions lately?", "role_evolution"),
    # --- disruption (3) ---
    ("Which occupations face the highest automation risk from generative AI?", "disruption"),
    ("How is AI adoption affecting entry-level administrative roles?", "disruption"),
    ("Are clerical and data-entry roles declining faster than the regional average?", "disruption"),
    # --- emergence (3) ---
    ("What entirely new job titles appeared in cybersecurity last year?", "emergence"),
    ("Which novel combinations of skills signal an emerging hybrid role?", "emergence"),
    ("Are there new AI safety or prompt-engineering roles showing up in postings?", "emergence"),
    # --- curriculum (3) ---
    ("What micro-credentials should a community college add for semiconductor techs?", "curriculum"),
    ("How should we sequence modules for an AI literacy workforce program?", "curriculum"),
    ("Which certifications appear most frequently alongside cloud engineer job postings?", "curriculum"),
    # --- employer (3) ---
    ("Which employers are hiring the most welders in the border region?", "employer"),
    ("Do local hospitals signal stronger demand for LVNs than last year?", "employer"),
    ("What companies in El Paso are actively posting for cybersecurity analysts?", "employer"),
    # --- workflow (3) ---
    ("What does a typical day look like for a field service technician?", "workflow"),
    ("Which tools dominate daily work for DevOps engineers in our sample?", "workflow"),
    ("What programming languages and frameworks do ML engineers use most often?", "workflow"),
    # --- geographic (3) ---
    ("How does job volume in El Paso compare to Las Cruces this month?", "geographic"),
    ("Where are remote software jobs concentrated versus on-site roles?", "geographic"),
    ("Are there more healthcare postings in Ciudad Juarez or the regional average?", "geographic"),
    # --- comparison (3) ---
    ("Compare median salary for Python versus Java developers in our data.", "comparison"),
    ("Is demand for cybersecurity analysts higher than for network admins?", "comparison"),
    ("Which grew faster last quarter: cloud roles or data roles?", "comparison"),
    # --- other (4) ---
    ("What is the weather like today?", "other"),
    ("Hello — can you help?", "other"),
    ("Who is the CEO of Microsoft?", "other"),
    ("Can you write me a poem about workforce data?", "other"),
]

_AGENT_NAME = "analytics-intent-classification"
_SEP = "─" * 100


def _fmt_check(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _truncate(s: str, n: int = 65) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def run_benchmark() -> None:
    router = QueryRouter()
    total = len(BENCHMARK)

    print(f"\n{'=' * 100}")
    print(f"  JIE — Intent Classification + Query Routing Live Benchmark  ({total} questions)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  agent: {_AGENT_NAME}")
    print(f"{'=' * 100}\n")

    results: list[dict] = []
    run_start = time.perf_counter()

    for idx, (question, expected) in enumerate(BENCHMARK, start=1):
        q_start = time.perf_counter()

        # ── Step 1: classify (hits LLM + writes to llm_audit_log) ──────────
        classification = classify_workforce_question(question)
        actual_intent = classification["intent"]
        confidence = classification["confidence"]
        needs_clarification = classification.get("needs_clarification", False)

        # ── Step 2: route (reads from aggregate tables) ─────────────────────
        route_result = None
        tables_used: list[str] = []
        row_count = 0
        try:
            with session_scope() as session:
                route_result = router.route(classification, session)
                tables_used = route_result.tables_used
                row_count = route_result.row_count
        except Exception as exc:
            tables_used = [f"ERROR: {exc}"]

        elapsed_ms = (time.perf_counter() - q_start) * 1000
        correct = actual_intent == expected

        results.append(
            {
                "question": question,
                "expected": expected,
                "actual": actual_intent,
                "confidence": confidence,
                "correct": correct,
                "tables": tables_used,
                "row_count": row_count,
                "elapsed_ms": elapsed_ms,
                "needs_clarification": needs_clarification,
            }
        )

        # ── Per-question output ──────────────────────────────────────────────
        tables_str = ", ".join(tables_used) if tables_used else "(unrouted)"
        clarify_flag = " [?]" if needs_clarification else ""
        print(
            f"[{idx:02d}/{total}] {_fmt_check(correct):4s}  "
            f"expected={expected:<16} got={actual_intent:<16} "
            f"conf={confidence:.2f}{clarify_flag:<4}  "
            f"rows={row_count:3d}  {elapsed_ms:6.0f}ms  "
            f"tables=[{tables_str}]"
        )
        print(f"       {_truncate(question)}")
        print()

    total_elapsed_ms = (time.perf_counter() - run_start) * 1000
    correct_count = sum(1 for r in results if r["correct"])
    accuracy = correct_count / total
    avg_latency = total_elapsed_ms / total

    # ── Summary ─────────────────────────────────────────────────────────────
    print(_SEP)
    print(f"  SUMMARY")
    print(_SEP)
    print(f"  Questions  : {total}")
    print(f"  Correct    : {correct_count} / {total}")
    print(f"  Accuracy   : {accuracy:.1%}")
    print(f"  Total time : {total_elapsed_ms / 1000:.1f}s")
    print(f"  Avg/call   : {avg_latency:.0f}ms")

    # ── Failures (if any) ────────────────────────────────────────────────────
    failures = [r for r in results if not r["correct"]]
    if failures:
        print(f"\n  {len(failures)} misclassified:")
        for r in failures:
            print(f"    expected={r['expected']:<16} got={r['actual']:<16} conf={r['confidence']:.2f}  {_truncate(r['question'], 55)}")

    # ── llm_audit_log tail — "where we wrote to" ─────────────────────────────
    print(f"\n{_SEP}")
    print(f"  dbo.llm_audit_log  (last {total} rows for agent={_AGENT_NAME!r})")
    print(_SEP)

    try:
        with session_scope() as session:
            rows = session.execute(
                select(
                    LLMAuditLog.id,
                    LLMAuditLog.model,
                    LLMAuditLog.provider,
                    LLMAuditLog.latency_ms,
                    LLMAuditLog.input_tokens,
                    LLMAuditLog.output_tokens,
                    LLMAuditLog.cost_usd,
                    LLMAuditLog.success,
                    LLMAuditLog.created_at,
                )
                .where(LLMAuditLog.agent_name == _AGENT_NAME)
                .order_by(desc(LLMAuditLog.created_at))
                .limit(total)
            ).fetchall()

        total_cost = sum(float(r.cost_usd or 0) for r in rows)

        print(
            f"  {'id':>8}  {'model':<20}  {'provider':<14}  "
            f"{'lat_ms':>7}  {'in_tok':>6}  {'out_tok':>7}  "
            f"{'cost_usd':>10}  {'ok':>4}  created_at"
        )
        print(f"  {'-' * 95}")
        for r in rows:
            created = r.created_at.strftime("%H:%M:%S") if r.created_at else "—"
            print(
                f"  {r.id:>8}  {(r.model or '?'):<20}  {(r.provider or '?'):<14}  "
                f"{int(r.latency_ms or 0):>7}  {int(r.input_tokens or 0):>6}  "
                f"{int(r.output_tokens or 0):>7}  "
                f"${float(r.cost_usd or 0):>9.6f}  "
                f"{'yes':>4}  {created}"
            )
        print(f"  {'-' * 95}")
        print(f"  Total cost for this run: ${total_cost:.6f}")
        print(f"  Cost per classification: ${total_cost / max(len(rows), 1):.6f}")

    except Exception as exc:
        print(f"  [WARNING] Could not query llm_audit_log: {exc}")
        print(f"  (Set PYTHON_DATABASE_URL in .env to see the write log)")

    print(f"\n{'=' * 100}\n")

    # Exit non-zero if accuracy below threshold
    min_accuracy = 0.70
    if accuracy < min_accuracy:
        print(f"  [FAIL] Accuracy {accuracy:.1%} is below minimum {min_accuracy:.0%}")
        sys.exit(1)
    else:
        print(f"  [PASS] Accuracy {accuracy:.1%} >= {min_accuracy:.0%} target")


if __name__ == "__main__":
    run_benchmark()
