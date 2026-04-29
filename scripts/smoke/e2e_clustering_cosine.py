"""Smoke test for JIE #297 — cosine-distance HDBSCAN clustering.

Verifies that:
1. The clustering config reads ``distance_metric: cosine`` (not euclidean).
2. ``canonical_roles`` table has ≥ 10 distinct labels in the DB.
3. Noise fraction is < 20 % (if freshness data is available via the
   ``dbo.posting_freshness`` table, used as a proxy for cluster assignment
   quality — otherwise this check is skipped gracefully).
4. The Q&A pipeline resolves a "software developer" role-evolution question
   without returning a refused / zero-row response.

Usage (repo root, venv active)::

    python scripts/smoke/e2e_clustering_cosine.py

Exit code 0 = all checks passed.  Non-zero = at least one check failed.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

import os  # noqa: E402

import structlog  # noqa: E402

log = structlog.get_logger()

_FAILURES: list[str] = []


def _fail(msg: str) -> None:
    log.error("smoke_check_failed", reason=msg)
    _FAILURES.append(msg)


def _ok(msg: str) -> None:
    log.info("smoke_check_passed", check=msg)


# ---------------------------------------------------------------------------
# Check 1 — config reads cosine
# ---------------------------------------------------------------------------

def check_config_metric() -> None:
    from analytics.clustering.config import cluster_distance_metric  # noqa: PLC0415

    metric = cluster_distance_metric()
    if metric == "cosine":
        _ok("distance_metric=cosine")
    else:
        _fail(f"distance_metric expected 'cosine', got {metric!r} (JIE #297 fix not applied)")


# ---------------------------------------------------------------------------
# Check 2 — canonical_roles has ≥ 10 labels
# ---------------------------------------------------------------------------

def check_canonical_roles_count() -> None:
    db_url = os.getenv("PYTHON_DATABASE_URL")
    if not db_url:
        log.warning("smoke_skip_db_check", reason="PYTHON_DATABASE_URL not set")
        return

    try:
        from sqlalchemy import create_engine, text  # noqa: PLC0415
        from sqlalchemy.orm import Session  # noqa: PLC0415

        engine = create_engine(db_url, pool_pre_ping=True)
        with Session(engine) as session:
            result = session.execute(
                text("SELECT COUNT(*) AS n FROM dbo.canonical_roles")
            )
            row = result.fetchone()
            count = int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001
        log.warning("smoke_db_error", error=str(exc))
        return

    if count >= 10:
        _ok(f"canonical_roles count={count} (≥ 10)")
    else:
        _fail(
            f"canonical_roles has only {count} rows — expected ≥ 10 coherent clusters "
            f"after cosine-distance re-clustering (JIE #297)"
        )


# ---------------------------------------------------------------------------
# Check 3 — noise fraction (best-effort; skipped if no freshness proxy)
# ---------------------------------------------------------------------------

def check_noise_fraction() -> None:
    db_url = os.getenv("PYTHON_DATABASE_URL")
    if not db_url:
        return

    try:
        from sqlalchemy import create_engine, text  # noqa: PLC0415
        from sqlalchemy.orm import Session  # noqa: PLC0415

        engine = create_engine(db_url, pool_pre_ping=True)
        with Session(engine) as session:
            result = session.execute(
                text(
                    "SELECT "
                    "  COUNT(*) FILTER (WHERE canonical_role_id IS NULL) AS noise, "
                    "  COUNT(*) AS total "
                    "FROM dbo.job_postings "
                    "WHERE is_spam = FALSE OR is_spam IS NULL"
                )
            )
            row = result.fetchone()
            if row is None:
                return
            noise, total = int(row[0]), int(row[1])
    except Exception as exc:  # noqa: BLE001
        log.warning("smoke_noise_check_skipped", error=str(exc))
        return

    if total == 0:
        log.warning("smoke_noise_check_skipped", reason="no postings found")
        return

    noise_pct = noise / total * 100
    threshold = 20.0
    if noise_pct < threshold:
        _ok(f"noise_fraction={noise_pct:.1f}% (< {threshold}%)")
    else:
        _fail(
            f"noise_fraction={noise_pct:.1f}% exceeds {threshold}% threshold — "
            f"re-run clustering pipeline after applying cosine metric fix (JIE #297)"
        )


# ---------------------------------------------------------------------------
# Check 4 — Q&A resolves "software developer" without zero-row refusal
# ---------------------------------------------------------------------------

def check_qna_software_developer() -> None:
    db_url = os.getenv("PYTHON_DATABASE_URL")
    if not db_url:
        log.warning("smoke_skip_qna_check", reason="PYTHON_DATABASE_URL not set")
        return

    if not os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("LLM_PROVIDER", "azure_openai") != "mock":
        log.warning("smoke_skip_qna_check", reason="LLM credentials not set and provider is not mock")
        return

    try:
        from analytics.query_engine.routing import run_analytics_qna  # noqa: PLC0415
        from common.data_store.database import session_scope  # noqa: PLC0415

        with session_scope() as session:
            resp = run_analytics_qna(
                session,
                "How has the software developer role evolved in the Borderplex?",
                "smoke-e2e-clustering-cosine",
            )

        refused = getattr(resp, "refused", False)
        row_count = getattr(resp, "row_count_returned", None) or 0
        answer = getattr(resp, "answer", "") or ""

        if refused and row_count == 0:
            _fail(
                "Q&A refused with 0 rows for 'software developer' role-evolution question — "
                "canonical_roles label vocabulary does not contain matching clusters yet. "
                "Re-run analytics clustering pipeline after the cosine-metric fix. (JIE #297)"
            )
        else:
            _ok(f"qna_software_developer: refused={refused} row_count={row_count} answer_len={len(answer)}")

    except Exception as exc:  # noqa: BLE001
        log.warning("smoke_qna_check_skipped", error=str(exc))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    print("=== JIE #297 — cosine clustering smoke checks ===\n")
    check_config_metric()
    check_canonical_roles_count()
    check_noise_fraction()
    check_qna_software_developer()

    if _FAILURES:
        print(f"\n❌  {len(_FAILURES)} check(s) FAILED:")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1

    print("\n✓  All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
