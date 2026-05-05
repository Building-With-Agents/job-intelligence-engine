# Week 11 stakeholder demo — LaborPulse + JIE (Pair D)

Template aligned with curriculum Week 11 demo rehearsal. Sections §1, §3, §4, and §11 are filled for **Wed May 6** rehearsal; extend other sections during dry-run.

---

## §1 — Metadata

| Field | Value |
|--------|--------|
| **Date** | Wednesday, May 6 |
| **Platform** | Microsoft Teams |
| **Scheduled run time** | ~30 minutes |
| **Primary surface** | wfd-os LaborPulse (`/laborpulse`) backed by JIE `POST /analytics/query` on port 8000 |

---

## §2 — Audience & outcomes

*(Fill after kickoff: primary stakeholders, what “success” looks like for this segment.)*

---

## §3 — Intro (Pair D — middle segment)

Pair D bridges the raw Borderplex hiring signal in JIE to an instructor-friendly narrative in LaborPulse. In this segment we show five locked demo questions that span skill demand, curriculum design, tooling expectations, and specialized roles—exactly the questions workforce boards and training partners ask during program design. The UI stays simple on purpose: each answer brings evidence rows forward so stakeholders can see what the model grounded on, not just prose.

---

## §4 — Callout picks (calibration pair: questions #2 and #3)

| Demo # | Callout moment |
|--------|----------------|
| **#2 — AI agent developer training** | The sharpest “aha” is watching the answer reconcile **emerging agentic/tooling demand** with **foundational software practices** still showing up in postings—use it to stress that curriculum must span orchestration *and* delivery hygiene, not only model prompts. |
| **#3 — Cybersecurity analyst training** | The compelling beat is the **coherence between defensive operations vocabulary** (monitoring, IR, identity) and **employer-specific concentration** in the Borderplex evidence strip—ideal for arguing that localized syllabi can stay aligned to global frameworks without sounding generic. |

---

## §5 — Preconditions

*(DB seeded, JIE on :8000, wfd-os env `JIE_BASE_URL=http://localhost:8000`, API keys, etc.)*

---

## §6 — Live checklist

*(Step-by-step clicks; link `docs/runbooks/LABORPULSE_MANUAL_TEST_CHECKLIST.md` if useful.)*

---

## §7 — Fallbacks

*(What to say if latency spikes, LLM timeout, or tenant/key misconfiguration.)*

---

## §8 — Timing cues

*(Approximate minutes per section for the 30-minute slot.)*

---

## §9 — Backup materials

Screenshots and a short screen recording live under `docs/demo-backup/` (see §10 in runbook process).

---

## §10 — Q&A guardrails

*(Topics we do vs. do not promise on this demo path.)*

---

## §11 — Roles

| Person | Responsibility |
|--------|----------------|
| **Juan** | Drives the demo UI (LaborPulse at `localhost:3000/laborpulse`): session hygiene, pacing through the five locked questions, zooming evidence when stakeholders ask “where did that come from?” |
| **Enrique** | Narrates the through-line from JIE analytics to LaborPulse answers and owns live Q&A—especially calibration on questions **#2** and **#3** (training-program pair). |

---

## Locked demo questions (smoke reference)

1. What are the top IT skills Borderplex employers are hiring for right now?
2. What should a training program for AI agent developers look like given what Borderplex employers are hiring for right now?
3. What should a training program for cybersecurity analysts look like given what Borderplex employers are hiring for right now?
4. What tools and practices do Borderplex employers expect from workflow automation engineers?
5. What does an MLOps role look like in the Borderplex job market right now?

Automated API smoke: `python scripts/smoke/laborpulse/real_query.py` (with JIE running).
