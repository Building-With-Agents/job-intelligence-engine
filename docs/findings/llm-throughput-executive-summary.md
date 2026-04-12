# LLM Throughput & Cost Analysis — Executive Summary

**Prepared:** 2026-04-12  
**Audience:** Director / Decision-makers  
**Pipeline:** Job Intelligence Engine (JIE) — 8-agent ingestion-to-analytics pipeline

---

## What Are RPM and TPM, and Why Do They Matter?

Every time our pipeline analyzes a job posting, it makes **6 API calls** to a large language model (LLM). These calls extract skills, classify industry codes, and score quality. LLM providers enforce two rate limits that cap how fast we can process:

| Limit | What It Means | Why It Matters |
|-------|---------------|----------------|
| **RPM** (Requests Per Minute) | Maximum number of API calls allowed per minute | Directly caps how many jobs we can process per minute. At 6 calls/job, 190 RPM = max 31 jobs/min. |
| **TPM** (Tokens Per Minute) | Maximum "words" (tokens) processed per minute | Each call averages ~1,620 tokens. 1,000 jobs need ~9.7M tokens in 5 min = 1.94M TPM. |

**Our SLA target is 1,000 job postings processed in under 5 minutes.** That requires at least **1,200 RPM** and **~2M TPM** sustained for 5 minutes.

---

## Current State

**Data sources:** Azure Cost Management (billing ground truth) + 13,576 LLM generations in Langfuse (self-hosted observability, token-level detail).

### What Happens Per Job

Each job posting goes through two LLM-intensive stages:

| Stage | LLM Calls | Avg Tokens/Call | Avg Latency | Purpose |
|-------|-----------|-----------------|-------------|---------|
| **Skills Extraction** (3 calls) | Tasks, Responsibilities, Skills | ~1,970 tokens | 30-48s | Extract structured work intelligence from job descriptions |
| **Enrichment** (3 calls) | SOC code, NAICS code, Employer profile | ~1,086 tokens | 5-9s | Classify industry codes and build employer metadata |
| **Total per job** | **6 calls** | **~9,710 tokens** | — | — |

### Current Azure OpenAI Deployment

| Resource | Value |
|----------|-------|
| Model | gpt-4.1-mini (Standard deployment) |
| Allocated RPM | **190** |
| Allocated TPM | **190,000** |
| Current throughput | **~2 jobs/minute** (12.6 RPM observed — code bottleneck, not quota) |
| Max achievable (code fix only) | **~31 jobs/minute** (190 RPM / 6 calls) |

### Gemini 2.5 Flash (Free Tier)

| Resource | Value |
|----------|-------|
| RPM | **10** |
| Daily limit | **250 requests** |
| Usable for pipeline | No (far too low) |

### The Gap

| Metric | Current | Needed for SLA | Gap |
|--------|---------|----------------|-----|
| RPM | 190 | 1,200 | **6.3x increase** |
| TPM | 190,000 | 1,940,000 | **10.2x increase** |
| Jobs/min | ~2 (code bottleneck) → 31 (after refactor) | 200 | **6.5x after refactor** |

---

## Observed Cost Per Job

Two data sources provide cost estimates. **Azure Cost Management is the billing ground truth.**

### Azure Cost Management (ground truth — Apr 3-12, 2026)

Actual billed charges from the `resumeJobMatch` Azure OpenAI resource:

| Meter | Tokens | Billed Cost |
|-------|--------|-------------|
| gpt-4.1-mini input (regional) | 11,981,818 | $5.27 |
| gpt-4.1-mini output (regional) | 4,455,196 | $7.84 |
| gpt-4.1-mini cached input | 2,895,744 | $0.32 |
| text-embedding-3-small | 1,431,459 | $0.03 |
| **Total** | | **$13.46** |

$13.46 / 644 fully processed jobs = **$0.021/job** (2.1 cents)

