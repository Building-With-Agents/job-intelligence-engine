# Workflow Golden Questions

**Data verified:** 2026-04-22. Verification: `python scripts/verify_workflow_golden_questions_data.py` on `scripts/pg-seed-data/fixtures` (set `PYTHON_DATABASE_URL` for live SQL probes). All questions apply the **`role_classification` guard** (exclude `N/A Not an IT role`) per **issue #197**. Join path: `dbo.companies` → `dbo.employer_profiles` (on `company_id`) → `dbo.job_postings` → `dbo.normalized_jobs` → `dbo.extracted_intelligence` (use **`tasks`** and **`responsibilities`** for process / sequence signals).

**Re-run output (ORM-equivalent, seed fixtures) — 10/10 PASS:**

| Q | Status | `job_postings` (theme + Borderplex + IT + `employer_profiles`) | `extracted_intelligence` (with non-empty `tasks` or `responsibilities`) |
|---|--------|----------------------------------------------------------------|------------------------------------------------------------------------|
| Q1 | PASS | 466 | 345 |
| Q2 | PASS | 345 | 260 |
| Q3 | PASS | 731 | 616 |
| Q4 | PASS | 455 | 353 |
| Q5 | PASS | 567 | 439 |
| Q6 | PASS | 645 | 553 |
| Q7 | PASS | 104 | 82 |
| Q8 | PASS | 624 | 506 |
| Q9 | PASS | 497 | 399 |
| Q10 | PASS | 869 | 735 |

| # | Question | Status | Notes |
|---|----------|--------|-------|
| 1 | What is the **typical software delivery pipeline** (build, test, deploy) that Borderplex IT employers describe in active postings—e.g. CI/CD, release automation, and infrastructure-as-code—and how do **`extracted_intelligence` tasks** surface those steps? | **PASS** | Posting text: CI/CD, Jenkins, GitHub Actions, ArgoCD, Terraform, release/deployment pipeline. #197. |
| 2 | How do **Agile / Scrum** process expectations (sprints, backlog, stand-ups) show up across Borderplex IT roles, and what **sequence of responsibilities** appear in hiring posts for the same employers? | **PASS** | Keywords: agile, scrum, sprint, backlog, kanban, stand-up. |
| 3 | For **data engineering** roles, what **end-to-end data pipeline and orchestration** patterns (ETL, schedulers, workflow tools) do employers emphasize, and how are they ordered in `tasks` / `responsibilities`? | **PASS** | Keywords: data pipeline, ETL, Airflow, dbt, orchestration, workflow, job scheduler. |
| 4 | What is the **incident and on-call** operating model (DevOps / SRE) that large Borderplex employers describe—e.g. paging, runbooks, observability—and how does that map to role **tasks** in the corpus? | **PASS** | Keywords: DevOps, SRE, on-call, incident, pager, runbook, Grafana, Prometheus. |
| 5 | What does the **federal / defense IT hiring path** look like for roles mentioning **clearance** and **government** employers—e.g. prerequisite steps and security process language—and how is that reflected in posting text plus **employer_profiles** sector context? | **PASS** | Dual keyword: clearance/federal/government + IT role terms. |
| 6 | For **healthcare IT** and **EHR** work, what **implementation and clinical workflow** sequence do employers expect (EHR, HIPAA, clinical systems), and how do extractions capture related **responsibilities**? | **PASS** | EHR, EMR, Epic, HIPAA, clinical, FHIR, interoperability. |
| 7 | What **MLOps and model-lifecycle** steps (training → deploy → monitor) appear in Borderplex IT postings, and which **tasks** in `extracted_intelligence` support answering “what happens in what order”? | **PASS** | MLOps, model deploy/registry/serving/monitoring, Kubeflow, SageMaker. |
| 8 | How do employers describe **cloud migration and platform** programs (phases, landing zones, infrastructure) for IT roles, and what **ordered activities** can be read from combined posting + extraction data? | **PASS** | Cloud migration/architect/strategy, infrastructure, landing zone. |
| 9 | What **IT support and escalation** path (help desk, tiers, service desk) do employers document, and how does that compare across **employer_profiles** in the Borderplex? | **PASS** | Help desk, service desk, tier, escalation. |
| 10 | How often do postings require **cross-functional coordination** (stakeholders, matrixed teams) as an explicit part of the job—can we rank employers by volume and describe the **process handoffs** from `tasks` / `responsibilities`? | **PASS** | cross-functional, stakeholder, across teams, coordinate. |

1. What is the **typical software delivery pipeline** (build, test, deploy) that Borderplex IT employers describe in active postings—e.g. CI/CD, release automation, and infrastructure-as-code—and how do **`extracted_intelligence` tasks** surface those steps?
2. How do **Agile / Scrum** process expectations (sprints, backlog, stand-ups) show up across Borderplex IT roles, and what **sequence of responsibilities** appear in hiring posts for the same employers?
3. For **data engineering** roles, what **end-to-end data pipeline and orchestration** patterns (ETL, schedulers, workflow tools) do employers emphasize, and how are they ordered in `tasks` / `responsibilities`?
4. What is the **incident and on-call** operating model (DevOps / SRE) that large Borderplex employers describe—e.g. paging, runbooks, observability—and how does that map to role **tasks** in the corpus?
5. What does the **federal / defense IT hiring path** look like for roles mentioning **clearance** and **government** employers—e.g. prerequisite steps and security process language—and how is that reflected in posting text plus **employer_profiles** sector context?
6. For **healthcare IT** and **EHR** work, what **implementation and clinical workflow** sequence do employers expect (EHR, HIPAA, clinical systems), and how do extractions capture related **responsibilities**?
7. What **MLOps and model-lifecycle** steps (training → deploy → monitor) appear in Borderplex IT postings, and which **tasks** in `extracted_intelligence` support answering “what happens in what order”?
8. How do employers describe **cloud migration and platform** programs (phases, landing zones, infrastructure) for IT roles, and what **ordered activities** can be read from combined posting + extraction data?
9. What **IT support and escalation** path (help desk, tiers, service desk) do employers document, and how does that compare across **employer_profiles** in the Borderplex?
10. How often do postings require **cross-functional coordination** (stakeholders, matrixed teams) as an explicit part of the job—can we rank employers by volume and describe the **process handoffs** from `tasks` / `responsibilities`?
