## Summary

- Investigates why 47.7% of `dbo.job_postings.canonical_role_id` is NULL (2,251 / 4,716 rows) as reported in #363.
- Full findings report written to `eval/findings-canonical-role-id-null-investigation.md`.
- Supporting query script added at `scripts/investigate_363.py`.

## Root causes identified

| Category | Count | % of NULLs |
|----------|-------|------------|
| **H1 - HDBSCAN noise** (primary) | 1,430 | 63.5% |
| **H4 - No normalized_job row** (secondary) | 790 | 35.1% |
| Failed/missing extraction | 26 | 1.2% |
| Spam / dedup (correctly excluded) | ~28 | ~1.2% |

**H3 (write-once / stale cluster table) and source-bias are not confirmed** - the loader has no date filter and reruns re-evaluate all eligible rows; NULL rates are identical across jsearch and web_scrape.

## Next steps (recommended, separate issues)

1. **Normalization backfill** for 790 rows missing a `normalized_jobs` record (these silently fall off the clustering loader with no error signal).
2. **HDBSCAN tuning** - lower `CLUSTER_MIN_CLUSTER_SIZE` and/or add a centroid-proximity fallback for noise rows to address the 1,430 HDBSCAN-noise postings.

## Test plan

- [ ] Read `eval/findings-canonical-role-id-null-investigation.md` - verify root-cause breakdown sums correctly
- [ ] Run `python scripts/investigate_363.py` against the dev DB to confirm counts are reproducible

Closes #363