| Scale | Azure Billed Cost |
|-------|-------------------|
| Per job | **$0.021** |
| Per 1,000 jobs | **$21** |
| Per 30,000 jobs/month | **$630/month** |

> Azure regional pricing is slightly above list ($0.44 vs $0.40/1M input, $1.76 vs $1.60/1M output). The total also includes embedding calls and cached input tokens not counted in the 6 LLM calls/job figure.

### Langfuse Token Analysis (cross-check — 13,576 generations, 1,533 jobs)

Token-based estimate using list pricing ($0.40/$1.60 per 1M tokens):

| Metric | Value |
|--------|-------|
| Avg input tokens/job | 8,041 |
| Avg output tokens/job | 1,669 |
| Avg total tokens/job | **9,710** |
| Estimated cost/job (list pricing) | **$0.0059** |

This underestimates actual cost because: (1) it uses list pricing, not regional; (2) it excludes embedding calls; (3) Langfuse token tracking underreports on some code paths.

**For budgeting purposes, use Azure billing: $21 per 1,000 jobs.**

---

## Three Deployment Options

Each option pairs specific LLM providers with the pipeline to balance cost, speed, and quality. All options include the same code refactoring (async enrichment, higher concurrency) — the difference is which providers handle the calls.

### Option A: Premium — All Azure OpenAI

| | Details |
|---|---|
| **Extraction provider** | Azure OpenAI gpt-4.1-mini (increased quota) |
| **Enrichment provider** | Azure OpenAI gpt-4.1-mini (shared quota) |
| **Required Azure change** | Increase capacity from 190 → 1,500 units (portal slider, within Tier 1 limit of 6,000) |
| **Projected speed** | ~1,000 jobs in **4-5 min** |
| **Cost per 1,000 jobs** | **$21** (Azure billing ground truth) |
| **Monthly (30k jobs)** | **$630** |
| **Quality** | Highest — single provider, proven structured output |
| **Complexity** | Lowest — no multi-provider routing |
| **Risk** | Quota increase may require approval (1-2 business days) |

### Option B: Mid-Tier — Azure Extraction + Gemini Enrichment

| | Details |
|---|---|
| **Extraction provider** | Azure OpenAI gpt-4.1-mini (moderate quota increase) |
| **Enrichment provider** | Google Gemini 2.5 Flash (Paid Tier 1 — 300 RPM) |
| **Required changes** | Azure: 190 → 450 units. Google Cloud: add billing to unlock Tier 1. |
| **Projected speed** | ~1,000 jobs in **5-6 min** |
| **Cost per 1,000 jobs** | **~$16** (Azure extraction ~$14 + Gemini enrichment ~$2) |
| **Monthly (30k jobs)** | **~$480** |
| **Quality** | High — Azure for complex extraction, Gemini for simpler classification |
| **Complexity** | Medium — multi-provider routing in code |
| **Risk** | Gemini has known structured output reliability issues (see azure-vs-gemini-structured-output.md). Enrichment classifiers use simpler schemas than extraction — may be viable, but needs validation. |

### Option C: Budget — DeepSeek + Groq

| | Details |
|---|---|
| **Extraction provider** | DeepSeek V3 (~500 RPM, $0.28/1M input) |
| **Enrichment provider** | Groq llama-3.3-70b (free tier, ~100 RPM) |
| **Fallback** | Azure OpenAI at current 190 RPM (no change needed) |
| **Required changes** | Create DeepSeek + Groq accounts. No Azure changes. |
| **Projected speed** | ~750 jobs in 5 min, **1,000 jobs in ~7 min** |
| **Cost per 1,000 jobs** | **~$6** (DeepSeek extraction ~$6 + Groq free $0.00) |
| **Monthly (30k jobs)** | **~$180** |
| **Quality** | Good — DeepSeek V3 comparable to gpt-4.1-mini for extraction; llama-3.3-70b strong for classification |
| **Complexity** | Highest — three providers, fallback logic |
| **Risk** | DeepSeek extraction quality needs validation. Groq free tier has daily limits (14,400 requests/day = max ~2,400 jobs/day for enrichment). |

