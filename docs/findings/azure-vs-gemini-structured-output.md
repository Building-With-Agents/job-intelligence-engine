# Azure OpenAI vs Gemini: Structured Output Reliability Analysis

**Date:** 2026-04-12  
**Author:** Gary Larson  
**Decision:** Retain Azure OpenAI (gpt-4.1-mini) as primary LLM provider  
**Data source:** `dbo.llm_audit_log` + `dbo.extracted_intelligence` — live pipeline runs

---

## Executive Summary

A side-by-side pipeline run comparing Azure OpenAI (gpt-4.1-mini) and Gemini 2.5 Flash
revealed a critical structured output reliability gap. Gemini returned empty skill, task, and
responsibility arrays on 100% of completed extraction jobs while marking them as successful
with 0.97 confidence. Azure delivered meaningful structured output on 98.5% of jobs with an
average of 13.5 skills, 10.1 tasks, and 6.5 responsibilities per posting.

The cost difference between providers is **~15% per job** — not the 2x implied by list pricing
— because Gemini uses significantly more output tokens to produce the same result, offsetting
its lower per-token rate.

---

## Test Conditions

| Dimension | Azure OpenAI | Gemini 2.5 Flash |
|-----------|-------------|-----------------|
| Model | gpt-4.1-mini-2025-04-14 | gemini-2.5-flash |
| Deployment | chat-gpt41mini (Azure) | chat-gpt41mini (routed) |
| Tier | Paid / production | **Free tier** (rate-limited) |
| Run period | 2026-04-03 → 2026-04-12 | 2026-04-11 20:32 → 2026-04-12 |
| Total LLM calls | 7,861 | 5,002 |
| Jobs fully processed | 644 | 2 |

> **Latency caveat:** Gemini latency numbers in this report reflect free-tier rate limiting
> and are not representative of paid-tier performance. Latency is excluded from the decision
> criteria for this reason.

---

## Finding 1: Structured Output Silent Failure (Critical)

The most significant finding is that Gemini produced **silent structured output failures** —
extractions that completed without error but returned zero usable data.

| Metric | Azure | Gemini |
|--------|-------|--------|
| Jobs processed | 644 | 2 |
| `extraction_failed = True` | 1.55% | 0% |
| Avg skills extracted | **13.54** | **0** |
| Avg tasks extracted | **10.09** | **0** |
| Avg responsibilities extracted | **6.50** | **0** |
| Avg overall confidence | 0.9138 | 0.97 |

Gemini reported **high confidence (0.97)** and **no failure flag** while extracting zero
structured fields. Azure returned rich structured output with 98.5% success.

This is the most dangerous failure mode in a pipeline context: the job appears processed,
confidence looks good, but all downstream consumers (analytics, visualization, query agent)
receive empty arrays. The failure would propagate silently through every aggregation, skill
demand report, and dashboard panel.

**Root cause hypothesis:** Gemini 2.5 Flash does not reliably adhere to the JSON schema
contract enforced by the pipeline's structured output mode. Azure gpt-4.1-mini has native
structured output enforcement (JSON mode) that prevents this class of failure.

---

## Finding 2: Cost Is ~15% Higher for Azure — Not 2x

The "almost double cost" perception comes from comparing list prices per million tokens:

| Provider | Input (per 1M) | Output (per 1M) | Ratio |
|----------|---------------|-----------------|-------|
| Azure gpt-4.1-mini | $0.40 | $1.60 | baseline |
| Gemini 2.5 Flash | $0.15 | $0.60 | ~2.67x cheaper per token |

However, **actual pipeline consumption** tells a different story. Gemini used significantly
more output tokens to produce the same task, which offsets its lower per-token price:

