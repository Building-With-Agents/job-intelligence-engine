# Week 9 — Data gaps inventory (working doc)

**Pairs:** Angel (Pair A — taxonomy / Q&A) · Fatima (Pair B — temporal / employer)  
**Related:** Issue #230 (inventory / later prioritization) · taxonomy audit #229

**Collaboration.**

- Pair A is drafted from Angel’s taxonomy audit (#229).
- Pair B is pending merge from Fatima’s temporal/employer audit.
- **Final top-3 prioritization** will be agreed jointly after both halves are present in this doc.

**Intro.** This file currently lists **all candidate data gaps** supported by evidence already written in the canonical findings doc. Per Gary’s direction, we **catalog first**; the **final “true top 3”** will be chosen later after Pair B content is merged and the team triages. **Do not treat this list as prioritized yet.**

**Canonical source for Pair A:** [WEEK09_REFRESH_AGGREGATES_FINDINGS.md](WEEK09_REFRESH_AGGREGATES_FINDINGS.md) — *Taxonomy Audit Addendum (Issue #229)* (Checks 1–3, *Q&A trace*, *Taxonomy gaps (carry-forward #230)*, and the addendum *Repo note*). Counts and outcomes below are **only** what that document already records.

---

## Pair A — Angel (taxonomy audit + Q&A trace)

### Gap A1

- **Table:** `dbo.role_snapshot_weekly`
- **Column / scope:** Whole table; `week_start` and snapshot rows (multi-week coverage check)
- **What is wrong:** Table is empty on the audited dev snapshot: `total_rows` = 0, `distinct_week_start` = 0, `min_week_start` / `max_week_start` = `None` / `None`; `GROUP BY week_start` returned `(no rows)`.
- **Effect on Q&A answers:** Any answer that needs **weekly** canonical-role demand, posting roll-ups, or salary-style bands keyed by `week_start` has **no** citeable rows from this table on the audited DB.
- **Classification:** `requires pipeline re-run`
- **Evidence source:** [WEEK09_REFRESH_AGGREGATES_FINDINGS.md](WEEK09_REFRESH_AGGREGATES_FINDINGS.md) — Taxonomy Audit Addendum, **Check 2**; **Taxonomy gaps (carry-forward #230), item 1**; addendum **Recommendation** (first bullet).

### Gap A2

- **Table:** `dbo.job_postings`
- **Column / scope:** `canonical_role_id` (non-null coverage and join to `dbo.canonical_roles`)
- **What is wrong:** Only **630** of **2,679** postings have non-null `canonical_role_id` (**23.52%** as printed); **2,049** rows are NULL by arithmetic on those same audited counts. On the non-null slice, join-validity counts are **630 / 630** (no mismatch on the pasted aggregate).
- **Effect on Q&A answers:** Queries that **INNER JOIN** on `canonical_role_id` exclude the NULL majority unless the narrative states the denominator; “all postings” vs “canonical-role–assigned postings” diverge.
- **Classification:** `requires pipeline re-run`
- **Evidence source:** [WEEK09_REFRESH_AGGREGATES_FINDINGS.md](WEEK09_REFRESH_AGGREGATES_FINDINGS.md) — Taxonomy Audit Addendum, **Check 3**; **Taxonomy gaps (carry-forward #230), item 2**; addendum **Recommendation** (second bullet).

### Gap A3

- **Table:** `dbo.canonical_roles`
- **Column / scope:** `label` vs `representative_titles` (top-volume cluster in the pasted sample)
- **What is wrong:** For `label` = “Site Reliability Engineer” (`posting_count` **542** in the paste), `representative_titles` include Machine Learning Engineer, AI Engineer, and Infrastructure Administrator alongside SRE-flavored strings — adjacent families in one cluster. The doc also notes verbatim surface noise in titles (e.g. trophy emoji) as stored text.
- **Effect on Q&A answers:** Narration that cites **`label` only** can read narrower or mis-scoped versus the title mix the cluster actually holds.
- **Classification:** `fixable in Week 10`
- **Evidence source:** [WEEK09_REFRESH_AGGREGATES_FINDINGS.md](WEEK09_REFRESH_AGGREGATES_FINDINGS.md) — Taxonomy Audit Addendum, **Check 1**; **Taxonomy gaps (carry-forward #230), item 3**; addendum **Recommendation** (third bullet).

### Gap A4

- **Table:** `dbo.canonical_roles`
- **Column / scope:** `label` (two clusters in the same broad mobile domain)
- **What is wrong:** Two clusters both read as mobile application developer families but use **different `label` text** (one includes “(2-4 years of exp) - USC and GC's” in the pasted `label`; the other is shorter).
- **Effect on Q&A answers:** Treating them as one interchangeable “mobile” bucket without naming distinct `label` values risks double-counting or wrong joins when filtering on `role_id` / `label`.
- **Classification:** `fixable in Week 10`
- **Evidence source:** [WEEK09_REFRESH_AGGREGATES_FINDINGS.md](WEEK09_REFRESH_AGGREGATES_FINDINGS.md) — Taxonomy Audit Addendum, **Check 1** (split/overlap signal); **Taxonomy gaps (carry-forward #230), item 4**; addendum **Recommendation** (fourth bullet).

### Gap A5

- **Table:** `dbo.canonical_roles` (read path used by Path B `role_evolution` routing)
- **Column / scope:** Router filter using extracted natural-language `role_names` against cluster `label` text (documented trace: phrase “software developer”)
- **What is wrong:** Path B ran end-to-end (`role_evolution`, `tables_used` = `canonical_roles`) but **`row_count` = 0** after scoping with extracted `role_names` = `['software developer']`; API returned a handled refusal (“No data in scope for the selected filters.”). The addendum ties this to cluster **`label`** values in the Check 1 inventory (SRE, automation/RPA, mobile variants, SDET, etc.) **not** containing the substring “software developer” as the user asked it.
- **Effect on Q&A answers:** **Vocabulary / label mismatch** yields empty scoped reads and refusal even when the taxonomy has other engineering families; evolution narratives stay thin until filters return non-empty slices (and, separately, weekly snapshots exist — see Gap A1).
- **Classification:** `fixable in Week 10` (per addendum: copy, router heuristics, synonym map, or broader unscoped read for evolution-style questions — aligned with other Week 10–class Q&A surface fixes in the same doc.)
- **Evidence source:** [WEEK09_REFRESH_AGGREGATES_FINDINGS.md](WEEK09_REFRESH_AGGREGATES_FINDINGS.md) — Taxonomy Audit Addendum, **§ Q&A trace — "How has the software developer role evolved?"** (intent/router/API bullets and “Why it matters” gap bullet).

### Gap A6

- **Table:** *(artifact)* — issue text; **not** a physical table in-repo
- **Column / scope:** Issue #229 wording references `job_title_to_canonical_role`; implemented audit path is **`dbo.job_postings.canonical_role_id` → `dbo.canonical_roles.role_id`**
- **What is wrong:** There is **no** table or module named `job_title_to_canonical_role` in this repository (addendum repo note); reviewers following issue wording alone may look for the wrong object.
- **Effect on Q&A answers:** Traceability and review comments can miss the real join path; answers and audits must cite **`canonical_role_id` → `role_id`**.
- **Classification:** `out of scope for demo`
- **Evidence source:** [WEEK09_REFRESH_AGGREGATES_FINDINGS.md](WEEK09_REFRESH_AGGREGATES_FINDINGS.md) — Taxonomy Audit Addendum, **Repo note (check #3 wording vs schema)**; **Check 3** intro and “Issue #229 vs repo shape” bullets.

### Gap A7

- **Table:** `dbo.canonical_roles`
- **Column / scope:** Cluster inventory size (`cluster_count`)
- **What is wrong:** Only **six** clusters exist on the audited dev snapshot (`cluster_count` = 6 from the documented query).
- **Effect on Q&A answers:** The addendum notes that thin or ambiguous clusters cap how confidently answers can narrate **which** job family the data is about, even when downstream SQL is valid; a six-cluster inventory limits breadth for organization-wide role questions.
- **Classification:** `requires pipeline re-run`
- **Evidence source:** [WEEK09_REFRESH_AGGREGATES_FINDINGS.md](WEEK09_REFRESH_AGGREGATES_FINDINGS.md) — Taxonomy Audit Addendum, **Check 1** (`cluster_count` and qualitative read); **Why it matters for Q&A readiness** under Check 1.

### Likely Pair A highest-impact candidates:
1. A1 role_snapshot_weekly empty
2. A2 canonical_role_id low coverage
3. A5 vocabulary mismatch causing 0-row routed answers

---

## Pair B — Fatima (temporal / employer)

**Placeholder — findings to be merged from Fatima’s audit.**  
This section will summarize temporal and employer-related data gaps using Fatima’s source doc(s) and evidence. Do not copy Pair A counts here until her content is merged.

---

## Final prioritized top 3 (to be agreed by Pair A + Pair B)

**Placeholder.** After Pair B findings are merged and the team triages, record exactly three prioritized gaps here (id, owner, rough customer impact, and pointer back to the gap rows in **Pair A** / **Pair B** above). Until then, **§ Pair A** is the authoritative unordered inventory for taxonomy/Q&A gaps documented in [WEEK09_REFRESH_AGGREGATES_FINDINGS.md](WEEK09_REFRESH_AGGREGATES_FINDINGS.md).

> Working draft: this file currently captures all candidate Pair A / Pair B gaps.
> Final top-3 prioritization will be decided jointly after both sections are merged.