---

## Side-by-Side Comparison

| | Premium | Mid-Tier | Budget |
|---|---------|----------|--------|
| **Est. time for 1,000 jobs** | 4-5 min | 5-6 min | 6-8 min |
| **Cost / 1,000 jobs** | $21 | ~$16 | ~$6 |
| **Cost / month (30k jobs)** | $630 | ~$480 | ~$180 |
| **Azure quota change** | 190 → 1,500 | 190 → 450 | None |
| **New accounts** | None | Google Cloud billing | DeepSeek + Groq |
| **Extraction quality** | Highest | Highest | Good (needs eval) |
| **Enrichment quality** | Highest | High | Good (needs eval) |
| **Code complexity** | Single provider | 2-provider routing | 3-provider routing + fallback |
| **Meets SLA?** | Yes | Close | No (6-8 min) |

---

## What the Code Refactor Delivers (All Options)

Independent of which tier is chosen, the pipeline refactoring delivers:

| Improvement | Current | After Refactor |
|-------------|---------|----------------|
| Enrichment execution | Serial (1 job at a time) | Parallel (30+ concurrent) |
| Enrichment per-job latency | ~20s (SOC + NAICS + employer sequential) | ~9s (all three concurrent) |
| Skills extraction concurrency | 5 concurrent jobs | 30-200 concurrent jobs |
| DB connection pool | 5 connections | 30-50 connections |
| Pipeline overlap | None (extract all, then enrich all) | Enrich batch N while extracting batch N+1 |
| Inter-batch delay | 10s sleep | 0s (rate limiting handled by semaphore) |
| **Effective throughput (Azure 190 RPM)** | **~2 jobs/min** | **~31 jobs/min (15x improvement)** |

The refactoring alone — with no quota or provider changes — increases throughput from **2 to 31 jobs/min** by actually utilizing the 190 RPM we already have.

---

## Recommendation

**Start with the code refactor** (shared across all tiers) to unlock the 15x throughput gain immediately. This is purely engineering work with no cost or account changes.

Then evaluate tier options based on:
1. **If 31 jobs/min is sufficient** for current volumes → stay on current Azure quota (no cost increase)
2. **If SLA of 1,000 in 5 min is required** → Option A (Premium) is simplest at $630/month; Option B (Mid-Tier) saves ~$150/month but adds Gemini structured output risk
3. **If budget is the primary constraint** → Option C (Budget) at ~$180/month, accepting 6-8 min processing time

---

## Appendix: Provider Rate Limits Reference

### Azure OpenAI gpt-4.1-mini (Standard Deployment)

| Tier | RPM | TPM |
|------|-----|-----|
| Current allocation | 190 | 190K |
| Default max | 2,700 | 450K |
| Tier 1 (request) | 6,000 | 6M |

### Google Gemini 2.5 Flash

| Tier | RPM | Cost (input/output per 1M tokens) |
|------|-----|-----------------------------------|
| Free | 10 | $0 (250 requests/day) |
| Paid Tier 1 (add billing) | 300 | $0.15 / $0.60 |
| Paid Tier 2 ($250 spend + 30d) | 1,000+ | $0.15 / $0.60 |

### DeepSeek V3

| Tier | RPM | Cost |
|------|-----|------|
| Free trial | ~500 | $5M free tokens (30 days) |
| Paid | ~500 | $0.28 / $0.42 per 1M tokens |

### Groq (llama-3.3-70b)

| Tier | RPM | Daily Limit | Cost |
|------|-----|-------------|------|
| Free | ~100 | 14,400 requests/day | $0 |
| Paid | Contact sales | Unlimited | TBD |

---

*Cost data from Azure Cost Management (billing ground truth, Apr 3-12 2026). Token-level analysis from Langfuse (13,576 generations, 1,533 jobs). Azure deployment limits from Azure CLI. External provider pricing as of April 2026.*
