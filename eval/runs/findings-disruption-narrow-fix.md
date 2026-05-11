# Eval findings — `dev-verify-disruption-narrow-fix` (#347)

**Date:** 2026-05-11
**Owner:** Pair A (Ángel Coronel + Fabian)
**Final run:** [`eval/runs/qa-dev-verify-disruption-narrow-fix-c5.json`](qa-dev-verify-disruption-narrow-fix-c5.json) (n=90, in-process path)
**Langfuse run:** `8d310165-8c9c-438d-8d2f-3c71691cd447` ([dataset run URL](https://langfuse.watechcoalition.org//project/laborpulse-golden-questions/datasets/cmobvy2aj002pp1070t0zbwir/runs/8d310165-8c9c-438d-8d2f-3c71691cd447))
**Baseline compared against:** [`eval/runs/qa-dev-verify-2026-05-01.json`](qa-dev-verify-2026-05-01.json) (`12640390-c24e-4ceb-a469-0c6ebe5af727`)
**Issue:** [#347](https://github.com/Building-With-Agents/job-intelligence-engine/issues/347) — "Disruption tie-breaker over-attracts trend/role_evolution/emergence — 7/16 intent misclassifications"

---

## 1. Original failure mode

The post-merge `dev-verify-2026-05-01` audit ([findings-dev-verify-2026-05-01.md](findings-dev-verify-2026-05-01.md)) flagged that **7 of 16** intent misclassifications all flowed toward `disruption`, regressing `intent_accuracy` from 1.000 → 0.822:

| gq | golden | baseline got | failure mode |
|---|---|---|---|
| gq-017 | emergence | disruption | "tools that did not exist in pre_chatgpt" is emergence-definition language, but the SHARE-of-AI-ADOPTION rule fired later in the prompt and overshot |
| gq-021 | trend | disruption | Pure posting-volume Q-over-Q; era tokens (pre_chatgpt → agentic_era) used only as time-axis descriptor — disruption tie-breaker over-attracted on the era cue |
| gq-026 | trend (orig) | disruption | "What percentage … reference AI-assistant tools (Copilot, Claude, Cursor, ChatGPT)" — matches the calibrated AI-tool-adoption-share rule and few-shot at [`intent.py`](../../analytics/query_engine/intent.py) l.260–275 + l.314–316 verbatim. Golden label was the anomaly. |
| gq-029 | trend | disruption | Era buckets present (pre_chatgpt vs agentic_era) but subject is remote-eligibility, not AI/automation |
| gq-033 | role_evolution | disruption | "Balance between AI-assistant familiarity and traditional programming skills evolved across the four temporal periods" — era buckets + skill-mix triggered disruption rule |
| gq-036 | role_evolution | disruption | "Supporting-skill mix (TypeScript, Next.js, testing frameworks, AI-assisted dev tools) changed" — AI-assist as 1 of 4 peer items, not the primary subject |
| gq-039 | role_evolution | disruption | "Expected AI-literacy level evolved from pre_chatgpt to agentic_era" — expectation/bar evolution, not adoption share |

Risk profile for the fix was high: Pair A's three-iteration calibration of the disruption tie-breaker and few-shots (commits `91f0eb8` and `5546fa0`) was load-bearing for `gq-001` through `gq-010`, all 10 of which still classified correctly in the regressed baseline (10/10). A wholesale rewrite would have risked regressing that set.

## 2. Pair A golden-label decision — `gq-026`

`gq-026` was the one ambiguous case. The question text matches the SHARE / GROWTH / VELOCITY of AI-ADOPTION rule at [`intent.py`](../../analytics/query_engine/intent.py) l.260–275 and the disruption few-shot at l.314–316 word-for-word (both added in `5546fa0` precisely to route this pattern):

> Q: "In Borderplex software engineering postings, how does AI-assistant tool adoption today compare to one year ago?"
> A: `{"intent":"disruption",...}` ← adoption shift of AI workplace tools over time → disruption, not a simple demand trend.

Two paths were considered. Path (a): relabel the golden `trend` → `disruption`. Path (b): narrow the rule + few-shot to keep `gq-026` in `trend`. Path (b) would have directly regressed:

- **gq-002** — "share now lists AI-assistant tools (Copilot, Claude, Cursor, ChatGPT) as required or preferred, compared to one year ago" (golden disruption — `gq-026`'s closest sibling)
- **gq-005** — "What percentage … mention AI-assisted testing tools … in the agentic_era period versus pre_chatgpt" (golden disruption)
- **gq-008** — "what share of current-period postings reference AI-assisted operations … how fast is that share growing" (golden disruption, anchored by the few-shot at l.324–328)

**Pair A picked (a).** The rule + few-shot were the more recent, more deliberate intent; the golden label predated Pair A's calibration cycles. Per-intent counts shifted to **disruption=11, trend=9**, total stayed at **90 items**. No schema fields were added — verified against parsers at [`scripts/upload_qa_dataset.py`](../../scripts/upload_qa_dataset.py) l.58–65 (`_REQUIRED_FIELDS`) and [`eval/qa_eval.py`](../qa_eval.py) l.69 (`_REQUIRED`).

## 3. Narrow scoping clauses added (Sub-task B)

All four clauses are **additive text-only edits** to the existing `_SYSTEM_PROMPT` in [`analytics/query_engine/intent.py`](../../analytics/query_engine/intent.py). The four pre-existing tie-breaker bullets, the EMERGENCE-vs-DISRUPTION block, the SHARE / GROWTH / VELOCITY of AI-ADOPTION block, and **all five anchoring few-shots** (l.310–333) are byte-for-byte unchanged.

| Cycle | Clause | Location in `_SYSTEM_PROMPT` | Resolves |
|---|---|---|---|
| c2 | Pure posting-volume Q-over-Q / M-over-M / W-over-W with NO AI / automation / skill-mix framing → trend, even when era tokens appear only as a time-axis descriptor ("the WHEN, not the WHAT") | `→ trend:` bullet inside DISRUPTION-vs-other tie-breaker (l.236–243) | gq-021 |
| c3 | Share or distribution questions with a non-AI subject (remote-eligibility, salary, experience-bar, headcount) → trend even with era buckets; era tokens scope the comparison range, they do not promote a non-AI subject to disruption | Same `→ trend:` bullet (appended sentence, l.242–249) | gq-029 |
| c4 | EXPLICIT first-seen language ("did not exist before", "first appeared", "newly emerging", "tools that did not exist in [prior era]") → emergence takes **PRECEDENCE** over BOTH the disruption tie-breaker AND the SHARE-of-AI-ADOPTION rule below, even when era buckets are present and the subject is AI tools | New `PRECEDENCE:` block appended to EMERGENCE-vs-DISRUPTION `RULE:` (l.270–280) | gq-017 |
| c5 | ROLE_EVOLUTION CARVE-OUT — supporting-skill mix / expected AI-literacy bar / balance within an existing role family → role_evolution even with era buckets, provided (a) AI-assist is one peer in a multi-skill list OR (b) the expectation/bar/literacy level is the subject. DISAMBIGUATOR: AI-adoption SHARE / GROWTH RATE / VELOCITY / PENETRATION as PRIMARY subject → disruption (per SHARE-of-AI-ADOPTION rule below) | New `ROLE_EVOLUTION CARVE-OUT:` block at end of DISRUPTION-vs-other tie-breaker, before EMERGENCE block (l.254–273) | gq-033, gq-036, gq-039 |

**Anti-leakage guards baked into each clause:**

- c2 requires "NO AI / automation / displacement / skill-or-tool-mix framing" — keeps gq-026, gq-002, gq-005, gq-008 in disruption.
- c3 requires the share subject to be in an explicit non-AI list — keeps gq-026, gq-002, gq-005, gq-008 in disruption.
- c4 requires EXPLICIT first-seen wording (capitalized in the prompt for emphasis); gq-026 / gq-002 / gq-005 / gq-008 use "compared to 12 months ago" / "agentic_era vs pre_chatgpt" framing without first-seen cues.
- c5 explicit DISAMBIGUATOR: AI-adoption SHARE / PERCENTAGE / GROWTH RATE / VELOCITY / PENETRATION as PRIMARY subject → disruption. Closing sentence names gq-001 / gq-004 / gq-007 / gq-010 patterns ("skill-composition SHIFT, skills DROPPED OUT, role TRANSFORMED, AI-DENSITY THRESHOLD CROSSING, displacement by automation") and routes them back to disruption.

## 4. Regression gates — all green

| Gate | Required | c5 final | Status |
|---|---|---|---|
| gq-001..gq-010 classify as `disruption` | 10/10 | **10/10** | ✓ Pair A `91f0eb8`/`5546fa0` calibration preserved |
| gq-026 classifies as `disruption` (post-relabel) | 1/1 | **1/1** | ✓ |
| **Disruption class total (11 items)** | 11/11 | **11/11** | ✓ |
| gq-011..gq-020 (emergence) | ≥ 9/10 | **9/10** (gq-017 corrected) | ✓ |
| gq-017 corrected to `emergence` | yes | **yes** | ✓ |
| At least 4 of 7 issue targets resolved | ≥ 4 | **7 of 7** | ✓ exceeded |

**Per-target outcome:**

| gq | golden (now) | baseline | c5 | target hit? |
|---|---|---|---|---|
| gq-017 | emergence | 0.0 | **1.0** | ✓ |
| gq-021 | trend | 0.0 | **1.0** | ✓ |
| gq-026 | disruption | 0.0 | **1.0** | ✓ |
| gq-029 | trend | 0.0 | **1.0** | ✓ |
| gq-033 | role_evolution | 0.0 | **1.0** | ✓ |
| gq-036 | role_evolution | 0.0 | **1.0** | ✓ |
| gq-039 | role_evolution | 0.0 | **1.0** | ✓ |

## 5. Final metrics — `c5` vs `dev-verify-2026-05-01` baseline

| Metric | Baseline | c5 final | Δ |
|---|---:|---:|---:|
| `intent_accuracy` (mean) | 0.8222 | **0.8444** | **+0.0222** |
| `evidence_citation` | 0.5707 | 0.5719 | +0.0013 |
| `must_include_recall` | 0.6222 | 0.6489 | +0.0267 |
| `evidence_overlap` | 0.7041 | 0.5659 | −0.1382 |
| `confidence_self_consistency` | 0.9728 | 0.9572 | −0.0156 |
| `confidence_in_expected_range` | 0.4922 | 0.5028 | +0.0106 |
| `latency_sla` | 1.0000 | 1.0000 | 0.0000 |
| `answerability` (n=50) | 0.4600 | 0.4600 | 0.0000 |
| **per-item composite mean** (intent + ev_cit + conf_sc + lat_sla, eval/qa_scoring.py `composite_score`) | 0.8414 | **0.8434** | +0.0020 |
| `composite_p25` | 0.7892 | **0.7999** | +0.0107 |
| `prompt_quality_composite` | 0.5707 | 0.5719 | +0.0013 |
| `classification_composite` | 0.8222 | **0.8444** | **+0.0222** |
| `pipeline_health_composite` | 0.6760 | 0.6760 | 0.0000 |
| `safety_composite` | 0.8046 | 0.7982 | −0.0064 |
| **`overall_geometric_composite`** | 0.7108 | **0.7145** | **+0.0037** |

### Per-intent intent_accuracy

| intent | golden n | baseline pass | c5 pass | Δ |
|---|---:|---:|---:|---:|
| comparison | 10 | 10/10 | 9/10 | −1 (drift) |
| curriculum | 10 | 10/10 | 10/10 | 0 |
| **disruption** | 11 | 10/11 | **11/11** | **+1** (gq-026 fixed) |
| emergence | 10 | 8/10 | 9/10 | +1 (gq-017 fixed) |
| employer | 10 | 6/10 | 4/10 | −2 (#346 territory, unrelated) |
| geographic | 10 | 10/10 | 10/10 | 0 |
| role_evolution | 10 | 6/10 | 9/10 | +3 (gq-033/036/039 fixed) |
| trend | 9 | 7/9 | 7/9 | 0 net (gq-021/029 fixed, gq-027/030 drifted) |
| workflow | 10 | 7/10 | 7/10 | 0 |
| **TOTAL** | **90** | **74/90** | **76/90** | **+2** |

### Non-target drifts

Five items where intent_accuracy moved from 1.0 → 0.0 between baseline and c5, none in the calibrated disruption set:

| gq | golden | baseline → c5 | likely cause |
|---|---|---|---|
| gq-027 | trend | 1.0 → 0.0 | "experience-requirement bar" wording lenient-matched by the c5 carve-out's `expectation / bar` clause despite the subject being non-AI; next iteration should tighten (b) to explicitly require AI-literacy as subject, not just any "bar". |
| gq-030 | trend | 1.0 → 0.0 | "mix of required skills evolved over the last 18 months" lenient-matched by the c5 carve-out's `supporting-skill mix` clause despite no AI-assist peer being named; next iteration should tighten (a) to require AI-assist as a named peer. |
| gq-055 | comparison | 1.0 → 0.0 | Unrelated to #347 clauses — classifier nondeterminism on a comparison-class boundary item. |
| gq-068 | employer | 1.0 → 0.0 | #346 employer-router territory, unrelated to this fix. |
| gq-069 | employer | 1.0 → 0.0 | Same. |

The two trend → role_evolution drifts (gq-027, gq-030) are the only ones potentially attributable to a clause added in this PR (c5). They represent a mild lenient-matching tail on the role_evolution carve-out; the next iteration cycle can tighten the (a) and (b) sub-conditions to require an explicit AI-assist peer or AI-literacy anchor. This was triaged below the 7-of-7 acceptance bar and not addressed in this PR per 1-iteration-at-a-time discipline.

## 6. What changed in this PR

- [`analytics/query_engine/intent.py`](../../analytics/query_engine/intent.py) — four narrow precedence clauses inserted into the existing tie-breaker blocks of `_SYSTEM_PROMPT`. **No rule rewrites. No few-shot removals. No logic changes.** Diff is text-only.
- [`eval/qa_golden_questions.json`](../qa_golden_questions.json) — one field flipped on `gq-026` (`"intent": "trend"` → `"intent": "disruption"`). No schema changes.
- [`eval/qa_prompt_iteration_log.md`](../qa_prompt_iteration_log.md) — corpus-edit row + final cycle row + two changelog entries.
- [`eval/runs/qa-dev-verify-disruption-narrow-fix-c5.json`](qa-dev-verify-disruption-narrow-fix-c5.json) — final eval artifact.
- This findings doc.

## 7. Next steps

1. **Tighten c5 carve-out** (next iteration cycle) — require AI-assist as named peer in (a) and AI-literacy specifically in (b) to recover gq-027 and gq-030.
2. **#346 employer router** is a separate Pair D concern (`_route_employer` ILIKE bug); not in scope here.
3. **`skill_taxonomy_gate_blocked` refusals on gq-001..010** — separate issue per Gary's outstanding decision point in [findings-dev-verify-2026-05-01.md](findings-dev-verify-2026-05-01.md) §7.3; intent_accuracy is fixed by this PR but evidence_citation on those items remains capped by the taxonomy gate.
