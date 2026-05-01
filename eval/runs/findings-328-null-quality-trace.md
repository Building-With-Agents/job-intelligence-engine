# JIE #328 — NULL `quality_score` vs NULL `date_posted` (root cause)

**Refs:** #328  
**Date:** 2026-04-30

## Empirical correlation (from issue)

- ~460 `job_postings` rows with `quality_score IS NULL`.
- 100% `source = jsearch`; 100% also have `date_posted IS NULL` on `job_postings`.
- `score_quality()` does **not** use `date_posted`; the correlation is **not** causal from the scorer.

## Actual root cause

`apply_enrichment_to_job_postings()` in `enrichment/job_postings_promotion.py` **returned early** when `record_enriched_payload["quality_score"]` was missing (`None`), logging `enrichment_promotion_skipped_no_quality_score` and **never running** the `UPDATE dbo.job_postings` that persists enrichment columns.

Typical sequence for affected rows:

1. `job_postings` row exists (e.g. inserted on first promotion path) with default **NULL** `quality_score`.
2. A later promotion call arrives with a payload that omits `quality_score` (e.g. partial `RecordEnriched` / sweeper-style payload, or historical agent bug).
3. Promotion is skipped entirely → `quality_score` stays NULL forever.
4. `date_posted` is often NULL for the same JSearch cohort because JSearch sometimes omits `job_posted_at_datetime_utc` and normalization leaves `normalized_jobs.date_posted` NULL — **independent** of the quality gate, but **co-occurring** in the live profile.

## Fix (code)

When the payload omits `quality_score`, **derive** it inside the promotion layer by loading `normalized_jobs` + latest `extracted_intelligence` for the `normalized_job_id` and calling `score_quality()` (same deterministic path as the batch agent). Then proceed with the existing UPDATE + dedup flow.

## Backfill

See `scripts/backfill_null_quality_job_postings.py` and `eval/runs/findings-null-quality-backfill.md` for the one-shot repair of existing NULL rows.
