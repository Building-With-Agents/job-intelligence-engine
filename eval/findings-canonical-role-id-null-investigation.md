# Investigation: `canonical_role_id` NULL Rate

**Issue:** [#363](https://github.com/Building-With-Agents/job-intelligence-engine/issues/363)  
**Investigator:** Nestor Escobedo  
**Date:** 2026-05-18  
**DB snapshot:** Gary's SoT (Azure PostgreSQL) — 4,716 rows in `dbo.job_postings`

---

## 1. Baseline

| Metric | Count | % |
|--------|-------|---|
| Total `job_postings` rows | 4,716 | 100% |
| `canonical_role_id IS NULL` | 2,251 | **47.7%** |
| `canonical_role_id IS NOT NULL` | 2,465 | 52.3% |
| Canonical roles in `dbo.canonical_roles` | 202 | — |

---

## 2. Root-Cause Breakdown

The 2,251 NULL rows decompose into three non-overlapping categories based on
the loader eligibility criteria defined in `analytics/canonical_roles/loader.py`:

| Category | Count | % of NULLs | Description |
|----------|-------|------------|-------------|
| **A — HDBSCAN noise** | 1,430 | 63.5% | Pass all loader filters; were seen by clustering; HDBSCAN classified as outliers (label = −1) |
| **B — No normalized_job** | 790 | 35.1% | No matching `normalized_jobs` row by `(source, external_id)` → never reached clustering |
| **C — Failed/missing extraction** | 26 | 1.2% | `extracted_intelligence.extraction_failed = true` or no EI row |
| **D — High spam score** | 4 | 0.2% | `spam_score >= 0.9` — correctly auto-rejected |
| **E — is_duplicate** | ~24 | ~1.1% | Dedup-flagged survivors excluded from clustering |

> **Note:** Categories D and E are _correctly_ excluded. Only A, B, and C represent
> rows that a healthy pipeline should classify.

---

## 3. Evidence by Investigation Question

### 3a. Source breakdown (question 4)

| Source | Total | NULL | NULL % |
|--------|-------|------|--------|
| `jsearch` | 4,714 | 2,250 | 47.7% |
| `web_scrape` | 2 | 1 | 50.0% |

**Finding:** No source bias. The NULL rate is essentially identical across sources.
H4 (ingestion-source bias) is **not confirmed**.

### 3b. Temporal distribution (question 3)

99.5% of `job_postings` rows have `publish_date IS NULL`, making this dimension
uninformative. Among the 26 rows with dates (2026-03 and 2026-04), the NULL rate
is 29–33% — slightly below average, which may reflect more recently ingested jobs
being more likely to have dense neighborhood postings.

### 3c. Role classification cross-tab (question 5)

IT-classified roles show a wide but uniformly distributed NULL rate (31–68%):

| `role_classification` | Total | NULL | NULL % |
|-----------------------|-------|------|--------|
| Mobile App Development | 1,032 | 439 | 42.5% |
| IT Project Management | 656 | 280 | 42.7% |
| Software Engineering | 613 | 380 | **62.0%** |
| Network Administration | 412 | 180 | 43.7% |
| Artificial Intelligence | 307 | 140 | 45.6% |
| Machine Learning | 190 | 109 | **57.4%** |
| DevOps | 99 | 62 | **62.6%** |
| Cloud Computing | 38 | 26 | **68.4%** |
| Cybersecurity | 10 | 10 | **100%** |
| `unclassified` | 73 | 71 | **97.3%** |

Notable: `unclassified` and `Cybersecurity` have near-100% NULL rates, suggesting
these roles are systematically sparse in embedding space — no dense enough cluster
forms for HDBSCAN to capture them.

### 3d. Clustering pipeline trace (question 2)

The `llm_audit_log` confirms clustering ran on:
- 2026-04-16 (59 labeling calls)
- 2026-04-29 (95 calls)
- 2026-04-30 (869 calls)
- 2026-05-04 (2 calls)
- 2026-05-07 (2 calls)

The loader SQL contains **no date filter** by default — it loads all eligible rows
across the full corpus on each run. This confirms the pipeline is **rerun-aware**:
it re-evaluates all eligible postings every clustering run. H3 (stale cluster table /
write-once) is **not confirmed**.

The 1,430 loader-eligible NULL rows were seen by the pipeline and explicitly classified
as noise (HDBSCAN label = −1), not skipped. Noise rows receive `canonical_role_id = NULL`
via `analytics/canonical_roles/persist.py` (the noise branch in the `for a in result.assignments` loop).

### 3e. Sample of 50 NULL rows

All 50 sampled rows are loader-ELIGIBLE (have normalized_job + extracted_intelligence,
pass spam/dedup filters). Representative titles:

**Genuinely outside cluster density (HDBSCAN noise — H1):**
- `LLM Security Evaluation Expert` — too niche
- `Senior Preproduction Engineer: Front-End Design Lead` — unusual hybrid title
- `AI Integration Architect (Part-Time, Remote)` — sparse specialty

**Possible misclassified non-IT (should be excluded — H2):**
- `Retail Supply Chain Driver` (classified as Software Development)
- `Service Writer` (classified as Network Administration)
- `Inventory Manager` (classified as IT Project Management)
- `Production Manager` (classified as IT Project Management)

**Dedup gap — 16 identical `Cloud DevSecOps Engineer` postings:**
All 16 are loader-eligible and NULL. Despite identical titles they have different
`external_id` values (not caught by fingerprint dedup) and appear to form a
micro-cluster that still falls below HDBSCAN's density threshold in the UMAP-reduced
space.

---

## 4. Hypotheses Ranked by Evidence

| Rank | Hypothesis | Evidence | Confirmed? |
|------|-----------|----------|------------|
| 1 | **H1 — HDBSCAN noise** | 1,430 eligible rows explicitly labeled noise; `unclassified`/`Cybersecurity` at 97–100% NULL. UMAP+HDBSCAN requires minimum density; diverse/niche titles scatter in embedding space | **YES — primary cause** |
| 2 | **H4 (rephrased) — Normalization promotion gap** | 790 rows in `job_postings` have no matching `normalized_jobs` row by `(source, external_id)`. These were likely promoted via a direct path (early ingestion batches or manual seeds) that bypassed the standard normalization pipeline | **YES — secondary cause** |
| 3 | **H2 — Pipeline failure mode on specific title shapes** | 26 rows with failed/missing extraction; 16 identical "Cloud DevSecOps Engineer" titles suggest near-duplicate title flooding | **Partial — minor** |
| 4 | **H3 — Stale cluster table / write-once** | Loader has no date filter; reruns see all eligible rows | **NOT confirmed** |
| 5 | **H4 — Source bias** | NULL rate identical across jsearch and web_scrape | **NOT confirmed** |

---

## 5. What Good Looks Like (Gap to Target)

Target from issue #363: NULL rate ≤ 15%.

| Category | Current NULLs | Addressable? | Action |
|----------|--------------|--------------|--------|
| HDBSCAN noise (A) | 1,430 | Partially | Lower `CLUSTER_MIN_CLUSTER_SIZE`, add representative-title fallback matcher, or backfill via centroid cosine similarity |
| No normalized_job (B) | 790 | Yes | Investigate promotion path for these 790 rows; re-run normalization if source data is recoverable |
| Failed extraction (C) | 26 | Yes | Re-extract (requeue for normalization → extraction pipeline) |
| Spam/dedup (D/E) | ~28 | No — correct behavior | Keep excluded |

Addressing A + B alone reduces NULLs from 2,251 → ~31 — bringing the NULL rate to
**< 1%** if all 790 no-normalized-job rows can be backfilled and the noise threshold tuned.
Realistic target after addressing A+B: **< 20%** (allowing for genuinely niche/sparse titles).

---

## 6. Recommended Next Steps

1. **Backfill mechanism for Category B (790 rows):** Audit which ingestion batches
   promoted rows to `job_postings` without creating `normalized_jobs` entries.
   Determine if the original raw data is still in `raw_ingested_jobs` and requeue
   through the normalization → extraction → enrichment pipeline. Create a separate
   issue to track this (scope is medium).

2. **HDBSCAN noise reduction for Category A (1,430 rows):** Options ranked by
   implementation cost:
   - **(a) Lower `CLUSTER_MIN_CLUSTER_SIZE`** from 5 → 3 to capture smaller clusters
     (low cost, verify no cluster fragmentation)
   - **(b) Representative-title fallback:** After clustering, assign any still-NULL
     loader-eligible posting to the nearest cluster by centroid cosine similarity if
     similarity ≥ threshold (e.g. 0.75). This handles niche titles near an existing cluster.
   - **(c) Periodic re-cluster with NULL-aware reprocessing:** Already implemented
     (rerun-aware); tuning (a) or (b) above is the real lever.

3. **Non-IT role filter upstream:** The sample shows roles like "Retail Supply Chain
   Driver" and "Service Writer" passing the loader (enrichment didn't set `is_spam`
   high enough). Consider tightening the `role_classification` filter in the loader
   to exclude roles in clearly non-IT `role_classification` buckets.

4. **Address near-duplicate flooding:** The 16 identical "Cloud DevSecOps Engineer"
   postings with different `external_id`s suggest the near-dedup embedding step
   (issue #135) is not catching same-title postings from the same employer. This
   inflates cluster membership counts artificially.

---

## 7. Decision

**Primary fix to address first:** Category B (790 no-normalized-job rows) is the
highest-certainty win — these rows are silently excluded from clustering with no
pipeline failure signal. A targeted audit + re-normalization backfill job is the
recommended first PR.

**Follow-up:** HDBSCAN tuning (option 2a + 2b above) for Category A, tracked as
a separate issue due to the larger scope and clustering pipeline risk.
