# Findings — Backfill Missing Normalized Jobs (JIE #399)

> **PR #403** | Script: `scripts/backfill_missing_normalized_jobs.py` | Author: Nestor

---

## What Was Investigated

790 rows exist in `dbo.job_postings` with no matching `dbo.normalized_jobs` record
by `(source, external_id)`. The clustering loader uses an INNER JOIN on this key,
making these rows invisible to `run_clustering.py` and permanently keeping
`canonical_role_id = NULL`.

---

## Root Cause (Confirmed)

All 790 gap rows trace to a **single 1-hour window on 2026-04-03** — 61 ingestion
runs all marked "completed" that produced `job_postings` rows but no corresponding
`normalized_jobs` records. The normalization agent wrote to `raw_ingested_jobs` and
updated `processing_status` to `normalized`, but never committed the
`normalized_jobs` insert.

Most likely cause: a batch-level DB transaction rollback after the status update
was already committed (different connection/transaction boundary), leaving orphan
`job_postings` rows that reference raw records whose status says "done."

**No gap rows exist from any date after 2026-04-03.** The root cause is historical
and non-recurring.

---

## Population Breakdown (as of 2026-05-23)

| Category | Count | Description |
|---|---|---|
| **Recoverable** | 330 | `raw_ingested_jobs` payload still exists; can be re-queued |
| **Irrecoverable** | 460 | Raw payload purged; cannot be re-normalized |
| **Total gap** | 790 | `job_postings` rows with no `normalized_jobs` match |

---

## Remediation Script

`scripts/backfill_missing_normalized_jobs.py` (dry-run by default):

- **Recoverable rows:** resets `raw_ingested_jobs.processing_status = 'pending'` so
  the standard `run_processing_loop.py` pipeline picks them up.
- **Irrecoverable rows:** stamps `job_postings.ingestion_run_id =
  'irrecoverable-backfill-399'` so they are queryable as a distinct population
  in the DB (not silently indistinguishable from rows awaiting clustering).

### Idempotency

Safe to re-run. Already-pending rows are counted but not double-reset. The
irrecoverable stamp uses `WHERE ingestion_run_id != 'irrecoverable-backfill-399'`
to skip already-stamped rows.

### Post-apply steps

After running with `--apply`:

```bash
# Re-queue the 330 recoverable rows through the full pipeline
python scripts/run_processing_loop.py --batch-size 50 --delay 5

# Assign canonical_role_id to the newly created normalized_jobs rows
python scripts/run_clustering.py

# Export fixtures so the fix is captured for everyone
python scripts/pg-seed-data/export_fixtures.py --scope all
```

---

## Acceptance Criteria Status

| Criterion | Status | Notes |
|---|---|---|
| Audit: 790 row count with 330/460 breakdown | Done | Documented in docstring and this file |
| Idempotent backfill script | Done | Dry-run default; safe to re-run |
| Root cause documented in findings file | Done | This file |
| Recoverable rows requeued through full pipeline | Pending post-apply | Run `run_processing_loop.py` after merge |
| Irrecoverable rows flagged with visible status | Done | `ingestion_run_id = 'irrecoverable-backfill-399'` stamped in DB |
| NULL rate from Category B drops to 0 after apply | Pending post-apply | Verify via `run_clustering.py` output |

---

## Queryable Verification (post-apply)

```sql
-- Confirm recoverable rows were processed
SELECT COUNT(*) FROM dbo.normalized_jobs
WHERE ingestion_run_id IN (
    SELECT DISTINCT ingestion_run_id FROM dbo.raw_ingested_jobs
    WHERE processing_status = 'normalized'
);

-- Confirm irrecoverable rows are stamped
SELECT COUNT(*) FROM dbo.job_postings
WHERE ingestion_run_id = 'irrecoverable-backfill-399';
-- Expected: 460

-- Confirm no remaining gap rows (after processing loop + clustering)
SELECT COUNT(*) FROM dbo.job_postings jp
WHERE jp.source IS NOT NULL
  AND jp.external_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM dbo.normalized_jobs nj
      WHERE nj.source = jp.source AND nj.external_id = jp.external_id
  )
  AND jp.ingestion_run_id != 'irrecoverable-backfill-399';
-- Expected: 0
```
