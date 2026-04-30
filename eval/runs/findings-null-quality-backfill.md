# NULL `quality_score` backfill — procedure (#328)

**Refs:** #328

## Script

`python scripts/backfill_null_quality_job_postings.py --dry-run` — lists candidate row count (join `job_postings` ↔ `normalized_jobs`, `quality_score IS NULL`, non-spam, `company_id` present).

`python scripts/backfill_null_quality_job_postings.py` — updates `quality_score` only (deterministic `score_quality` via the same derivation path as promotion).

Optional: `--limit N` for staged rollout.

## Safety

- Does **not** touch `is_spam`, `spam_tier`, or dedup columns.
- Run against **admin / staging** first; record counts in `findings-328-verify-null-count.md`.
