"""One-off investigation script for issue #363 — canonical_role_id NULL rate."""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).parent.parent / ".env")
eng = create_engine(os.environ["PYTHON_DATABASE_URL"])


def run():
    with eng.connect() as c:
        # Sample 50 NULL rows
        print("=== 12. SAMPLE OF 50 NULL CANONICAL_ROLE_ID ROWS ===")
        rows = c.execute(
            text("""
        WITH has_nj AS (
            SELECT jp.job_posting_id, nj.id AS nj_id
            FROM dbo.job_postings jp
            JOIN dbo.normalized_jobs nj
                ON jp.source IS NOT NULL AND jp.external_id IS NOT NULL
                AND nj.source = jp.source AND nj.external_id = jp.external_id
        ),
        has_good_ei AS (
            SELECT hnj.job_posting_id
            FROM has_nj hnj
            JOIN (
                SELECT DISTINCT ON (normalized_job_id)
                    normalized_job_id, COALESCE(extraction_failed, false) AS extraction_failed
                FROM dbo.extracted_intelligence WHERE normalized_job_id IS NOT NULL
                ORDER BY normalized_job_id, extracted_at DESC NULLS LAST, id DESC
            ) ei ON ei.normalized_job_id = hnj.nj_id AND ei.extraction_failed = false
        ),
        loader_eligible AS (
            SELECT jp.job_posting_id
            FROM dbo.job_postings jp
            JOIN has_nj ON has_nj.job_posting_id = jp.job_posting_id
            JOIN has_good_ei ON has_good_ei.job_posting_id = jp.job_posting_id
            WHERE jp.company_id IS NOT NULL
              AND COALESCE(jp.is_duplicate, false) = false
              AND (jp.is_spam IS NOT TRUE)
              AND (jp.spam_score IS NULL OR jp.spam_score < 0.9)
        )
        SELECT
            jp.job_title,
            COALESCE(jp.role_classification, '(null)') AS role_classification,
            jp.source,
            CASE WHEN hn.job_posting_id IS NOT NULL THEN 'yes' ELSE 'no' END AS has_nj,
            CASE WHEN he.job_posting_id IS NOT NULL THEN 'yes' ELSE 'no' END AS has_ei,
            CASE WHEN le.job_posting_id IS NOT NULL THEN 'ELIGIBLE' ELSE 'EXCLUDED' END AS loader_status
        FROM dbo.job_postings jp
        LEFT JOIN has_nj hn ON hn.job_posting_id = jp.job_posting_id
        LEFT JOIN has_good_ei he ON he.job_posting_id = jp.job_posting_id
        LEFT JOIN loader_eligible le ON le.job_posting_id = jp.job_posting_id
        WHERE jp.canonical_role_id IS NULL
        ORDER BY le.job_posting_id NULLS LAST, jp.job_title
        LIMIT 50
        """)
        ).fetchall()
        header = f"  {'TITLE':<55} {'ROLE_CLASS':<30} {'SRC':<12} {'NJ':<4} {'EI':<4} STATUS"
        print(header)
        print("  " + "-" * 120)
        for r in rows:
            title = (r[0] or "")[:54]
            rc = (r[1] or "")[:29]
            src = (r[2] or "")[:11]
            print(f"  {title:<55} {rc:<30} {src:<12} {r[3]:<4} {r[4]:<4} {r[5]}")

        # 13. canonical_roles table: how many roles exist, posting coverage
        print("\n=== 13. CANONICAL_ROLES TABLE STATS ===")
        r = c.execute(
            text("""
            SELECT COUNT(*) AS role_count,
                   SUM(posting_count) AS total_assigned_via_roles
            FROM dbo.canonical_roles
        """)
        ).fetchone()
        print(f"  canonical_roles rows: {r[0]}, sum(posting_count): {r[1]}")

        print("\n=== 13b. TOP 10 CANONICAL ROLES BY POSTING COUNT ===")
        rows = c.execute(
            text("""
            SELECT label, posting_count
            FROM dbo.canonical_roles
            ORDER BY posting_count DESC
            LIMIT 10
        """)
        ).fetchall()
        for r in rows:
            print(f"  [{r[1]:>4}]  {r[0]}")

        # 14. Check if write-once: are there loader-eligible NULL rows that have
        # had a canonical_role_id previously set (i.e., was wiped to NULL)?
        # We can't know this without audit log, but we can check llm_audit_log for clustering runs
        print("\n=== 14. CLUSTERING RUN HISTORY (llm_audit_log) ===")
        rows = c.execute(
            text("""
            SELECT
                DATE(created_at) AS run_date,
                COUNT(*) AS calls,
                SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successes
            FROM dbo.llm_audit_log
            WHERE agent_name ILIKE '%cluster%'
            GROUP BY DATE(created_at)
            ORDER BY run_date DESC
            LIMIT 10
        """)
        ).fetchall()
        if rows:
            for r in rows:
                print(f"  {r[0]}: calls={r[1]}, successes={r[2]}")
        else:
            print("  No clustering entries in llm_audit_log")

        # 15. Check if canonical_role_id rerun-aware: loader has no date filter by default
        # Check if loader-eligible rows were simply not present when clustering last ran
        # by looking at ingestion_run timestamps
        print("\n=== 15. INGESTION TIMELINE FOR LOADER-ELIGIBLE NULL ROWS ===")
        r = c.execute(
            text("""
            WITH loader_eligible AS (
                SELECT jp.job_posting_id, jp.ingestion_run_id
                FROM dbo.job_postings jp
                INNER JOIN dbo.normalized_jobs nj
                    ON jp.source IS NOT NULL AND jp.external_id IS NOT NULL
                    AND nj.source = jp.source AND nj.external_id = jp.external_id
                INNER JOIN (
                    SELECT DISTINCT ON (normalized_job_id)
                        normalized_job_id, COALESCE(extraction_failed, false) AS extraction_failed
                    FROM dbo.extracted_intelligence WHERE normalized_job_id IS NOT NULL
                    ORDER BY normalized_job_id, extracted_at DESC NULLS LAST, id DESC
                ) ei ON ei.normalized_job_id = nj.id AND ei.extraction_failed = false
                WHERE jp.canonical_role_id IS NULL
                  AND jp.company_id IS NOT NULL
                  AND COALESCE(jp.is_duplicate, false) = false
                  AND (jp.is_spam IS NOT TRUE)
                  AND (jp.spam_score IS NULL OR jp.spam_score < 0.9)
            )
            SELECT
                COALESCE(ingestion_run_id, '(null)') AS ingestion_run_id,
                COUNT(*) AS count
            FROM loader_eligible
            GROUP BY ingestion_run_id
            ORDER BY count DESC
            LIMIT 15
        """)
        ).fetchall()
        print(f"  {'INGESTION_RUN_ID':<40} COUNT")
        for r in rows:
            print(f"  {str(r[0]):<40} {r[1]}")


if __name__ == "__main__":
    run()
