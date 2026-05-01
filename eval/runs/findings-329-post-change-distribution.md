# Post-change `quality_score` distribution — #329

**Refs:** #329

## Expected qualitative shift (re-score offline or SQL histogram)

- **Before (#329 issue text):** heavy mass ~0.45–0.55; empirical max ~0.75; AI floor at 0.38 compressed non-AI postings.
- **After:** AI-neutral postings gain ~0.05–0.12 combined score mass; tail toward **0.9+** reopens for rich AI postings; **std dev should exceed ~0.15** on a large sample once re-scored.

## Offline re-score

Re-score is **cheap** (no LLM): iterate `job_postings` with existing descriptions + join latest `extracted_intelligence`, call `score_quality`, optional bulk `UPDATE`. The committed **backfill** script covers NULLs only; a full corpus re-score can extend the same pattern in a follow-up ops script if product wants DB-level relabeling immediately.

## Clustering Phase 4

Set `clustering.cluster_input_min_quality_score` in `config/clustering.yaml` **above 0.0** only after:

1. NULL `quality_score` rows are cleared (#328).
2. This distribution note is validated against a fresh `SELECT` histogram on prod/staging.

Recommended first non-zero trial: **0.48** (adjust ±0.05 based on eligible posting count vs #179 minimum-data guard).
