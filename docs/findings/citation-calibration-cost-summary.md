# Findings: Citation Accuracy, Confidence Calibration & Cost Breakdown

**Issue:** [#124](https://github.com/Building-With-Agents/job-intelligence-engine/issues/124)
**Pair:** C — Bryan Perez + Emilio Briones Quinones
**Period covered:** Weeks 4–10 (2026-02-01 → 2026-05-23)
**Audience:** Team leads, cohort review, Phase 2 planning

---

## Executive summary

Three questions this document answers:

1. **How accurately does the Q&A pipeline cite its evidence?** Evidence citation score was **0.691** at the v1-baseline and degraded to **0.571** after the May re-audit due to two regressions (employer router bug + disruption taxonomy gap). Both are being addressed (#346, #349).
2. **Is the confidence self-assessment calibrated?** The pipeline is over-confident (reports 0.973 self-consistency at `dev-verify` but the ground-truth answerability is only ~0.46). The `DataSufficiency` enum and volume-warning threshold provide the right levers once taxonomy and data gaps are closed.
3. **What does a full batch cost?** Extraction costs ~$0.0111/job for a 30-record cohort against the main skills path. At 1,000 jobs, the raw LLM spend is ~$11.10; the rate limit (not cost) is the binding constraint for the 5-minute SLA.

---

## 1. Citation accuracy

### What `evidence_citation` measures

`evidence_citation` is scored by the v2 automated scorer (`eval/qa_scoring.py`) as a binary per-item check: does the synthesis answer cite a fact that was verifiably in the `EvidenceBundle` row payload? It is **not** a precision-at-k citation score; it measures whether the answer is grounded in the data the pipeline actually returned.

Full scoring logic: `eval/qa_scoring.py:score_evidence_citation`.

### Historical timeline

| Version | evidence_citation | Notes |
|---------|:-----------------:|-------|
| v1-baseline (2026-04-23) | **0.691** | 90 questions, in-process, populated aggregate tables |
| v2-scorer-redesign (2026-04-24) | 0.510 geo | Scorer redesign + empty aggregate tables (pre-#291) |
| v2.1-post-seed-fix (2026-04-27) | recovered | #291 aggregate seed fix confirmed sole cause of collapse |
| dev-verify-2026-05-01 (2026-05-01) | **0.571** | Regression from two bugs: employer ILIKE (#346) + disruption taxonomy gate (#349) |
| target (post #346 + #349 fixes) | **≥0.70** | #346 fix in router.py (issue open); #349 in review (PR #405) |

**Full per-intent breakdown:** `eval/qa_prompt_iteration_log.md` → `dev-verify-2026-05-01` row.

### Root causes of the 0.691 → 0.571 regression

| Root cause | Impact | Fix |
|------------|--------|-----|
| `_route_employer`: ILIKE matched `geo_terms` against `company_name` — 0 rows for all 10 employer questions | Employer intent ev_cit = 0.196 (10/10 refusing) | Fix in `router.py` (guards added per issue #346; issue remains open) |
| Disruption: AI-tool skill names not in `dbo.skills` → `skill_taxonomy_gate_blocked` before any query fires | Disruption intent 10/10 refusing | PR #405 (`_AI_TOOL_SUPPLEMENTAL_TERMS`) |
| Comparison: "LLM", "ETL" not in exact-match taxonomy | 4/10 comparison questions refusing | PR #406 (`_COMPARISON_SKILL_SUPPLEMENT`) |

### Grounding verification

Post-synthesis grounding (`analytics/query_engine/grounding.py:verify_answer_grounding`) checks numeric tokens in `answer_text` against the `EvidenceBundle` corpus. Invented statistics trigger one synthesis retry, then a safe template fallback. This is a hard deterministic check — not a prompt heuristic.

**Key finding:** grounding check alone is insufficient without accurate SQL first. When the router returns 0 rows (due to bugs above), there are no evidence facts to cite, and any answer the LLM produces will correctly be flagged as low-evidence.

---

## 2. Confidence calibration

### Thresholds (from `analytics/query_engine/constants.py`)

| Threshold | Default | Meaning |
|-----------|:-------:|---------|
| `SKILL_TAXONOMY_GATE_CONFIDENCE_CAP` | **0.35** | Caps confidence when taxonomy gate fires |
| Transparency threshold | **0.6** | Below this, LLM receives explicit instruction to explain why confidence is low |
| Volume warning | **< 30 postings** | `data_volume_warning = True` on `SynthesisResponse` |

### Calibration evidence

| Metric | v1-baseline (v1 scorer) | dev-verify-2026-05-01 (v2 scorer) |
|--------|:-----------------------:|:---------------------------------:|
| confidence_self_consistency | — ¹ | **0.973** (improved from 0.910) |
| intent_accuracy | 0.811 | 0.822 |
| answerability | 0.260 | 0.460 |

> ¹ The v1 scorer uses `confidence_flags` (mean = 1.000), not `confidence_self_consistency`. The metric is not directly comparable across scorer versions. Pre-dev-verify v2 baseline for `confidence_self_consistency` was 0.910 (per `findings-dev-verify-2026-05-01.md`).

**Finding:** `confidence_self_consistency` rose to 0.973 while `answerability` is only 0.460 — the pipeline over-reports confidence on questions it cannot actually answer (taxonomy gate firing, empty aggregates). This is expected calibration behavior: the LLM correctly expresses low confidence on genuinely sparse intents, but the **gate itself** returns a high-looking refusal confidence score rather than 0.0.

**Calibration lever:** `DataSufficiency` (`NO_DATA` / `SPARSE` / `ADEQUATE`) defined in `analytics/query_engine/schemas.py` and used in `analytics/query_engine/evidence.py`. When the pipeline is unblocked (post #349 / #406), answerability is expected to rise toward 0.6+, which will bring calibration closer to actual answer quality. No threshold changes are needed yet — close the data gaps first.

### Confidence calibration recommendation

Do not retune the 0.6 transparency threshold or the volume-warning floor until:
1. `skill_taxonomy_gate` regressions are resolved (#346 fix confirmed live; #349/#406 in review)
2. `_route_employer` geographic fix is confirmed in a live re-run
3. A new full-cohort baseline is captured — calibration metrics will shift

---

## 3. Cost breakdown

### Per-job extraction cost (skills pipeline)

Source: `eval/cost_audit_week5.md` — 30-record cohort, `dbo.llm_audit_log` aggregates.

| Metric | Value |
|--------|------:|
| Total LLM cost (audit window) | **$2.9147** |
| Total tokens | **504,874** |
| Total API calls | **341** |
| Skills share of cost | **~91.5%** ($2.6681) — Sonnet-class extraction |
| Responsibilities + tasks | **~8.5%** ($0.2466) — non-main-path callers |
| Tools / context LLM cost | **$0** — Pass 1 pattern matching eliminated LLM calls |
| **Avg cost per job (skills path)** | **~$0.0111** |

### Per-query Q&A cost

Source: `eval/qa_prompt_iteration_log.md` cost notes, `analytics/query_engine/synthesis.py` role assignments.

| Call leg | LLM role | Tier |
|----------|----------|------|
| Intent classification | `classification` | Haiku-class (`chat-gpt41mini`) |
| SQL routing / evidence | no LLM | deterministic |
| Synthesis (main answer) | `synthesis` | Sonnet-class (`chat-gpt41`) |
| Follow-up suggestions | `classification` | Haiku-class |

No measured per-query cost from `llm_audit_log` is available in this document — the cost audit query in `eval/cost_audit_week5_report.py` does not yet filter for Q&A rows. `synthesis.py` already uses `AGENT_SYNTHESIS = "analytics-qna-synthesis"` and `AGENT_FOLLOWUP = "analytics-qna-followup"` as `agent_name` labels in audit log writes. **Recommendation:** extend `cost_audit_week5_report.py` to include a second aggregate over these two `agent_name` values to produce a per-query cost breakdown.

### Throughput vs. cost trade-off

Source: `docs/findings/llm-throughput-executive-summary.md`.

| Constraint | Value |
|------------|-------|
| Allocated Azure RPM | 190 |
| Calls per job | 6 |
| Max jobs/min (code fix only) | 31 |
| Jobs/min needed for 1,000-job / 5-min SLA | 200 |
| **Cost per 1,000-job batch** | **~$11.10** (at $0.0111/job) |
| **Binding constraint** | Rate limit (190 RPM → 31 jobs/min), not cost |

**Key finding:** cost is not the constraint; the rate limit gap (6.5× below SLA target after code optimization) is. A provisioned-throughput Azure OpenAI deployment would solve the rate limit without changing the per-token cost curve meaningfully.

---

## 4. Cross-references

| Topic | Document |
|-------|----------|
| Full per-intent citation scores | `eval/qa_prompt_iteration_log.md` → `dev-verify-2026-05-01` |
| Synthesis / grounding architecture | `docs/findings/week-08-qna-synthesis-evidence-findings.md` |
| Intent classification calibration | `docs/findings/week-08-intent-classification-kb-routing.md` |
| Cost audit (30-record cohort) | `eval/cost_audit_week5.md` |
| Cost projections / CLI report | `eval/cost_projection.py`, `eval/cost_audit_week5_report.py` |
| Throughput executive summary | `docs/findings/llm-throughput-executive-summary.md` |
| Eval harness architecture | `eval/qa-eval-harness-findings.md` |
| Red-team observations | `eval/qa_red_team_report.md` |
| Taxonomy gate fixes (#349, #406) | PR #405, PR #406 |

---

## 5. Open items

| Item | Owner | Status |
|------|-------|--------|
| Extend `cost_audit_week5_report.py` to aggregate Q&A rows (`analytics-qna-synthesis`, `analytics-qna-followup`) for per-query cost attribution | Pair C / analytics team | Open |
| Post-fix citation accuracy re-run (expected ≥0.70) | Gary (SoT DB) | Pending PR #405 + #406 merge |
| Confidence calibration re-assessment after data gaps closed | Pair C | Pending baseline re-run |
| Provisioned-throughput Azure OpenAI request | Team lead / admin | Open (not blocking Phase 1) |
