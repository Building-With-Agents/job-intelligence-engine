# Q&A Eval Harness — Prompt Iteration Log

Tracks every dataset run against the **LaborPulse Golden Questions** corpus.
One row per `--prompt-version` in the version history; full per-run analysis
lives in `eval/runs/findings{version}.md` (findings-per-milestone pattern,
adopted from v2 onward per IMP-030 / JIE #287).

> **Canonical source of truth:** Langfuse dataset runs. Scores in Langfuse are
> authoritative if they disagree with a row here.

---

## Version history

| Version | Date | Author | Scorer | Change summary | Composite | Answerability | Human scores (Langfuse) | Findings |
|---------|------|--------|--------|----------------|:---------:|:-------------:|------------------------|----------|
| `v1-baseline` | 2026-04-23 | Bryan + Emilio | v1 (partial-credit intent, lenient refusal) | Initial baseline — 90 golden Qs, unmodified prompts, in-process pipeline | **0.875** | 0.260 (n=50) | — | `eval/runs/qa-v1-baseline.json` |
| `v2-scorer-redesign` | 2026-04-24 | Bryan + Emilio | v2 (binary intent, geometric composite, None exclusions) | Scorer redesign (PR #278) — binary intent, stronger refusal penalty, `None` for infra failures; run against empty aggregate tables (pre-#291) | **0.510** geo | 0.060 (n=50) | — | [`eval/runs/findingsv2.md`](findingsv2.md) |
| `v2.1-post-seed-fix` | 2026-04-27 | Bryan | v2 (same scorer as v2) | Re-run post-PR #291 aggregate table seed fix; confirms data-seed was sole cause of 0.06 answerability collapse | — | **0.340** (n=50) | — | [`eval/runs/findingsv2.1.md`](findingsv2.1.md) |
| `v2.1-embedding-router` | 2026-04-28 | Pair A / #229 | v2 | `label_embedding` pgvector on `canonical_roles`; backfill + persist sync; Q&A router resolves `role_names` via `<=>` with ILIKE fallback (`role_evolution`, `workflow` only). **Local verification; Langfuse run pending.** Tests: 42 passed; re-cluster env: `CLUSTER_MIN_CLUSTER_SIZE=5`, `CLUSTER_MIN_SAMPLES=2`, `CLUSTER_MIN_TOTAL_POSTINGS=100` + `scripts/run_clustering.py --min-postings 100`. | — | — | — | [`eval/runs/findingsv2.1-embedding-router.md`](findingsv2.1-embedding-router.md) |
| `v2.1-disruption-intent-prompt` | 2026-04-29 | Pair A | v2 | Intent classifier `_SYSTEM_PROMPT` only; added disruption tie-breakers vs role_evolution/comparison/trend/geographic plus two few-shots for era skill-shift and AI-assistant adoption. **Notes:** `intent_accuracy` improved 0.400 → 0.800 on Pair A 20-question slice; remaining misses were gq-008, gq-014, gq-016, gq-017. | **0.772** geo | — | — | [`eval/runs/qa-v2.1-disruption-intent-prompt.json`](qa-v2.1-disruption-intent-prompt.json) |
| `v2.2-emergence-fix` | 2026-04-30 | Pair A | v2 | Intent classifier `_SYSTEM_PROMPT` only; added an **emergence vs disruption** tie-breaker for era-bucket framing (`pre_chatgpt` / `agentic_era`) — net-new roles / first-seen tools / "<5 postings before, 50+ now" → emergence; existing roles whose skill mix transformed → disruption. Plus one few-shot for the "<5-postings-pre-ChatGPT → 50+-in-agentic_era" emergence pattern. **Notes:** intent-only Pair A 20-question slice (`gq-001..gq-020`); `intent_accuracy` 16/20 = **0.800**; misses: gq-008, gq-016, gq-017, gq-019. JSON artefact reports `n_data_backed=0` / `n_intent_only_skipped=20` — full composite + answerability TBD on a data-backed run against a populated DB. | — | — | — | [`eval/runs/qa-v2.2-emergence-fix.json`](qa-v2.2-emergence-fix.json) |
| `v2.3-disruption-trend-tiebreak` | 2026-04-30 | Pair A | v2 | Intent classifier `_SYSTEM_PROMPT` only; added a **disruption vs trend** tie-breaker for share / growth / velocity questions whose subject is AI-tool adoption (Copilot, AIOps, LLM incident triage), automation / RPA penetration, or AI displacement of humans → disruption; generic posting / skill demand share/growth without AI-adoption framing → trend. Plus one few-shot for the "Borderplex DevOps current-period AI-assisted ops share + growth-rate" pattern. **Notes:** intent-only Pair A 20-question slice (`gq-001..gq-020`); `intent_accuracy` 18/20 = **0.900**; misses: gq-016, gq-017. JSON artefact reports `n_data_backed=0` / `n_intent_only_skipped=20` — full composite + answerability TBD on a data-backed run against a populated DB. | — | — | — | [`eval/runs/qa-v2.3-disruption-trend-tiebreak.json`](qa-v2.3-disruption-trend-tiebreak.json) |
| `nestor-baseline` | 2026-04-30 | Nestor | Langfuse UI annotation | Human annotation of gq-031..gq-040 (10 of 90 items); all 3 scores per question. Reconstructed from chronological order on trace `84ab514155f87ac26` — Langfuse queue applied all annotations to one trace rather than each question's trace. Mapping confirmed by annotator. | — | — | correctness=**0.25** (n=10), decision_relevance=**0.10** (n=10), followup_quality=**0.30** (n=10) | [`eval/runs/nestor_baseline_run_langfuse_annotations.json`](runs/nestor_baseline_run_langfuse_annotations.json) |
| `nestor-v2-role-evolution-fix` | 2026-04-30 | Nestor | v2 automated scorer | **Atomic fix:** `_route_role_evolution` rewritten to query `job_postings GROUP BY temporal_period` instead of static `canonical_roles` snapshot. On gq-031..040: `correct_refusal` **+0.30** (0.70→1.00), `must_include_recall` **+0.09** (0.68→0.78). Three previously-refusing questions (gq-032, gq-035, gq-036) now produce data-backed answers. `extracted_intelligence` skill data still absent — see DEV-005. Human annotation pending. | — | **0.52** (n=50) | *pending annotation* | — |
| `dev-verify-2026-05-01` | 2026-05-01 | Gary (post-merge re-audit) | v2 automated scorer | **Post-merge re-audit** of `origin/development` after #288/#294/#295/#335/#336/#337 squash-merged. Run against Gary's SoT DB. Headline: `intent_accuracy` 0.822 (regressed from 1.000 — 16/90 misclassifications), `evidence_citation` 0.571 (regressed from 0.752 — driven by employer 10/10 refusing on `_route_employer` ILIKE bug + disruption 10/10 refusing on `skill_taxonomy_gate_blocked`), `confidence_self_consistency` 0.973 (improved), `latency_sla` 1.000, `answerability` 0.460 (improved). Filed 3 regression-fix issues with regression-discipline pivots: #346 (Pair D, employer router), #347 (Pair A, disruption narrow scoping — includes gq-026 golden label review as a sub-task), #348 (Pair C, geographic add-only fix). Filed #349 backlog (taxonomy expansion). Step-9 ranking: 51/90 demo-eligible, all of Pair D's curation criteria green. | **0.698** geo | **0.460** (n=50) | — | [`eval/runs/findings-dev-verify-2026-05-01.md`](runs/findings-dev-verify-2026-05-01.md) |
| `pairc-week10-harness` | 2026-05-04 | Pair C | v2 | **#340 harness:** cohort CLI (`--cohort pair-c-geo-comp`, `--golden-ids` precedence), lexicographic run order, per-item + run-level **composite** in JSON, Langfuse `Evaluation(name="composite")` + `mean_composite`. Iteration cycles document methodology; headline scores require a DB-backed `--dry-run` or Langfuse re-run — see Pair C table below and [`eval/runs/qa-pairc-week10-harness-contract.json`](runs/qa-pairc-week10-harness-contract.json). | — | — | — | [`eval/runs/findings-pairc-cycle1-baseline.md`](runs/findings-pairc-cycle1-baseline.md) |
| `corpus-edit-gq-026-disruption` (#347 Sub-task A) | 2026-05-11 | Pair A (Ángel + Fabian) | — (corpus-only, no scorer change) | **Corpus edit — #347 Sub-task A, Pair A golden-label decision.** Relabel `gq-026` intent `trend` → `disruption` in [`eval/qa_golden_questions.json`](qa_golden_questions.json). Question text ("What percentage of Borderplex IT postings now reference AI-assistant tools (Copilot, Claude, ChatGPT, Cursor) compared to 12 months ago?") matches the calibrated SHARE / GROWTH / VELOCITY of AI-ADOPTION disruption rule at [`analytics/query_engine/intent.py`](../analytics/query_engine/intent.py) l.260–275 and the disruption few-shot at l.314–316 verbatim (both added in Pair A commit `5546fa0`). The alternative — narrowing the rule to keep gq-026 in trend — would directly regress gq-002 (software-engineer AI-assistant share vs 1 year ago, golden disruption), gq-005 (QA AI-assisted testing share, agentic_era vs pre_chatgpt, golden disruption), and gq-008 (DevOps AIOps share + growth rate, golden disruption, anchored by few-shot l.324–328). Per-intent counts shift to **disruption=11, trend=9**; total stays at **90 items**. No schema fields were added — the canonical 8-key + optional `expected_confidence_range`/`refusal_appropriate` shape is preserved (verified via the parsers at [`scripts/upload_qa_dataset.py`](../scripts/upload_qa_dataset.py) l.58–65 and [`eval/qa_eval.py`](qa_eval.py) l.69). No code change to `intent.py` in this commit — narrow precedence clauses for the remaining classifier-overshoot items (gq-021, gq-029, gq-017, and optionally gq-033/036/039) will follow as separate `dev-verify-disruption-narrow-fix-c2…c5` cycles per the 1-iteration-at-a-time discipline. | — | — | — | (re-run + findings deferred to first code-edit cycle of #347 Sub-task B) |
| `dev-verify-disruption-narrow-fix` (#347 Sub-task B) | 2026-05-11 | Pair A (Ángel + Fabian) | v2 automated scorer | **#347 final — narrow precedence clauses across 4 stacked code cycles (intent.py prompt only; no logic, no rule rewrites, no few-shot removals).** Cycles re-run as `dev-verify-disruption-narrow-fix-c1` (corpus relabel only, gq-026), `…-c3` (pure-volume Q-over-Q with no AI framing → trend, gq-021; non-AI subject with era buckets → trend, gq-029), `…-c4` (explicit first-seen / "did not exist before" tool language → emergence precedence over both disruption tie-breaker and SHARE-of-AI-ADOPTION rule, gq-017), `…-c5` (supporting-mix / expected-AI-literacy-bar within an existing role family → role_evolution carve-out, gq-033 / gq-036 / gq-039). **Final = c5** ([`eval/runs/qa-dev-verify-disruption-narrow-fix-c5.json`](runs/qa-dev-verify-disruption-narrow-fix-c5.json), Langfuse run `8d310165-8c9c-438d-8d2f-3c71691cd447`). **Hard gates ALL green:** disruption class **11/11** (gq-001–010 + gq-026 preserved — Pair A's three-iteration calibration `91f0eb8`/`5546fa0` is intact), emergence **9/10** (gq-017 corrected, 1 unrelated drift), **7 of 7** issue-listed targets resolved (gq-017, gq-021, gq-026, gq-029, gq-033, gq-036, gq-039). Headline scores vs `dev-verify-2026-05-01` baseline: `intent_accuracy` **0.8444** (+0.0222 — 76/90, was 74/90), `evidence_citation` 0.5719 (flat), `confidence_self_consistency` 0.9572 (−0.0156), `latency_sla` 1.0000, `answerability` 0.4600 (flat). Sub-composites: `prompt_quality` 0.5719, `classification` 0.8444 (+0.0222), `pipeline_health` 0.6760, `safety` 0.7982 (−0.0064). Five non-target intent_accuracy drifts (gq-027 / gq-030 trend → role_evolution; gq-055 comparison → other; gq-068 / gq-069 employer → other) are consistent with Haiku-tier classifier nondeterminism at n=90 + a mild lenient-matching tail on the c5 supporting-mix carve-out (gq-027 / gq-030 use "experience-bar" / "skill-mix evolved" framing without explicit AI-assist peer or AI-literacy anchor; next iteration can tighten the carve-out to require AI-assist as named peer in (a) and AI-literacy specifically in (b)). gq-068 / gq-069 are #346-territory employer drift, unrelated to this fix. | **0.8246** geo_4 | **0.4600** (n=50) | — | [`eval/runs/findings-disruption-narrow-fix.md`](runs/findings-disruption-narrow-fix.md) |

> **v2 vs v2.1 note:** scorer logic did not change between v2 and v2.1.
> The only difference is that aggregate tables were empty during the v2 run
> (post-#285 re-export) and populated during v2.1 (post-#291 refresh).
> The composite field for v2.1 reflects individual metric means rather than
> the geometric composite gate; see `findingsv2.1.md` for the full breakdown.

---

## Implementation deviations from spec

Deviations recorded here so that anyone reviewing raw Langfuse scores
understands exactly what each automated scorer measures.

---

### DEV-001 — `latency_sla`: continuous decay curve instead of discrete buckets

**Spec (IMP-030 / runbook):**

| Wall-clock latency | Score |
|--------------------|-------|
| < 10 s | 1.0 |
| 10 – 15 s | 0.5 |
| > 15 s | 0.0 |

**Implemented formula (`eval/qa_scoring.py: score_latency_sla`):**

```
score = min(1.0, sla / latency_seconds)
```

Where `sla` defaults to **45 s** (overridable via env var
`QA_EVAL_LATENCY_SLA_SECONDS`).

**Behaviour at key latencies (sla = 45 s):**

| Latency | Spec score | Implemented score |
|---------|------------|-------------------|
| 5 s | 1.0 | 1.0 |
| 10 s | 1.0 | 1.0 |
| 15 s | 0.5 | 1.0 |
| 20 s | 0.0 | 1.0 |
| 30 s | 0.0 | 1.0 |
| 45 s | 0.0 | 1.0 |
| 60 s | 0.0 | 0.75 |
| 90 s | 0.0 | 0.50 |

**Rationale:**
The JIE Analytics Agent pipeline (SQL generation → execution →
synthesis) has a realistic P50 wall-clock latency of 20 – 40 s in the
current stack. The spec's discrete thresholds were calibrated against
a sub-15 s target that the pipeline does not yet meet. Applying those
thresholds to the baseline run would score every question 0.0 on
`latency_sla`, producing a flat signal that carries no information
across prompt versions. The continuous decay curve preserves
proportional signal at current latencies — a 30 s response scores
meaningfully higher than a 90 s response — and will converge to the
spec's range as pipeline optimisations land.

**Re-computation path:**
Raw latency per question is logged in each Langfuse trace comment
(`"latency_sla": "min(1, 45.0s / latency)"`) and in any
`--output-json` dump. To recompute using the spec's discrete buckets,
apply:

```python
def spec_latency_sla(latency_seconds: float) -> float:
    if latency_seconds < 10:
        return 1.0
    if latency_seconds <= 15:
        return 0.5
    return 0.0
```

against the raw latency values stored per trace.

**Status:** Deviation accepted for v1-baseline. Revisit once
pipeline P50 latency is below 15 s. If the team wants to match the
spec before then, set `QA_EVAL_LATENCY_SLA_SECONDS=15` — the
continuous curve will then approximate the spec's behaviour (15/15 =
1.0, 15/30 = 0.5, 15/90 ≈ 0.17).

---

### DEV-002 — "insufficient data" question coverage: 1 of 20 (Pair C corpus)

**Spec guidance:** At least one question per 10 where the correct
answer is a refusal / "no data available."

**Current state:** Pair C's 20 questions contain **one** explicit
insufficient-data question: `gq-049` (legal-tech postings in El Paso
→ zero results expected). The 10 comparison questions (`gq-051` –
`gq-060`) do not include an equivalent because every comparison
question routes through `skill_demand_weekly` and would produce a
no-data refusal by design until that table is populated — which is
not a meaningful test of refusal behaviour.

**Decision:** 1 of 20 is below the spirit of the spec (should be
≥ 2) but acceptable for v1-baseline given the routing constraint on
comparison questions. Add a second refusal question if the corpus
expands beyond 80 items in a follow-up cycle.

---

### DEV-003 — `source` field dropped from the canonical schema (all 90 rows)

**Spec / Emilio's originals:** `"source": "wfd_archetype"` present on
a subset of items (notably the first-author Pair D rows
`gq-061`–`gq-080`).

**Team-lead call (2026-04-23):** drop `source` from the canonical
golden-question schema. Not required by `upload_qa_dataset.py`
validation, not consistently populated across pairs, and carries no
signal for the automated scorers. Provenance, if needed, belongs in
Langfuse dataset-item metadata — not in the JSON source of truth.

**Current state:** `source` is **not** present on any of the 90
rows. Canonical schema is the 8-key set enforced by the invariant
check run against `eval/qa_golden_questions.json`:

```
{id, question, intent, context, ideal_answer_summary,
 must_include, must_not_include, difficulty}
```

**Applied in:**
- `18783f5 chore(eval): drop non-canonical 'source' field from gq-061-gq-080`
  (2026-04-23) — removed `source` from the 20 Pair D rows that still
  carried it.
- `3f7d657 chore(eval): sync corpus to chore/gq-consolidate-90-medium`
  (2026-04-23) — full-file sync to Gary's consolidated corpus
  (`origin/chore/gq-consolidate-90-medium`); locks the 8-key schema
  across all 90 rows and confirms via the invariant check
  (`schema clean` assertion).

**Decision:** No deviation remaining — spec is now the
`source`-less 8-key schema. Leaving DEV-003 in the log as the
audit trail for the schema change.

---

### DEV-004 — `--use-http` path in `eval/qa_eval.py` regressed by JIE #222 contract drift

**What happened:**
The first v1-baseline launch used `--use-http` against a local
analytics API and returned `pipeline_error: 'http_400'` for all
90/90 items (all Layer-1 scores 0 except `latency_sla=1.0`). The
resulting Langfuse run was deleted; the real v1-baseline was
re-run in-process (see v1-baseline row in version history above).

**Root cause:**
- Commit `d360cda` (Emilio, 2026-04-21 00:49) added
  `execute_qa_item`'s HTTP branch sending
  `{"question", "correlation_id"}` with no custom headers,
  matching the legacy Week 8 `POST /analytics/query` contract.
- Commit `b0cf407` (Juan, 2026-04-21 14:45,
  "Implement LaborPulse POST /analytics/query contract (JIE #222)")
  replaced that shape with the wfd-os contract: required
  `X-Tenant-Id`, `X-User-Email`, `X-Request-Id` headers,
  `X-API-Key` (or `LABORPULSE_ALLOW_NO_API_KEYS=1`), body
  `conversation_id` instead of `correlation_id`.
- The `--use-http` path was not exercised by any test, so the
  regression was silent until v1-baseline kickoff.

**Scope:**
- `--use-http` path only. The default in-process path
  (`run_analytics_qna(session, question, correlation_id)`) was
  unaffected and is what v1-baseline actually ran under.
- No Layer-1 or Layer-2 scores depend on the HTTP wire — the
  scorers operate on the response dict regardless of transport.

**Resolution:** Fixed in JIE #255 (PR #207, merged). `execute_qa_item`
now sends the correct headers (`X-Tenant-Id`, `X-User-Email`,
`X-Request-Id`) and renames `correlation_id` → `conversation_id` in
the request body. Integration tests added to prevent silent regression.

**Status:** Resolved.

---

## Human annotation workflow (Langfuse → repo)

Human annotation scores entered in the Langfuse UI are durable only in Langfuse.
After annotating, run the pull script to snapshot them into the repo:

```bash
# Pull the three standard annotation scores for the nestor baseline run
python scripts/pull_langfuse_annotations.py

# Pull all annotation scores (if you add more score names in Langfuse)
python scripts/pull_langfuse_annotations.py --all-annotations

# Pull annotations for a different run file
python scripts/pull_langfuse_annotations.py --run-json eval/runs/<run>.json

# After pulling, commit the annotations file
git add eval/runs/*_annotations.json
git commit -m "chore(eval): snapshot human annotations from Langfuse <date>"
```

The output file (`eval/runs/<run>_annotations.json`) contains:
- `human_scores` — the values you entered in the Langfuse UI (`source: ANNOTATION`)
- `automated_scores` — the programmatic scorer output from the original run
- `summary` — mean per score name and coverage percentage

**Score names tracked:** `correctness`, `decision_relevance`, `followup_quality`
(add new names with `--score-names <name1> <name2> ...`)

> **Langfuse naming note:** the annotation queue score is `followup_quality` (no underscore between "follow" and "up"). The pull script and all downstream files now use this exact name.

---

## Targeted intent-only smoke runs

Smaller verifications that exercise `classify_workforce_question` directly
against a subset of the gold corpus, without running the full Q&A pipeline.
Use these when a prompt change targets intent classification specifically and
the binding success criterion is the per-question intent matrix. Full
`--prompt-version` runs still belong in the version-history table above.

| Date | Author | Branch | Scope | Result | Artifacts |
|------|--------|--------|-------|--------|-----------|
| 2026-05-03 | Pair (intent prompt iteration) | `fix/intent-employer-disambiguation-gq061-067` | gq-041..050 (geographic, #257 preservation), gq-061/063/065/067 (employer fix), gq-062/064/066/068/069/070 (employer prior spot-check) — 20 questions total | **20/20 pass**: 10/10 geographic, 4/4 employer fix, 6/6 employer prior (all conf ≥ 0.90) | [`eval/runs/findings-intent-employer-fewshot-fix.md`](runs/findings-intent-employer-fewshot-fix.md), [`eval/runs/intent-smoke-employer-fewshot-fix.json`](runs/intent-smoke-employer-fewshot-fix.json) |

---

## How to add a future run

1. Run `eval/qa_eval.py --prompt-version <semantic-tag> --output-json eval/runs/<tag>.json`.
2. Add a row to the **Version history** table with a link to the findings file.
3. Create `eval/runs/findings<tag>.md` using the findings-per-milestone template
   (see `findingsv2.md` as reference): hypothesis tested, three-way scorecard,
   result, what changed, remaining gaps, next steps.
4. Commit the findings doc alongside any prompt change (or immediately after the
   run if no prompt changed).

**Comparison guidance:** Look at per-intent score distributions, not only overall
means. A run with a higher overall mean but a collapsing p25 has gotten *worse*
on hard questions. At n ≈ 90, a 2-point composite delta is within noise.

---

### DEV-005 — `role_evolution` router returns volume data only; `extracted_intelligence` skill content absent

**Observed in:** `nestor-v2-role-evolution-fix`

**What happened:**
After the v2 fix, `_route_role_evolution` queries `job_postings GROUP BY temporal_period`, which
provides posting and employer counts per era. This resolved the three outright refusals (gq-032,
gq-035, gq-036) and lifted `correct_refusal` from 0.70 → 1.00.

However, 8 of 10 role-evolution `must_include` rubrics require `extracted_intelligence` — the
JSONB table that holds per-posting skill names, tool names, tasks, and context signals. Because
the router does not join to `extracted_intelligence`, the synthesis layer has no skill-level data
and cannot name specific skills that changed across temporal eras. Questions that ask "which
skills were added or dropped" still score below ceiling on `must_include_recall`.

**Per-question impact (gq-031..040):**

| GQ | `extracted_intelligence` in rubric? | `must_include_recall` (v2) | ceiling gap |
|----|:---:|:---:|---|
| gq-031 | ✅ | 0.81 | skills gap |
| gq-032 | ✅ | 0.63 | skills gap + was refusal |
| gq-033 | ✅ | 0.78 | skills gap + grounding retry |
| gq-034 | — | 0.91 | uses `canonical_roles`/`role_snapshot_weekly` |
| gq-035 | ✅ | 0.78 | skills + tools gap |
| gq-036 | ✅ | 0.78 | skills + tools + co-occurrence gap |
| gq-037 | ✅ | 0.92 | near-ceiling; seniority_level present |
| gq-038 | ✅ | 0.72 | tools gap |
| gq-039 | ✅ | 0.74 | skills gap + grounding failure regression |
| gq-040 | ✅ | 0.70 | skills + tools gap |

**Planned fix (`nestor-v3`):** Replace the `job_postings GROUP BY temporal_period` query with a
3-table join (`job_postings → normalized_jobs → extracted_intelligence`) that unnests
`ei.skills JSONB` and groups by `(temporal_period, skill_name)`. This returns the top skills per
era for the requested role, giving the synthesis the skill-evolution evidence it needs.

**Status:** Open — next atomic iteration.

---

### Pair C — Week 10 cohort iteration (#340)

Cumulative iteration arc over **`gq-041` … `gq-060`** (geographic + comparison). Each cycle: sort cohort by composite (worst first), inspect top **5** failures, pick **one** primary bucket (intent vs SQL vs synthesis), apply **one** stacked change. Cycles **stack** unless explicitly labeled A/B vs baseline.

| Cycle | Before cohort composite (mean / p25) | Failure pattern (top-5 skew) | Single change (file + summary) | After (mean / p25) | Outcome / link |
|-------|----------------------------------------|--------------------------------|----------------------------------|--------------------|----------------|
| 1 | *TBD — run `python -m eval.qa_eval --prompt-version pairc-week10-c1 --cohort pair-c-geo-comp --dry-run --output-json eval/runs/qa-pairc-week10-c1.json`* | Baseline inventory | Document harness + cohort contract only (`eval/qa_eval.py`, `eval/qa_eval_cohorts.py`, composite JSON + Langfuse alignment) | — | [`findings-pairc-cycle1-baseline.md`](runs/findings-pairc-cycle1-baseline.md) |
| 2 | ref: `dev-verify-2026-05-01` — comparison composite ~0.849 (6/10 answerable; 4 refusing on taxonomy gate or data shape) | **SQL — taxonomy gate block**: gq-054 ("Large Language Models"), gq-056 ("ETL"), gq-060 ("CI/CD" vs "CI") — extracted skill names absent from `dbo.skills` exact match → `skill_taxonomy_gate_blocked` before any query fires | `analytics/query_engine/router.py` — add `_COMPARISON_SKILL_SUPPLEMENT` frozenset (ETL, LLM, Generative AI, MLOps, NLP, continuous-delivery variants) to `_skill_terms_all_in_dbo_skills`; terms bypass DB lookup without breaking RT-007 exact-match guard | pending Gary SoT re-run | [`findings-pairc-cycle2-taxonomy-supplement.md`](runs/findings-pairc-cycle2-taxonomy-supplement.md) |
| 3 | post-cycle-2 baseline — comparison evidence_citation ~0.394 (stacked on top of cycle 2 gate fix) | **Synthesis — thin-data hedge**: even when the comparison gate passes and `skill_demand_weekly` returns 2–5 rows, the synthesis LLM responds with a hedge ("insufficient data") rather than stating the magnitude difference between the two skill counts | `analytics/query_engine/synthesis.py` — add `comparison_clause` to `_build_main_prompt`: instructs LLM to state magnitude ("X has 3× more postings than Y"), identify the leader, and name which side is absent when only one has data; do not refuse when facts are thin — use caveats | pending Gary SoT re-run | [`findings-pairc-cycle3-synthesis-clause.md`](runs/findings-pairc-cycle3-synthesis-clause.md) |

**Langfuse:** When keys are present, capture **`dataset_run_id` + `dataset_run_url`** per cycle in the JSON artifact comments or iteration notes. CI remains offline for scored runs; local-only capture is acceptable per IMP-030.

---

## Changelog

| Date | Who | Change |
|------|-----|--------|
| 2026-04-22 | Bryan | Created skeleton; documented DEV-001–DEV-003; v1-baseline section stubbed |
| 2026-04-23 | Bryan + Emilio | v1-baseline run complete (composite 0.875, answerability 0.260); added DEV-004 for `--use-http` regression |
| 2026-04-27 | Bryan | Restructured per JIE #287: per-run analysis moved to `eval/runs/findings*.md` pattern; log now holds version history table + DEV registry only. Added v2 and v2.1 rows; updated DEV-004 status to Resolved |
| 2026-04-30 | Nestor | Added `nestor-baseline` human-annotation row (Langfuse reconstruction); added `nestor-v2-role-evolution-fix` row with final automated-score results; created `findings_nestor_v2_role_evolution_fix.md`; documented DEV-005 |
| 2026-05-03 | Pair (intent prompt iteration) | Added "Targeted intent-only smoke runs" section and recorded the gq-061..067 employer disambiguation smoke (20/20 pass). Refs #257, #340, #346 |
| 2026-05-04 | Pair C | #340 harness: cohort + golden-id precedence, composite JSON + Langfuse `composite` / `mean_composite`; Pair C iteration table + cycle findings stubs; contract JSON |
| 2026-05-11 | Pair A (Ángel + Fabian) | #347 Sub-task A: corpus-edit row — relabel `gq-026` `trend` → `disruption` to match the calibrated AI-tool-adoption-share rule + few-shot in `analytics/query_engine/intent.py` (Pair A commit `5546fa0`); per-intent counts now disruption=11, trend=9, total 90; no schema fields added |
| 2026-05-11 | Pair A (Ángel + Fabian) | #347 Sub-task B (final): added 4 stacked narrow precedence clauses to `analytics/query_engine/intent.py` `_SYSTEM_PROMPT` (no logic / no rule rewrites / no few-shot removals) — pure-volume Q-over-Q → trend (c2/c3), non-AI subject with era buckets → trend (c2/c3), explicit "did not exist before" first-seen tool language → emergence precedence (c4), supporting-mix / expected-AI-literacy-bar within a role family → role_evolution carve-out (c5). Final = c5: disruption gate **11/11** preserved (Pair A `91f0eb8`/`5546fa0` calibration intact), **7 of 7** issue targets resolved (gq-017/021/026/029/033/036/039), `intent_accuracy` 0.8222 → **0.8444**, `overall_geometric_composite` 0.7108 → **0.7145**. Findings: `eval/runs/findings-disruption-narrow-fix.md`. Artifact: `eval/runs/qa-dev-verify-disruption-narrow-fix-c5.json` |
| 2026-05-11 | Angel (#339) | Backfilled missing iteration-log rows for `v2.2-emergence-fix` and `v2.3-disruption-trend-tiebreak`; artefacts were already committed on 2026-04-30 (commit `5546fa0`) but the cycle rows were missing from the version-history table. |
