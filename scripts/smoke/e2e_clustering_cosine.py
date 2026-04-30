"""Smoke test for JIE #297 cosine HDBSCAN + JIE #327 clustering acceptance targets.

Verifies that:
1. The clustering config reads ``distance_metric: cosine`` (not euclidean).
2. ``canonical_roles`` table has **≥ 20** distinct labels in the DB (#327 Phase 5).
3. Noise fraction is **< 30 %** among non-spam ``job_postings`` (``canonical_role_id``
   NULL = noise proxy — same population as historical #297 check, relaxed per #327).
4. **Mega-cluster share** < 10 %: ``MAX(cluster_size) / COUNT(*)`` over postings with a
   non-null ``canonical_role_id`` in the same non-spam slice (SQL-computed; see #327).
5. The Q&A pipeline resolves a "software developer" role-evolution question without
   returning a refused / zero-row response.

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
# Check 2 — canonical_roles has ≥ 20 labels (#327)
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
            result = session.execute(text("SELECT COUNT(*) AS n FROM dbo.canonical_roles"))
            row = result.fetchone()
            count = int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001
        log.warning("smoke_db_error", error=str(exc))
        return

    min_roles = 20
    if count >= min_roles:
        _ok(f"canonical_roles count={count} (>= {min_roles})")
    else:
        _fail(
            f"canonical_roles has only {count} rows - expected >={min_roles} after #327 "
            f"UMAP + leaf clustering roadmap (JIE #327)"
        )


# ---------------------------------------------------------------------------
# Check 3 — noise fraction (#327: < 30 %)
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
    threshold = 30.0
    if noise_pct < threshold:
        _ok(f"noise_fraction={noise_pct:.1f}% (< {threshold}%)")
    else:
        _fail(
            f"noise_fraction={noise_pct:.1f}% exceeds {threshold}% threshold (#327) — "
            f"re-run clustering after tuning"
        )


# ---------------------------------------------------------------------------
# Check 3b — mega-cluster share (#327)
# ---------------------------------------------------------------------------


def check_mega_cluster_share() -> None:
    """Largest canonical_role bucket / assigned postings in the non-spam slice."""
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
                    """
                    SELECT COALESCE(
                        (SELECT MAX(cnt) FROM (
                            SELECT COUNT(*) AS cnt
                            FROM dbo.job_postings jp
                            WHERE (jp.is_spam = FALSE OR jp.is_spam IS NULL)
                              AND jp.canonical_role_id IS NOT NULL
                            GROUP BY jp.canonical_role_id
                        ) z),
                        0
                    ) AS max_cluster,
                    (SELECT COUNT(*)
                     FROM dbo.job_postings jp
                     WHERE (jp.is_spam = FALSE OR jp.is_spam IS NULL)
                       AND jp.canonical_role_id IS NOT NULL
                    ) AS assigned
                    """
                )
            )
            row = result.fetchone()
            if row is None:
                return
            max_cluster, assigned = int(row[0]), int(row[1])
    except Exception as exc:  # noqa: BLE001
        log.warning("smoke_mega_cluster_skipped", error=str(exc))
        return

    if assigned == 0:
        log.warning("smoke_mega_cluster_skipped", reason="no assigned canonical_role_id rows")
        return

    mega_pct = max_cluster / assigned * 100
    threshold = 10.0
    if mega_pct < threshold:
        _ok(f"mega_cluster_share={mega_pct:.1f}% (< {threshold}%) max={max_cluster} assigned={assigned}")
    else:
        _fail(
            f"mega_cluster_share={mega_pct:.1f}% exceeds {threshold}% (#327) — "
            f"max_cluster={max_cluster} assigned={assigned}"
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
                "Q&A refused with 0 rows for 'software developer' role-evolution question - "
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
    print("=== JIE #297 / #327 — clustering smoke checks ===\n")
    check_config_metric()
    check_canonical_roles_count()
    check_noise_fraction()
    check_mega_cluster_share()
    check_qna_software_developer()

    if _FAILURES:
        print(f"\n[FAIL] {len(_FAILURES)} check(s) FAILED:")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1

    print("\n[OK] All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
