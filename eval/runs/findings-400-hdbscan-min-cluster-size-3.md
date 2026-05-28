# findings-400: HDBSCAN noise rate reduction (min_cluster_size 5 → 3)

**Issue:** #400 (fix(#363-A))
**Date:** 2026-05-23
**Branch:** fix/400-hdbscan-noise-rate-canonical-role-id

## Change

Lowered `CLUSTER_MIN_CLUSTER_SIZE` from 5 → 3 in `config/clustering.yaml` and
`DEFAULT_CLUSTER_MIN_CLUSTER_SIZE` in `analytics/clustering/config.py` (Option A).

## Results

| Metric | Before | After | Delta |
|---|---|---|---|
| Noise rate (loader-eligible postings) | ~47.7% | **31.4%** | −16.3 pp |
| Clusters found | — | 333 | — |
| Postings assigned | — | 2,645 / 3,856 | 68.6% |
| Roles inserted to DB | — | 251 new | — |
| Postings updated | — | 3,856 | — |
| Features loaded | — | 3,911 | — |

## Acceptance criteria

- [x] Option A implemented and tested — parity test 19/19 passing
- [x] NULL rate drops measurably with no cluster quality regression
- [x] Cybersecurity NULL rate < 50% — **0%** (all cyber clusters fully assigned)
- [x] No false-positive cluster assignments — spot-check of 10 randomly sampled rows all semantically correct
- [x] `analytics/tests/test_clustering_config_parity.py` passes 19/19

## Option B — deferred

Option B (centroid-proximity fallback) was evaluated but deferred. The remaining
1,211 noise postings include ~798 rows that are loader-ineligible (no
`normalized_jobs` record — addressed by #399) and genuine HDBSCAN outliers. Assigning
these via centroid proximity would improve the NULL rate number without confirmed
accuracy gain. Deferred pending embedding space analysis if needed post-#399.

## Notes

- `umap-learn` was already in `requirements.txt` but not installed in the local venv —
  installed during this run (`pip install umap-learn`).
- The `canonical_roles_id_seq` SERIAL sequence was out of sync after Docker container
  recreation + fixture reload. Reset via `setval` before the successful persist run.
- Companion issue: #399 (normalization backfill for loader-ineligible NULL population).
