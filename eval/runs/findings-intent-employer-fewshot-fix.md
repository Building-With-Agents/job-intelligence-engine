# Findings — Intent Employer Few-Shot Fix (gq-061..067)

**Date:** 2026-05-03
**Author:** Pair (intent prompt iteration)
**Branch:** `fix/intent-employer-disambiguation-gq061-067`
**Refs:** [JIE #257](https://github.com/Building-With-Agents/job-intelligence-engine/issues/257) (geographic primacy — preserved), [JIE #340](https://github.com/Building-With-Agents/job-intelligence-engine/issues/340) (employer disambiguation), [JIE #346](https://github.com/Building-With-Agents/job-intelligence-engine/issues/346) (Pair D downstream)

---

## 1. Problem

Under the `dev-verify-2026-05-01` corpus run, four gold questions whose canonical
intent is **`employer`** were misclassified in the *opposite* direction from
the regression that motivated #257:

| ID | Question (abbrev.) | Gold | Wrong-direction model output |
|---|---|---|---|
| gq-061 | "AI-core roles at UTEP, NMSU, or EPCC..." | `employer` | `geographic` |
| gq-063 | "IT postings from Borderplex federal-contractor employers..." | `employer` | `geographic` |
| gq-065 | "Borderplex fintech and payments-technology employer postings..." | `employer` | `geographic` |
| gq-067 | "Compare hiring activity between Borderplex academic employers... and private-sector IT employers" | `employer` | `comparison` |

The four surface forms overlap with the load-bearing geographic anchoring
few-shots that #257 added (Las Cruces / NMSU + UTEP, Borderplex cybersecurity)
and, in gq-067, with the literal word "Compare."

## 2. Approach — add-only prompt edits

Two additions to `_SYSTEM_PROMPT` in
[`analytics/query_engine/intent.py`](../../analytics/query_engine/intent.py).
Nothing existing was rewritten; the geographic-primacy block, the GEOGRAPHIC
vs EMPLOYER tie-breaker text, the three geographic anchoring few-shots
(El Paso AI roles, Las Cruces NMSU/UTEP, Borderplex cybersecurity), and the
two existing employer few-shots (Borderplex employers / Dell) are unchanged.

### Edit 1 — extended `comparison` bullet (commit `35d1f0d`)

A tie-breaker sentence: comparing two **employer cohorts** within **one**
region (academic vs private-sector, federal contractors vs commercial,
public vs private) is primary intent **employer**, not **comparison**.
**`comparison`** is reserved for two locations, two time periods, or two
skills/roles — which keeps the existing El Paso vs Las Cruces few-shot
consistent.

### Edit 2 — two new employer few-shots (commit `991abcb`)

Inserted directly after the Dell example, before the El Paso vs Las Cruces
comparison example, so all employer few-shots stay grouped.

1. **"Postings at named institutions" → employer.** Anchors gq-061 and
   explicitly contrasts itself with the existing Las Cruces / NMSU
   geographic few-shot, where the city is the primary filter and the
   institutions are secondary qualifiers.
2. **"Postings from <employer-type> employers in <region>" → employer.**
   Anchors gq-063 (federal contractors), gq-065 (fintech/payments),
   gq-064 (healthcare-IT), and reinforces gq-066/068 by name pattern.

## 3. Verification — intent-only smoke (2026-05-03)

Direct calls to
[`classify_workforce_question`](../../analytics/query_engine/intent.py)
against the 20 gold questions in the regression matrix. Artifact:
[`intent-smoke-employer-fewshot-fix.json`](intent-smoke-employer-fewshot-fix.json).

| Cohort | Result | Pass rate |
|---|---|---|
| Geographic (#257 preservation) — gq-041..050 | 10/10 `geographic` (conf ≥ 0.90) | **100%** |
| Employer fix — gq-061, 063, 065, 067 | 4/4 `employer` (conf ≥ 0.90) | **100%** |
| Employer prior spot-check — gq-062, 064, 066, 068, 069, 070 | 6/6 `employer` (conf ≥ 0.90) | **100%** |
| **Total** | **20/20** | **100%** |

All 20 calls succeeded against Azure OpenAI `chat-gpt41mini`
(`role="classification"`, `LLM_DEFAULT`); per-call latency 1.0–1.8 s,
per-call cost ≈ $0.00065. The local Postgres `llm_audit_log` write was
unavailable during the smoke (DB not running) — this does not affect
classification correctness, only the audit-log persistence.

## 4. Existing test guards

The structural and gold-question parser tests in
[`analytics/tests/test_intent_classification.py`](../../analytics/tests/test_intent_classification.py)
all pass after the edits:

- `test_geographic_gold_questions_parse_correctly[gq-041..050]` — 10 mock-LLM
  parses validate that a `{"intent":"geographic"}` LLM response still maps to
  `'geographic'` for each gq-041..050 text.
- `test_geographic_intent_in_prompt_has_borderplex_rule` — structural assertion
  that `_SYSTEM_PROMPT` still contains `geographic`, `POSTINGS`, `EMPLOYERS`,
  `Borderplex`, `El Paso`, `Las Cruces`. This is the canary against accidental
  removal of the #257 primacy rule; the add-only edits in this PR do not touch
  any of those phrases.
- `test_live_geographic_gold_per_intent_floor` (`@pytest.mark.live_llm`) — live
  per-intent floor of ≥ 0.8 on gq-041..050. The intent smoke above shows
  10/10, well above the floor.

Result: `43 passed, 2 deselected (live_llm)` in 3.88 s.

## 5. Scope and risk

- **Scope:** [`analytics/query_engine/intent.py`](../../analytics/query_engine/intent.py)
  (add-only inside `_SYSTEM_PROMPT`),
  [`eval/qa_prompt_iteration_log.md`](../qa_prompt_iteration_log.md) and this
  findings file. No agents outside `analytics/`, no Next.js frontend, no
  Prisma schema, no `.cursor/rules/*.mdc` touched.
- **LLM policy:** classifier still routes through
  `common.llm_adapter.complete(..., role="classification")` (Haiku-tier;
  Azure OpenAI `chat-gpt41mini` via `LLM_DEFAULT`). No Anthropic model IDs
  introduced.
- **Prompt length:** Edit 1 adds 4 short lines; Edit 2 adds 2 Q/A few-shots
  (≈ 14 lines). Net delta ≈ 18 lines, well within `chat-gpt41mini`'s context
  budget for a 500-token output.
- **Edge cases:** The most plausible failure mode would be a question that
  pairs a city *and* an "at <institution>" clause (e.g. "AI roles at NMSU
  in Las Cruces"). The new institution few-shot's contrast text explicitly
  re-asserts the geographic case ("Las Cruces postings, highlighting
  NMSU/UTEP/EPCC" → geographic), which together with the unchanged #257
  GEOGRAPHIC vs EMPLOYER tie-breaker block keeps the city-as-primary case
  intact. Any future regression on that edge case would surface in the
  `live_llm` per-intent floor test.

## 6. Follow-ups (not in this PR)

- Run a full `python -m eval.qa_eval --prompt-version <tag>` once Postgres
  is available to also capture answerability / evidence / latency / Langfuse
  metrics across the 90-question suite. Append a row to the version-history
  table at that time and link the resulting `findingsv*.md`. The intent
  matrix above is the binding success criterion for the prompt change
  itself.
- An analogous structural test (`_SYSTEM_PROMPT` must contain the new
  employer few-shot anchors and the comparison tie-breaker phrase) would be
  a cheap belt-and-braces guard. Deferred per the brief's "scope: touch only
  files required for this workstream" guidance — the live floor test
  already catches semantic regressions at PR time.