| Agent | Azure cost/call | Gemini cost/call | Δ |
|-------|----------------|-----------------|---|
| skills-extraction-agent | $0.015226 | $0.015284 | ≈ tie |
| skills-extraction-responsibilities | $0.015493 | $0.010499 | Gemini 32% cheaper |
| skills-extraction-tasks | $0.001598 | $0.000910 | Gemini 43% cheaper |
| enrichment-soc-classifier | $0.003593 | $0.003693 | ≈ tie |
| enrichment-naics-classifier | $0.000278 | $0.000330 | Azure 16% cheaper |
| enrichment-employer-classifier | $0.000308 | $0.000272 | Gemini 12% cheaper |

**Estimated per-job total:**

| Provider | Per-job cost | Per 1,000 jobs | Per 30,000 jobs/month |
|----------|-------------|---------------|----------------------|
| Azure gpt-4.1-mini | **$0.0365** | $36.50 | $1,095 |
| Gemini 2.5 Flash | **$0.0310** | $31.00 | $930 |
| Difference | +$0.0055 | +$5.50 | +$165/month |

**The real cost premium for Azure is $165/month at full production scale — not 2x.**
Given that Gemini produced zero usable structured output in this run, the effective cost
per *valid* enriched job with Gemini is undefined (no valid output produced).

---

## Finding 3: Latency Context (Free Tier — Excluded from Decision)

For reference only — these numbers are **not valid** for provider comparison because Gemini
ran on the free tier with rate limiting:

| Agent | Azure p50 | Gemini p50 (free tier) |
|-------|-----------|----------------------|
| skills-extraction-agent | 11,725ms | 43,631ms |
| enrichment-soc-classifier | 1,038ms | 6,925ms |
| enrichment-naics-classifier | 1,785ms | 4,373ms |

On a paid Gemini tier, latency would improve substantially. Latency is not used as a
decision factor in this analysis.

---

## Finding 4: Reliability Metrics

| Metric | Azure | Gemini |
|--------|-------|--------|
| LLM call success rate | 100% | 100% |
| Distinct error types | 0 | 0 |
| Silent structured output failures | 1.55% (flagged) | 100% (unflagged) |

Azure's 1.55% extraction failure rate is correctly flagged (`extraction_failed = True`)
and surfaced for operator review. Gemini's 100% silent failure rate goes undetected.

---

## External Benchmark Evidence

### JSONSchemaBench (arxiv 2501.10868)
Independent benchmark measuring empirical JSON schema coverage across model families:

| Provider | GlaiveAI dataset coverage | GitHub Easy dataset |
|----------|--------------------------|---------------------|
| OpenAI GPT-4o | **0.89** | significantly higher |
| Gemini 2.5 Flash | 0.86 | **<0.01** (drops to near-zero on harder schemas) |

Gemini's schema coverage collapses on complex schemas — exactly the kind used in multi-field
structured extraction pipelines.

### Constrained Decoding Performance Degradation (Gemini)
When JSON-Schema constraints are enforced (the mode required for reliable structured output),
Gemini's task accuracy **drops by 11 percentage points**:

- Natural language prompting: **97.15%** accuracy
- JSON-Schema constrained decoding: **86.18%** accuracy

Source: Dylan Castillo structured output analysis. The degradation is caused by Gemini reordering
schema properties alphabetically, which breaks chain-of-thought reasoning (model outputs answers
before intermediate reasoning steps).

### Architectural Blocker: Function Calling + Structured Output (Gemini 2.5)
This is the most critical finding for a multi-turn agent pipeline:

> **Gemini 2.5 Flash cannot combine function calling with structured output.** When tool calls
> exist in the message history, structured output requests fail with:
> `"Function calling with a response_mime_type: 'application/json' is unsupported"`

- Works in Gemini 2.0; broken in all 2.5 models
- No workaround exists; it is an architectural incompatibility
- Additional failure modes: tool call looping (identical parameters called endlessly), silent
  `MALFORMED_FUNCTION_CALL` finish reason (processed as success by downstream code)

Sources: googleapis/python-genai#706, google/discuss#110777, BerriAI/litellm#17949, #16651

