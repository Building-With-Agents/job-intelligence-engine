# Demo Prep Findings — Pair D

**Branch:** `week-10/demo-prep-pair-d`  
**Sources:** Provisional 7-question demo set from JIE #341 / `eval/runs/findings-dev-verify-2026-05-01.md` (Step 9) and composite ranks in `eval/runs/ranking-dev-verify-2026-05-01.txt` (Gary, PR #350).  
**Harness:** `LLM_PROVIDER=mock QA_EVAL_OFFLINE=1 python -m eval.qa_eval --prompt-version v2-demo-set-pair-d --only-ids … --dry-run` (subset filter + dry-run; Langfuse skipped).

## Questions evaluated

| gq_id | Intent (golden) | Question (abridged) |
|-------|-----------------|----------------------|
| gq-030 | trend | In Borderplex cybersecurity postings, how has the mix of required skills evolved over the last 18 months — which are rising fastest and which are in decline? |
| gq-087 | workflow | What MLOps and model-lifecycle steps (training → deploy → monitor) appear in Borderplex IT postings, and which tasks in extracted_intelligence support answering "what happens in what order"? |
| gq-047 | geographic | Retrieve all El Paso, TX entry-level IT postings that have a published salary range, grouped by job family. |
| gq-037 | role_evolution | How has the seniority-level mix for Borderplex data engineer postings shifted between the early_genai and agentic_era periods — more senior, more junior, or more staff-level roles? |
| gq-014 | emergence | Which Borderplex IT roles had fewer than five postings before ChatGPT's release but have grown to 50+ postings in the agentic_era period, and what skills are driving that growth? |
| gq-073 | curriculum | What should a cybersecurity training program cover given current Borderplex cybersecurity postings — which certifications, tools, and AI-augmentation skills should be included? |
| gq-052 | comparison | How does Borderplex demand for Cloud Computing skills compare to Cybersecurity skills — which domain has more job postings? |

## Scores per question

Per-item composites below are taken from **`eval/runs/qa-dev-verify-2026-05-01.json`** (Gary’s 2026-05-01 run on SoT Postgres), i.e. the same ranking inputs as `ranking-dev-verify-2026-05-01.txt`. They are **not** re-scored in this workspace: the mock `--dry-run` here failed every item with `PYTHON_DATABASE_URL` unset, so no fresh intent/evidence metrics were produced locally.

| gq_id | Composite (4-metric mean) | intent_acc | ev_citation | conf_self | latency_sla |
|-------|---------------------------|------------|-------------|-----------|-------------|
| gq-030 | **1.000** | 1.0 | 1.00 | 1.0 | 1.0 |
| gq-087 | **0.996** | 1.0 | 0.985 | 1.0 | 1.0 |
| gq-047 | **0.985** | 1.0 | 0.941 | 1.0 | 1.0 |
| gq-037 | **0.980** | 1.0 | 0.918 | 1.0 | 1.0 |
| gq-014 | **0.968** | 1.0 | 0.870 | 1.0 | 1.0 |
| gq-073 | **0.968** | 1.0 | 0.873 | 1.0 | 1.0 |
| gq-052 | **0.952** | 1.0 | 0.806 | 1.0 | 1.0 |

## Any questions below 0.85 composite — flag for Gary

**None.** All seven provisional demo questions sit at **composite ≥ 0.952** on the 2026-05-01 run. Lowest evidence citation in the set is **gq-052** (≈0.81); still above the 0.80 “high-evidence” style bar used in the ranking notes.

## Recommendation for demo ordering

Use the **JIE #341 narrative order** (already ranked by composite in `ranking-dev-verify-2026-05-01.txt`):

1. **gq-030 (trend)** — strongest hook; perfect composite; skills mix over time.  
2. **gq-087 (workflow)** — concrete MLOps lifecycle; bridges tasks / postings.  
3. **gq-047 (geographic)** — named city, salary, entry-level — audience-friendly.  
4. **gq-037 (role_evolution)** — seniority mix shift; complements trend + workflow.  
5. **gq-014 (emergence)** — “&lt;5 to 50+” story; good mid-demo energy.  
6. **gq-073 (curriculum)** — Pair D signature intent; place after evidence-heavy intents.  
7. **gq-052 (comparison)** — clean A-vs-B close; shorter cognitive load after curriculum.

**Re-run after router/prompt fixes:** `findings-dev-verify-2026-05-01.md` notes employer and disruption cohort issues elsewhere; this seven-pack intentionally **omits employer** and (for that run) **omits disruption** as demo anchors. Re-score on a DB-backed run before locking stakeholder demo if #346/#347 land on `development`.

## Harness note

`eval/qa_eval.py` now accepts **`--only-ids gq-030,gq-087,…`** (comma-separated, order preserved) so the demo subset matches the command in the Week 10 runbook without temporary JSON copies.
