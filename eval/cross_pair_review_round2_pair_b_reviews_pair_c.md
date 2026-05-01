# Pair B (Fatima + Nestor) Reviews Pair C v2.1

**Source:** `eval/runs/findingsv2.1.md` (post-seed QA run `qa-v2.1-post-seed-fix`, Langfuse run linked therein). **PR #293** is treated as the findings carrier for geographic/comparison scorecard work; **PR #297** clustering plan — reviewed at architecture level only (no branch checkout in this session).

**Final review disclaimer:** All scorecard metrics and numeric callouts in this memo are tied to **`findingsv2.1.md` (2026-04-27)** — not a fresh golden eval at merge/push time; re-run LaborPulse QA after major pipeline merges for current deltas.

---

## PR #293 Findings Review

### Question cohort: geographic + comparison (worst evidence_citation vs v1)

Pair C’s v2.1 headline: **evidence_citation 0.45 vs v1 0.69** with **answerability 0.34** recovered after seed — the residual gap is **scorer strictness + thin rows**, not empty tables.

#### Trace issue (aggregate)

- **Trace issue:** After aggregates populated (#291), refusals dropped but many answers still sit at **1–3 evidence rows**; v2 penalizes sparse grounding harder than v1. **Intent/SQL** paths are largely healthy; the pain is **synthesis density vs row count** and golden expectations on niche Borderplex queries.

#### Comment 1

For a geographic golden that returns **exactly one** `geo_demand_weekly` row, does the trace show the synthesis model **enumerating** the single sub-region and week_start, or does it paraphrase into generic “demand remains active” language? If the latter, is the failure in **prompt** (no requirement to quote the bucket) or **scorer** (expects numeric repetition the model rightly avoids)?

#### Comment 2

When comparison intent hits **sector_summary_weekly** with **0 rows** after role/sector filters, does the router log `router_row_count` vs post–issue-197 filter stripping? Pair B’s red-team **RT-403** shows `comparison` + **0 rows** + refusal — good — but geographic comparisons with two metros need the same diagnosis to separate **data sparsity** from **mis-filter**.

#### Comment 3

**correct_refusal 0.25 on intent-only (10/40):** Which ten items scored “correct,” and do they share a pattern (e.g. all `other` unrouted)? If the other thirty are **false negatives** on the rubric, should Pair C re-label goldens vs change the scorer? Asking for a **confusion matrix** intent × `refused` × `correct_refusal` would settle whether this is a metrics artifact.

---

### Question pattern: disruption ↔ role_evolution misroutes (from v2 findings lineage)

v2.1 doc points back to v2’s disruption confusion; worth spot-checking on worst Langfuse traces.

#### Trace issue

- **Trace issue:** **Intent** — `disruption` vs `role_evolution` vs `comparison` boundary on “AI changes hiring mix” phrasing.

#### Comment 1

On the lowest **intent_accuracy** disruption golden, what **extracted_entities** (`role_names`, `time_references`) did the classifier emit, and did the router use **disruption_fingerprints** vs **canonical_roles**? If the trace shows `role_evolution` + `canonical_roles` with **0 rows**, is that a **vocabulary mismatch** (cluster `label` vs user phrase) per Week 9 docs rather than a classifier bug?

#### Comment 2

Could a **single** additional disruption few-shot (already proposed in v2 findings) fix multiple failures, or do failures split evenly between **intent** and **empty role slice**? That determines whether Pair B’s intent iteration or Pair C’s clustering/labels work should own the next PR.

---

## PR #297 Clustering Plan

Pair C’s clustering outputs (`canonical_roles`, `role_snapshot_weekly`, `job_postings.canonical_role_id`) are **upstream dependencies** for `role_evolution`, curriculum, and parts of trend-style narratives in `QueryRouter`. The plan is **aligned** with router tables_used in `analytics/query_engine/router.py` as long as cluster **labels** stay human-phrase-friendly (synonym map or broader match on 0 rows — see Week 9 findings). **Feasibility:** HDBSCAN + embeddings is already in-repo; risk is **label drift** and **minimum posting** gates producing empty reads for valid user phrases — mitigate with role hints (JIE #298) and golden re-labels, not router bypass.

---

## PR #293 — review comments to paste on GitHub (substantive)

1. **Evidence thin rows:** “On items where `row_count_returned` ≤ 3, does the Langfuse trace show synthesis citing each row’s key dimensions (week_start, subregion, skill_label), or does the model generalize? If generalize, should the scorer treat ‘directionally correct + sparse’ as partial credit?”

2. **Answerability recovery:** “v2.1 confirms seed regression hypothesis — great. For the remaining **0.66** gap to v1 evidence_citation, can you attach **three** traces where v2 scores `<0.5` but v1 scored `≥0.7` so we can see if it’s synthesis wording vs missing `distinct_posting_count`?”

3. **correct_refusal calibration:** “Can you export the ten `correct_refusal=1` item IDs and a five-sample of the `0` rows with classifier intent + `refused` flag? That tells Pair B whether prompt iteration should target refusals or answers.”