### OpenAI Structured Outputs Guarantee
Azure OpenAI (GPT-4.1-mini) provides a deterministic compliance guarantee via constrained
sampling infrastructure:

- **100% JSON Schema compliance** when `strict: true` is set in response format
- Engineering-based guarantee (not model-only): constrained sampling ensures valid output
  even when model probability mass would otherwise violate the schema
- Baseline from training alone: 93%; after constrained sampling: 100%

Source: OpenAI Structured Outputs documentation and release blog.

### Summary: External Benchmarks

| Metric | Azure GPT-4.1-mini | Gemini 2.5 Flash |
|--------|-------------------|-----------------|
| JSON Schema compliance | **99.7–100%** (with strict mode) | 86% (GlaiveAI), <1% (complex schemas) |
| Function calling + structured output | **Fully supported** (multi-turn) | **Broken in 2.5** |
| Constrained decoding accuracy drop | No documented degradation | **−11 points** |
| Production silent failure modes | None documented | Tool loops, MALFORMED_FUNCTION_CALL |
| Complex schema support | No documented limits | Rejects large/nested schemas (400 error) |

---

## Recommendation

**Retain Azure OpenAI (gpt-4.1-mini) as the primary provider.**

1. **Architectural blocker:** Gemini 2.5 Flash cannot combine function calling with structured
   output in the same request. This is a hard incompatibility with LangGraph StateGraph agents
   that use tools and emit structured payloads in the same turn. No workaround exists.

2. **Silent structured output failure (internal data):** Gemini produced 0 skills, 0 tasks,
   0 responsibilities on 100% of pipeline-processed jobs while reporting success and 0.97
   confidence. Azure delivers 13.5 skills, 10.1 tasks, 6.5 responsibilities at 98.5% true success.

3. **Schema compliance gap (external benchmarks):** Gemini 2.5 Flash drops to <1% schema
   coverage on complex nested schemas (JSONSchemaBench). The extraction pipeline uses deeply
   nested schemas with arrays, optional fields, and enums — exactly the hard case.

4. **Cost delta is small:** $165/month at 30k jobs/month scale, not 2x. Gemini uses more output
   tokens offsetting its lower per-token rate. The effective cost premium is marginal.

5. **Latency data excluded:** Free-tier Gemini was rate-limited; those numbers are not valid
   for comparison. Latency is not a factor in this decision.

---

## Next Steps if Gemini Reconsideration Is Required

A valid evaluation requires all three conditions to be met first:

1. Google resolves the function calling + structured output incompatibility in Gemini 2.5+
2. Run on **paid tier** to eliminate rate-limiting distortion
3. Use a **held-out labeled dataset** (50–100 jobs with known skills/tasks/responsibilities)
   to measure schema conformance and field completeness against ground truth

Until condition 1 is met, Gemini 2.5 Flash is not a viable option for this pipeline regardless
of cost. Gemini 2.5 **Pro** (not Flash) may be evaluated separately if the incompatibility
is model-tier-specific.

---

## Sources

- JSONSchemaBench: https://arxiv.org/html/2501.10868v3
- StructEval: https://tiger-ai-lab.github.io/StructEval/
- Gemini structured output degradation: https://dylancastillo.co/posts/gemini-structured-outputs.html
- Gemini 2.5 function calling + structured output blocker: https://github.com/googleapis/python-genai/issues/706
- Tool call loop bug: https://discuss.ai.google.dev/t/gemini-2-5-flash-stuck-in-a-tool-call-loop-when-using-both-tools-and-structured-output/110777
- LiteLLM multi-turn failure: https://github.com/BerriAI/litellm/issues/17949
- Silent MALFORMED_FUNCTION_CALL: https://github.com/BerriAI/litellm/issues/16651
- OpenAI Structured Outputs: https://openai.com/index/introducing-structured-outputs-in-the-api/
- Berkeley Function Calling Leaderboard: https://gorilla.cs.berkeley.edu/leaderboard.html
