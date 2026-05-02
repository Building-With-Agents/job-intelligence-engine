# Eval findings — `dev-verify-2026-05-01`

**Date:** 2026-05-01
**Run:** `eval/runs/qa-dev-verify-2026-05-01.json` (90 items, in-process path)
**Langfuse:** http://localhost:3001/project/job-intelligence-engine/datasets/cmon9mkdr0003o507ym6xvu8p/runs/12640390-c24e-4ceb-a469-0c6ebe5af727
**Branch reviewed:** `origin/development` at tip `c06ff5a` (last 5 commits include all four Week 10 pair PRs: #288 Pair D curriculum, #295 Pair A taxonomy-embedding-router, #294 Pair B red-team-prompt-iteration, #335/#336/#337 Pair C)
**DB:** Gary's SoT Postgres `localhost:5432/talent_finder` — `dbo.job_postings` 4,711 rows, `dbo.employer_profiles` 1,740 rows, `dbo.companies` 2,007 rows
**Migrations:** applied via `scripts/db_check.py migrate` (2 benign skips: `migration_orchestration_audit_skipped`, `migration_canonical_role_fk_skipped`)
**Methodology:** code-first per Gary's "never trust markdown" rule — every claim verified against actual code on `origin/development` (file:line, `git log` author filter, `git blame`) and against eval JSON output

---

## 1) Headline metrics vs v2.4 scorecard

| Metric | v2.4 expected | Run | Drift | Within band? |
|---|---|---|---|---|
| `intent_accuracy` | 1.000 (exact) | **0.822** | -0.178 | ❌ 16/90 misclassified |
| `evidence_citation` | 0.752 ± 0.02 | **0.571** | -0.181 | ❌ regressed |
| `confidence_self_consistency` | 0.910 ± 0.02 | 0.973 | +0.063 | ↑ improved (outside band, good direction) |
| `latency_sla` | — | 1.000 | — | ✅ |
| `answerability` | ~0.20 ± 0.05 | 0.460 | +0.260 | ↑ improved (rubric pass-rate, more questions data-backed than baseline) |

Regression on `intent_accuracy` and `evidence_citation` traces to **3 distinct root causes** (one code bug, two prompt-rule edges). Improvements on `confidence_self_consistency` and `answerability` reflect the corpus expansion and the consolidated synthesis paths landing as expected.

## 2) Per-intent breakdown (n=10 per intent)

| intent | int_acc | ev_cit | refused≈ | composite | notes |
|---|---|---|---|---|---|
| geographic | 1.000 | 0.749 | 2/10 | **0.937** | strongest — Bryan's #257 fix is doing its job |
| disruption | 1.000 | 0.513 | 10/10 | 0.878 | Pair A's classifier OK, but **all 10 refusing on `skill_taxonomy_gate_blocked`** — separate from the misclassification regression |
| curriculum | 1.000 | 0.694 | 0/10 | 0.862 | Pair D — answering every question |
| trend | 0.700 | 0.745 | 4/10 | 0.861 | 3 misclassified (gq-021, 026, 029 → disruption) |
| emergence | 0.800 | 0.636 | 6/10 | 0.859 | 2 misclassified (gq-016 → employer, gq-017 → disruption) |
| comparison | 1.000 | 0.394 | 6/10 | 0.849 | classifier perfect; ev_cit low because of refusals on data shape |
| workflow | 0.700 | 0.564 | 5/10 | 0.816 | 3 misclassified (gq-085, 089, 090) |
| role_evolution | 0.600 | 0.644 | 4/10 | 0.811 | 4 misclassified (gq-032, 033, 036, 039) — Pair B's table redesign worked |
| **employer** | **0.600** | **0.196** | **10/10** | **0.699** | worst — all 10 refusing due to router bug (#346); 4 also misclassified (#348) |

## 3) Regression triage — three root causes

All three filed with code-first evidence and pivoted to honor 1-iteration-at-a-time discipline (i.e. additive fixes, not rule rewrites, with hard regression-test gates).

### #346 — Employer router ILIKE-matches role/geo terms against `company_name` → 10/10 employer questions refuse

**Owner:** Pair D (Juan + Enrique) — extension of #342 employer iteration
**Risk profile:** code-only, no prompt regressions possible
**File:** `analytics/query_engine/router.py:1041-1044`

```python
hints = role_names + geo_terms
name_filter = self._ilike_or(co.company_name, hints)
```

For gq-064 the LLM extracted `role_names=['clinical-data-analyst', 'health-informatics']`, `geo_terms=['Borderplex']`. Generated SQL: `WHERE company_name ILIKE '%clinical-data-analyst%' OR ... ILIKE '%Borderplex%'`. No company is named `Borderplex` → 0 rows for all 10 employer questions. DB has the data (1,740 employer_profiles, 2,007 companies with city/state/normalized_location columns).

**Fix:** route `geo_terms` to `companies.city` / `companies.state` / `companies.normalized_location`; only ILIKE on `company_name` for terms that look like company-name hints.

### #347 — Disruption tie-breaker over-attracts trend / role_evolution / emergence → 7/16 misclassifications

**Owner:** Pair A (Ángel + Fabian) — extension of #295 taxonomy-embedding-router
**Risk profile:** **Pair A's 3-iteration calibration on gq-001–010 is load-bearing. Hard regression-test gate enforced in the issue.**
**File:** `analytics/query_engine/intent.py:139-192` (tie-breakers) + l.227, 231, 241 (3 disruption few-shots)

Pivoted scope: not a wholesale rule rewrite. Two narrowly scoped sub-tasks:

1. **Golden label review:** gq-026 ("share of postings reference AI-assistant tools") matches the existing rule + few-shot exactly. Update the golden label, document rationale.
2. **Add narrow precedence clauses** to existing rules — do NOT rewrite them — for: pure-volume Q-over-Q without AI framing (gq-021), era buckets with non-AI subject (gq-029), explicit "did not exist" first-seen language (gq-017).

Hard regression test: gq-001–010 must remain 10/10 disruption.

### #348 — Geographic few-shot l.199 (NMSU/UTEP) trains employer→geographic confusion → 4/16 misclassifications

**Owner:** Pair C (Bryan + Emilio) — extension of #340 geographic/comparison iteration
**Risk profile:** **Bryan's prior fix (#257, commits `546069c1` + `2cd7a7f5`) at l.199 is load-bearing for gq-041–050. Original proposal to "rewrite l.199" would have re-introduced the #257 regression. Pivoted to add-only.**

Pivoted scope: do NOT modify l.199. Add 2 new employer few-shots and 1 comparison-vs-employer tie-breaker clarification, covering 4 specific patterns Bryan's calibrated set didn't anchor:

| gq | gap pattern | covered by Bryan? |
|---|---|---|
| gq-061 | "Show all postings AT &lt;institution-list&gt;" | no |
| gq-063 | "List from &lt;employer-type&gt;" | no |
| gq-065 | "Show all &lt;region&gt; &lt;industry&gt; employer postings" | no |
| gq-067 | "Compare &lt;employer-type&gt; vs &lt;employer-type&gt;" | no |

Hard regression test: gq-041–050 must remain 10/10 geographic; gq-062, 064, 066, 068, 069, 070 must remain employer.

## 4) Demo candidate ranking (Step 9)

Output: `eval/runs/ranking-dev-verify-2026-05-01.txt`

**51 of 90 demo-eligible** (composite ≥ 0.85). Pair D's curation criteria all pass:

| Criterion | Required | Got |
|---|---|---|
| Intent diversity ≥ 3 | 3 | **8** (comparison, curriculum, disruption, emergence, geographic, role_evolution, trend, workflow) |
| High-evidence (ev_cit ≥ 0.80) ≥ 3 | 3 | **24** |
| Curriculum eligible ≥ 1 | 1 | **3** |

Only excluded intent: **employer** (top-scoring at 0.801) — will recover automatically when #346 + #348 land.

### Suggested 7-question demo set

Picks balance composite + narrative coverage; gives Pair D a defensible starting point per their runbook (consistency over 3 runs, follow-up flow, ≥1 curriculum).

| # | Intent | gq | composite | Why |
|---|---|---|---|---|
| 1 | trend | gq-030 | 1.000 | Strong opener — perfect score, "cybersecurity skills mix evolved" |
| 2 | workflow | gq-087 | 0.996 | Concrete MLOps lifecycle answer with visible structure |
| 3 | geographic | gq-047 | 0.985 | El Paso entry-level IT with salary range — relatable, named |
| 4 | role_evolution | gq-037 | 0.980 | Seniority mix shift in data engineer postings |
| 5 | emergence | gq-014 | 0.968 | "Roles grew from <5 before ChatGPT to 50+" — narrative gold |
| 6 | curriculum | gq-073 | 0.968 | Pair D's signature intent — cybersecurity training program |
| 7 | comparison | gq-052 | 0.952 | Cloud Computing vs Cybersecurity — clean A-vs-B |

**Skip:** employer (regressed, blocked on #346/#348) and disruption (refusing on `skill_taxonomy_gate_blocked` — separate issue not in this triage).

### Caveat for Pair D

This ranking is from the **regressed** `dev-verify-2026-05-01` run. Once #346 + #347 + #348 land, **re-run the eval** before locking the final demo set:
- Employer should re-enter the eligible set
- Several disruption questions may move into top-15 territory
- Misclassifications in gq-021/026/029/033/036/039 should resolve

## 5) Sub-composites (JIE #268)

| Composite | Score | Status |
|---|---|---|
| `prompt_quality_composite` | 0.571 | proxies evidence_citation until #265 — regressed with the eval |
| `classification_composite` | 0.822 | regressed alongside `intent_accuracy` |
| `pipeline_health_composite` | 0.628 | OK |
| `safety_composite` | 0.805 | OK |
| `overall_geometric_composite` | 0.698 | OK, gate not tripped |

## 6) Documentation drift uncovered

`eval/qa_golden_questions.json` contains **exactly 90 items** (9 intents × 10 each). Multiple curriculum-side artifacts reference "80 questions":

- `lesson-framework/week-11/WEEK-11-final-eval-run-bryan-emilio-runbook.md`
- `lesson-framework/week-11/WEEK-11-demo-curation-polish-juan-enrique-runbook.md`
- `lesson-framework/week-11-demo-polish-and-stakeholder-demo.md`
- `instructor-use/week-11/presentations/presentation-outline-week-11-kickoff.md`
- `instructor-use/week-11/presentations/presentation-outline-week-11-kickoff-instructor.md`
- `instructor-use/week-11/presentations/cjs/generate-week11-kickoff.cjs`
- Both Week-11-Kickoff PPTX files (regenerate after CJS update)

Tracked separately. Memory file `reference_golden_question_count.md` to be added to the curriculum-planning memory index.

## 7) Decision points outstanding for Gary

1. **Demo set lock timing** — wait for #346/#347/#348 to land before Pair D commits the 5–7 demo questions, or use this run's top-7 as a placeholder?
2. **gq-026 golden label** — recommend updating to `disruption` per the existing prompt rule. Confirm before Pair A pivots their fix.
3. **Skill-taxonomy-gate refusals on disruption (gq-001–010)** — separate issue from the three filed. All 10 refuse on `skill_taxonomy_gate_blocked` because the AI-tool skill names ("Copilot", "AI-adjacent", "automation") aren't in the taxonomy. Worth filing as a 4th regression-fix issue, or scope as a Week 11 follow-up?

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
