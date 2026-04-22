# Employer Golden Questions

**Data verified:** 2026-04-22 (re-run after Q1/Q7/Q8/Q10 rewrites). Verification: `python scripts/verify_employer_golden_questions_data.py` on `scripts/pg-seed-data/fixtures` (set `PYTHON_DATABASE_URL` for live SQL). All questions apply the **`role_classification` guard** (exclude `N/A Not an IT role`) per **issue #197**. Join path: `dbo.companies` → `dbo.employer_profiles` (on `company_id`) → `dbo.job_postings` (on `company_id` / `employer_profile_id`).

**Re-run output (ORM-equivalent, seed fixtures) — 10/10 PASS:**

| Q | Status | Count (verification metric) |
|---|--------|------------------------------|
| Q1 | PASS | 2563 postings (Borderplex + IT guard) |
| Q2 | PASS | 114 distinct companies (agentic_era + AI tool text) |
| Q3 | PASS | 528 rows |
| Q4 | PASS | 32 rows |
| Q5 | PASS | 301 rows |
| Q6 | PASS | 294 rows |
| Q7 | PASS | 413 postings (non-academic + entry-level IT) |
| Q8 | PASS | 54 employers (≥3 distinct `role_classification` per company) |
| Q9 | PASS | 16 rows (legal + 14m) |
| Q10 | PASS | 434 postings (IT + `salary_min`/`salary_max` both set) |

| # | Question | Data verified | Notes |
|---|----------|---------------|-------|
| 1 | Which employers have the highest volume of open **IT** job postings in the Borderplex region, and which `role_classification` values are most common per employer? | **PASS** | No fixed recency window. `role_classification` ≠ `N/A Not an IT role` (#197). Rank: `COUNT(*)` per `company_id` with Borderplex filter (`borderplex_subregion` / `location` / `county`). |
| 2 | Which Borderplex employers have the highest **share** of postings mentioning AI tools (Copilot, LangChain, LLM APIs) in the **agentic_era** `temporal_period`? | **PASS** | `companies` + `employer_profiles` + `job_postings`. Share = per-employer `COUNT` / `SUM` with IT guard (#197). |
| 3 | List all IT postings from Borderplex **federal-contractor**-style employers (defense, aerospace, government-tech) that require a **security clearance** (as surfaced in text). | **PASS** | Heuristic on description + `employer_profiles.sector` + `companies` text; #197. |
| 4 | Which Borderplex **healthcare-IT** employers are hiring for clinical-data-analyst or health-informatics roles with **AI-adjacent** skill requirements? | **PASS** | Health + analyst + AI/ML in posting text. |
| 5 | Show all Borderplex **fintech and payments-technology** employer postings from the **post_gpt4** and **agentic_era** `temporal_period` values. | **PASS** | Fintech/NAICS 52* + period filter + IT guard. |
| 6 | Which Borderplex employers posted the most **data-engineer or data-scientist** roles in the last **12–14** months, and what share of those postings also signal **AI-adjacent** skills? | **PASS** | Dated on `date_posted` / `publish_date` where present; per-employer share query. |
| 7 | Which **non-academic** Borderplex employers (excluding names that match UTEP / NMSU / EPCC-style patterns) post the most **entry-level** IT roles—by `seniority_level` (e.g. junior, intern) or by entry-level cues in title/description? | **PASS** | Excludes academic `company_name` matches; #197; 413 matching postings in seed. |
| 8 | Which Borderplex employers post the **widest variety of IT `role_classification` values** (rank employers by `COUNT(DISTINCT role_classification)` for IT roles)? | **PASS** | **54** employers with ≥3 distinct `role_classification` in Borderplex+IT seed—no `first_appearance` schema required. |
| 9 | Show all Borderplex **legal-tech and e-discovery** employer postings, grouped by employer, in the last **~12–14** months. | **PASS** | Title/description keywords; date on posting fields. |
| 10 | Which Borderplex employers have the most IT job postings with **structured compensation** data (`salary_min` and `salary_max` both populated) for **salary benchmarking**? | **PASS** | Does **not** use `posting_freshness` / repost flags. |

1. Which employers have the highest volume of open **IT** job postings in the Borderplex region, and which `role_classification` values are most common per employer?
2. Which Borderplex employers have the highest **share** of postings mentioning AI tools (Copilot, LangChain, LLM APIs) in the **agentic_era** `temporal_period`?
3. List all IT postings from Borderplex **federal-contractor**-style employers (defense, aerospace, government-tech) that require a **security clearance** (as surfaced in text).
4. Which Borderplex **healthcare-IT** employers are hiring for clinical-data-analyst or health-informatics roles with **AI-adjacent** skill requirements?
5. Show all Borderplex **fintech and payments-technology** employer postings from the **post_gpt4** and **agentic_era** `temporal_period` values.
6. Which Borderplex employers posted the most **data-engineer or data-scientist** roles in the last **12–14** months, and what share of those postings also signal **AI-adjacent** skills?
7. Which **non-academic** Borderplex employers (excluding names that match UTEP / NMSU / EPCC-style patterns) post the most **entry-level** IT roles—by `seniority_level` (e.g. junior, intern) or by entry-level cues in title/description?
8. Which Borderplex employers post the **widest variety of IT `role_classification` values** (rank employers by `COUNT(DISTINCT role_classification)` for IT roles)?
9. Show all Borderplex **legal-tech and e-discovery** employer postings, grouped by employer, in the last **~12–14** months.
10. Which Borderplex employers have the most IT job postings with **structured compensation** data (`salary_min` and `salary_max` both populated) for **salary benchmarking**?
