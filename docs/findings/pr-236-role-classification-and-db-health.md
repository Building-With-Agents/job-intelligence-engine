# [DRAFT PR #236] Fix `role_classification` sector fallback (#197) + local DB health checks

Mirror of the GitHub draft PR description for offline editing. Source of truth: https://github.com/Building-With-Agents/job-intelligence-engine/pull/236

---

## Summary

This PR addresses **Issue #197**: the sector fallback in `classify_role()` can label real IT jobs as **`N/A Not an IT role`** because that row exists in `industry_sectors` and wins token overlap. It also **promotes and expands** the local DB health checker so we catch bad `role_classification` distributions and pipeline/schema issues before baselines and demos.

**Status:** Work in progress — classification changes and expanded verifier to land in follow-up commits on this branch.

---

## Problem

After PR #195’s deterministic backfill (~3,496 `dbo.job_postings`), **`N/A Not an IT role`** became the **largest** `role_classification` bucket (~657 rows, ~19%), ahead of real categories (Software Engineering, Mobile, etc.). Examples mis-bucketed: Junior Back-End Developer, AWS Solution Architect, SRE, Cyber Security Analyst, Front-End Web Designer.

**Root cause:** In `enrichment/classification.py`, `classify_role()` returns **`best_sec`** when `tech_score < min_score` but `sec_score >= min_score`. The reference sector **"N/A Not an IT role"** can still match generic corpus tokens and beat **`unclassified`**, which is misleading.

---

## What we will ship

### 1. Classification (code)

- **Filter** meta / negative / placeholder sectors from the industry candidate list before scoring (e.g. titles starting with `n/a`, `not an`, `not classified`, `unknown` — case-insensitive, stripped).
- **Tighten sector fallback** (preferred in combination): e.g. require `sec_score >= min_score * 1.5` (or a named constant) so weak sector matches fall through to **`unclassified`**.
- **Single helper** used anywhere `classify_role` receives `industry_sectors` (runtime enrichment + `scripts/backfill_qna_columns.py`).

### 2. Data repair (docs / optional one-liner)

- Document: backfill only fills **NULL**s; fixing existing bad labels requires e.g.  
  `UPDATE dbo.job_postings SET role_classification = NULL WHERE role_classification = 'N/A Not an IT role'`  
  then `python scripts/backfill_qna_columns.py` (with `--columns role_classification` if supported). Dry-run first.
- **No** `docker compose down -v` required.

### 3. DB health checker (promote + expand)

- **Rename / promote** `scripts/scratch_verify_local_db_health.py` → `scripts/verify_local_db_health.py` (implemented).
- **One command, no CLI flags:** `python scripts/verify_local_db_health.py` runs **all** checks; tuning via **constants** at top of file only.
- **Exit code:** `0` unless any **ERROR**; **WARN** does not fail.

**Checks (full list per run):**

| Area | Checks |
|------|--------|
| **Existing** | Core table floors; 8 Q&A columns present + non-null floors; `job_ingestion_runs.id` uuid vs integer; `orchestration_audit_log.event_type` |
| **`role_classification`** | Distribution; **WARN** if `N/A Not an IT role` is top bucket or above a fraction of classified rows; optional WARN if `unclassified` is too high |
| **Referential** | `extracted_intelligence.normalized_job_id` orphans; `ingestion_run_id` not in `job_ingestion_runs.run_id`; dangling `employer_profile_id` |
| **Counts** | `normalized_jobs` vs `extracted_intelligence`; `job_postings` vs fixture metadata; `raw_ingested_jobs` vs `normalized_jobs` gaps |
| **Duplicates** | `(source, external_id)` on `job_postings`; `raw_payload_hash` smoke on `raw_ingested_jobs` |
| **Q&A quality** | `seniority_level` dominance; `date_posted` NULL/future/concentration; **`salary_min > salary_max`** (ERROR or WARN); `is_remote` coverage |
| **Schema** | Extensions (`vector`, `uuid-ossp` if needed); Q&A indexes on `job_postings`; spot-check column types (`dedup_embedding` / vector) |
| **Analytics** | `canonical_roles`, `disruption_fingerprints`, `sector_summary_weekly` empty-table WARN; light `orchestration_audit_log` optional |
| **Ops** | `version()`, `current_database()`; large table size WARN |

**Out of scope for v1:** long-running transactions / lock monitoring.

### 4. Tests

- **Unit tests** for `classify_role`: mock sectors include **`N/A Not an IT role`**; assert it does not win when a sensible tech/sector label should; sample titles from the bug report where feasible.
- Follow `.cursor/rules/testing-standards.mdc`.

### 5. Follow-up

- Re-verify Pair B/C golden questions that **group by `role_classification`** after repair.

---

## Current branch state (for reviewers)

- [x] Local DB health script: `scripts/verify_local_db_health.py` (promoted from scratch; expanded checks).
- [ ] `classify_role` filtering + stricter sector threshold
- [ ] Shared helper + backfill path wired
- [ ] Operator notes (NULL-then-backfill)
- [ ] Promoted + expanded `verify_local_db_health.py`; remove `scratch_` prefix
- [ ] Unit tests

---

## How to verify locally (after implementation)

1. `python scripts/backfill_qna_columns.py --dry-run` (then live after NULL reset if repairing).
2. `python scripts/verify_local_db_health.py` — expect **no ERROR**; review **WARN** lines.
3. Spot-check SQL: `SELECT role_classification, COUNT(*) FROM dbo.job_postings GROUP BY 1 ORDER BY 2 DESC`.

---

Closes #197 (when complete).
