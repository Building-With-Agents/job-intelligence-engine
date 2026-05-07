# DEMO_SCRIPT.md — May 6 Stakeholder Demo

> **Owner:** Pair D (Juan + Enrique) — middle section + handoffs
> **Pulls from:** [IMP-033](./IMP-033-wfd-os-local-setup-and-jie-qa-wiring-juan-enrique-reading.md) (local stack) · [IMP-034](./IMP-034-may-6-demo-question-guide-juan-enrique-reading.md) (5 questions, verbatim answers, narration angles)
> **Status:** TEMPLATE — iterate as info lands. See §0 for the placeholder convention.

---

## §0 — How To Use This Template

This file is the working script for Pair D's section of the May 6 demo. It is **not** the whole demo — Gary opens and closes from a separate Waifinder capability deck. The structure of the day:

```
GARY OPENS (Waifinder deck — Issues / meta problem)         ~5 min
    ↓ hand off to Juan + Enrique  ······························  §2
PAIR D DEMOS (this script — intro + 5 questions + metrics)  ~20 min
    ↓ hand back to Gary  ·······································  §7
GARY CLOSES (Waifinder deck — Benefits + 8 patterns + offer) ~5-7 min
```

### Conventions used in this file

```markdown
<!-- TBD owner=pair-c what="per-intent eval numbers from final run" deadline="Tue 5/5 AM" -->
<!-- LOCKED source="IMP-034 Question 2 section" -->
```

These are HTML comments — invisible when GitHub renders the markdown, but greppable for the "remaining work" view:

```bash
grep -E "<!-- (TBD|LOCKED) " lesson-framework/week-11/readings/DEMO_SCRIPT.md
```

### Iteration cadence

- **Update before each Pair D work session** — pull info from Pair C / Pair A+B / Gary as it lands.
- **Freeze rule:** after Tuesday 5/5 rehearsal, do **not** edit content in §3, §4, §5 (the live demo body). Only fallback content (§9) and roles (§11) stay editable through Wednesday morning.

### Branding rule (per `marketing/README.md`)

- **Waifinder Consulting** (never CFA, Computing for All, Building with Agents)
- **Engineering team** (never students, participants, trainees, cohort)
- This applies to anything you say out loud or anything that ends up on a slide. Internal notes in this file can use any term — but the moment you read it aloud, switch to the prospect-facing voice.

### What's NOT in this file

- Gary's Waifinder deck open + close — that lives at `marketing/waifinder-capability-deck/` (Phase 2 deliverable)
- Verbatim demo answers — those live in [IMP-034](./IMP-034-may-6-demo-question-guide-juan-enrique-reading.md). Link, don't restate.
- Local-stack setup — that lives in [IMP-033](./IMP-033-wfd-os-local-setup-and-jie-qa-wiring-juan-enrique-reading.md). Link, don't restate.

---

## §1 — Demo Metadata

| Field              | Value                                                                                                       |
| ------------------ | ----------------------------------------------------------------------------------------------------------- |
| Date               | **Wed May 6 2026** (locked)                                                                                 |
| Time               | 1:00pm PST                                                                                                  |
| Platform           | **Microsoft Teams**                                                                                         |
| Audience           | Borderplex                                                                                                  |
| Presenter rotation | Juan: §3 intro + Q1 + Q4. Enrique: Q2 + Q3 + Q5. Backup-switcher: Juan.                                     |
| Backup screenshots | `agents/docs/demo-assets/question_1.png` through `question_5.png` captured from local LaborPulse rehearsal. |

---

## §2 — Hand-Off IN: From Gary's Open

After Gary delivers the Waifinder deck open (Issues + meta problem framing + bridge slide), he cues Pair D:

> **Gary:** _"And now Juan and Enrique will walk you through what we built."_

Pair D's response — keep it short and confident, don't restate Gary's framing:

> **Pair D (Juan or Enrique):** _"Thanks Gary. So what you're about to see is LaborPulse — a Q&A assistant grounded in live job posting data from the Borderplex region. We'll ask it five questions, narrate what's happening, and at the end Bryan and Emilio will walk you through the quality numbers."_

<!-- TBD owner=juan & enrique+gary what="refine the cue/response after Tue rehearsal — should feel natural, not staged" deadline="Tue 5/5 EOD" -->

---

## §3 — Intro to LaborPulse (3 min)

Three short beats. Gary already framed the meta problem in his open, so Pair D's intro is shorter than what Week 11 lesson framework originally specified (was 5 min; now 3 min).

### Beat 1 — Restate the problem in your own words (~45 sec)

Don't repeat Gary verbatim. Show ownership of the problem from the engineering side:

> _"Workforce-development directors need real-time labor market intelligence. Quarterly reports are too late. The data exists — job postings, employer profiles, skills demand — but nobody's structuring it in a way you can ask questions of. That's the gap LaborPulse fills."_

<!-- TBD owner=juan & enrique what="rephrase in your own voice — this is just a starting point" -->

### Beat 2 — Solution overview (~60 sec)

> _"LaborPulse is a Q&A interface backed by an 8-agent pipeline that ingests, normalizes, extracts, and analyzes job postings from the Borderplex region. You ask a question in plain English; the system classifies the intent, routes to the right data, runs the SQL, synthesizes a grounded answer with evidence citations, and offers follow-up questions."_

### Beat 3 — Architecture in plain language (~75 sec)

Don't go deep — just enough that the audience understands what "agentic" means here:

> _"Each agent has one responsibility — ingestion, normalization, intelligence extraction, enrichment, analytics, visualization, orchestration. They communicate through typed events, never direct calls. Every LLM call is logged with cost, latency, and the prompt used. Every answer cites the data it came from. That's how we keep an AI system trustworthy."_

<!-- LOCKED source="lesson-framework/week-11-demo-polish-and-stakeholder-demo.md lines 47-48 (Demo Script structure)" -->

---

## §4 — LaborPulse Live Demo (15 min) — With Transferable-Skill Callouts

Five questions, ~3 min each (~15 sec answer render + ~2:45 narration + transition). All five questions are **locked** per IMP-034 — do not improvise prompts on the day.

For each question:

- **Verbatim prompt** — copy-paste from IMP-034 (link below)
- **Narration angle** — what to say while the answer renders (from IMP-034)
- **What to avoid** — foot-guns from IMP-034
- **Callout** — pick **one** from the bank in §4.6 to drop in naturally (one sentence, said once during the question)
- **Fallback line** — what to say if the live answer is wrong-shaped or hangs

### §4.1 — Question 1 (trend, ~3 min)

> **Prompt:** _"What are the most prominent skills showing up in IT job postings right now?"_
>
> Full guide: [IMP-034 Question 1](./IMP-034-may-6-demo-question-guide-juan-enrique-reading.md#question-1--what-are-the-most-prominent-skills-showing-up-in-it-job-postings-right-now)

- **Narration angle:** Top 3-4 skills, highlight the non-obvious finding (communication / code review / collab outrank Python).
- **What to avoid:** Don't claim trend direction; this is one week of data.
- **Callout (pick 1):** From §4.6 — "trend / aggregates" bank.
- **Fallback:** _"The live system seems slow right now — let me show you what we captured during testing."_

### §4.2 — Question 2 (curriculum, **high confidence** — start of calibration pair, ~3 min)

> **Prompt:** _"What should an AI agent developer training program cover given current postings — which LLM frameworks, orchestration patterns, and integration skills are in demand?"_
>
> Full guide: [IMP-034 Question 2](./IMP-034-may-6-demo-question-guide-juan-enrique-reading.md#question-2--ai-agent-developer-training-program--curriculum--high-calibration-showcase-rich-data-half)

- **Narration angle:** Open-ended question, system surfaces 8 modules + named employers. Percentages are deterministic SQL aggregates.
- **What to avoid:** Don't claim this IS the curriculum to use — it's the system's read of current data.
- **Callout (pick 1):** From §4.6 — "calibration" bank. Save the strongest calibration line for Question 3 transition.
- **Fallback:** Pre-captured verbatim answer in IMP-034 "Question 2" section.

### §4.3 — Question 3 (curriculum, **low confidence** — second half of calibration pair, ~3 min)

> **Prompt:** _"What should a cybersecurity training program cover given current job postings?"_
>
> Full guide: [IMP-034 Question 3](./IMP-034-may-6-demo-question-guide-juan-enrique-reading.md#question-3--cybersecurity-training-program--curriculum--low-calibration-showcase-thin-data-half)

- **Narration angle:** **Run back-to-back with Question 2 — do not let any other question sit between them.** Same intent. Same code path. Different data density. The system tells you which is which.
- **What to avoid:** Don't try to defend "only 2 modules" as adequate cybersec training. The honest framing IS the demo.
- **Callout (pick 1):** From §4.6 — "calibration" bank. This is THE moment for the strongest calibration line.
- **Fallback:** Pre-captured verbatim answer in IMP-034 "Question 3" section.

### §4.4 — Question 4 (workflow, ~3 min)

> **Prompt:** _"What CI/CD, release automation, and infrastructure-as-code skills appear in active IT job postings, and what does the typical build / test / deploy sequence look like?"_
>
> Full guide: [IMP-034 Question 4](./IMP-034-may-6-demo-question-guide-juan-enrique-reading.md#question-4--cicd-release-automation-infrastructure-as-code--workflow--medium)

- **Narration angle:** Real tools cited (Terraform, K8s, AWS, Azure). Honest hedge that postings don't enumerate sequence steps.
- **What to avoid:** **The phrase "IT job postings" (with "job") is required** — "IT postings" alone fails the role-extractor. Never name internal tables (`extracted_intelligence`, `canonical_roles`) on stage.
- **Callout (pick 1):** From §4.6 — "guardrails" bank.
- **Fallback:** Pre-captured verbatim answer in IMP-034 "Question 4" section.

### §4.5 — Question 5 (workflow, ~3 min)

> **Prompt:** _"What MLOps and model-lifecycle activities (training, deployment, monitoring) appear in IT job postings, and what does the typical training → deploy → monitor sequence look like?"_
>
> Full guide: [IMP-034 Question 5](./IMP-034-may-6-demo-question-guide-juan-enrique-reading.md#question-5--mlops-and-model-lifecycle-activities--workflow--medium)

- **Narration angle:** Same shape as Question 4 but for AI/ML roles. Names AI Solutions Architect + AI Infrastructure Engineering Architect 3/3 runs.
- **What to avoid:** Same as Question 4 — "IT job postings" required, no internal table names.
- **Callout (pick 1):** From §4.6 — "guardrails" or "discipline" bank.
- **Fallback:** Pre-captured verbatim answer in IMP-034 "Question 5" section.

### §4.6 — Transferable-Skill Callout Bank

Pick **one** callout per question — eight options across four buckets. You don't have to use all eight in a single demo. Practice the ones that feel natural.

#### Trend / aggregates (use on Question 1)

1. _"The numbers we just cited — communication outranking Python — those are deterministic SQL aggregates. The LLM doesn't get to make them up. That separation is the trust signal in any agentic system."_
2. _"Notice the system is honest about the time window — it says 'the most recent week' instead of claiming a long-term trend. That's evidence-citation discipline, and it's a pattern any team building agents needs."_

#### Calibration (use on Question 2 or Question 3 — save the strongest for Q3)

3. _"Same code path. Different data density. The system tells you which is which — that's confidence calibration, and it's the difference between an AI you can trust and one you can't."_
4. _"You'll notice the confidence label changed from 'high' to 'low' between these two questions. That's not the LLM being humble — that's a deterministic taper based on how many employers and skills the data actually has. Calibration like this is the single biggest trust-builder in agentic systems."_

#### Guardrails (use on Question 4 or Question 5)

5. _"Notice the system says 'the data doesn't include that level of detail' on the sequence question. That's a guardrail we built — synthesis only cites what evidence supports. Anti-hallucination by construction."_
6. _"The system's saying 'I don't know' on something it could easily make up. That kind of restraint takes engineering — it's not the model's default behavior. Every team building production agents has to design for it."_

#### Discipline (use anywhere — strongest as a closing line on §4)

7. _"The eval discipline that gets you to this kind of answer — we caught our own answer-key leakage in the test harness two days ago and removed it. Real numbers came in lower than the leaky ones. That kind of self-audit is what production AI looks like."_
8. _"Every LLM call you just saw was logged — prompt, model, latency, cost. We can tell you what this demo cost in inference dollars. That observability is non-negotiable when AI is in your operations."_

<!-- TBD owner=juan & enrique what="pick your 5 callouts from this bank — one per question — and rehearse them out loud Tue PM" deadline="Tue 5/5 rehearsal" -->

---

## §5 — Metrics Review (3 min)

Pair C presents. Slide-ready format expected by the rehearsal.

| Metric                                                                               | Value                                                                                                                        |
| ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| Per-intent breakdown (trend, curriculum, workflow, geographic, comparison, employer) | <!-- TBD owner=pair-c what="per-intent composite scores from final eval against locked prompts" deadline="Tue 5/5 AM" -->    |
| Improvement trajectory                                                               | <!-- TBD owner=pair-c what="v1 baseline → v2 → v2.3 → final (post-PR#358)" deadline="Tue 5/5 AM" -->                         |
| Evidence citation rate                                                               | <!-- TBD owner=pair-c what="% of answers with citations · % accurate" deadline="Tue 5/5 AM" -->                              |
| Latency p50 / p95                                                                    | <!-- TBD owner=pair-c what="report median across warm runs if cold-start blows the < 500ms target" deadline="Tue 5/5 AM" --> |
| Total demo run cost                                                                  | <!-- TBD owner=pair-c what="sum of cost per question — should be ≈ $0.07 per IMP-034" deadline="Tue 5/5 AM" -->              |

**Pair C narration discipline (per Day 2 deck slide 8):**

- Reproducibility beats peak. If runs vary, report the range.
- Put the weakest intent on the slide too. Stakeholders respect honesty more than perfection.
- Frame the trajectory as _discipline_, not just numbers — "we caught a leaky baseline, the real numbers came in lower, here they are."

<!-- LOCKED source="instructor-use/week-11/presentations/presentation-outline-week-11-day-02-monday-meetup.md slide 8 (Pair C — Final Eval Run + Quality Metrics)" -->

---

## §6 — Q&A — Hand-Off to Pair A + B (5 min)

> **Priority shift (Gary, post-merge iteration):** The transferable-skills story from the engineer's perspective is a higher-priority sell than JIE's capabilities. Lead with what _you_ learned that travels to other orgs — not what the system does. The audience came to see a workforce-intelligence demo; they leave hearing engineers who could build the same kind of system for any org. That's what makes Gary's close (Slide 7 — the offer) land.

Each answer **under 90 seconds**. Two tiers of questions.

### Tier 1 — Lead with these (engineer-perspective, transferable-skills)

These are questions you (Pair A + B) raise yourselves if the audience opens with a generic "tell me about your experience." If the audience asks something else, pivot toward one of these in your closing line. Three for Pair A, two for Pair B (or whichever split feels natural — these are voluntary leads, not assigned).

| #   | Question                                                                                                                        | Engineer-perspective answer angle                                                                                                                                                                                                                                                                                                                                    |
| --- | ------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T1  | "What did you learn building this that you'd take to your next agentic system?"                                                 | Pick one pattern from §4.6 callout bank you actually internalized — confidence calibration, evidence citation, code-first audits, eval discipline. "The biggest one for me was X — before this project I'd have just trusted the model output; now I instinctively ask 'what's the evidence behind this?'"                                                           |
| T2  | "What did 'starting with the operational problem first' look like in practice?"                                                 | Concrete: when a question kept failing the eval, the fix wasn't always the prompt — sometimes it was that we'd misunderstood the operational question. The discipline is keeping the operational problem in front of you when you're tempted to just iterate the model.                                                                                              |
| T3  | "What discipline would you teach another team that you wish you'd had on day one?"                                              | Strongest single answer: eval discipline — including auditing your own eval harness. Tell the PR#358 leakage story from the engineer side: "we had a curve that looked too good. Two days before this demo, we found out our test mock was reading the answer key. Real numbers came in lower. We shipped the fix. Catching that is the discipline I'd teach first." |
| T4  | "How do you build trust into an AI system — what did you learn about that?"                                                     | Two layers: structural (evidence citation, confidence calibration — the LLM doesn't get to make up the numbers) + procedural (we audit our own evals). "Trust isn't a feature you add at the end — it's how you architect from day one."                                                                                                                             |
| T5  | "If you were starting an agentic project at another org tomorrow, what would you do differently from how you started this one?" | Honest: "I'd lock the eval harness before iterating prompts — we caught a leakage late and that's avoidable. I'd also start with the operational problem statement on the wall, not the architecture diagram."                                                                                                                                                       |

### Tier 2 — Capability questions (answer if asked, but always pivot to a transferable lesson)

These are Ritu's predicted set. They will probably come up. Answer briefly, then **end every answer with the transferable lesson** — that's the pivot move that keeps the room thinking about you, not the system.

| #   | Question                             | Brief capability answer                                                                                             | Transferable-lesson pivot                                                                                                                                                        |
| --- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | How does it handle a new job source? | Adapter pattern — each source implements the same Ingestion contract; new sources are isolated additions.           | "And the lesson there is: typed contracts at the boundary make systems extensible. Any team building agents needs that."                                                         |
| 2   | What if the AI is wrong?             | Confidence calibration (you saw it live in the curriculum pair). Evidence citation. Self-audit of the eval harness. | "What we learned is trust isn't one feature — it's three layers, and you have to architect for all of them."                                                                     |
| 3   | How much does it cost to run?        | About $0.07 per full demo run-through. Per-call logging gives you the breakdown.                                    | "The lesson: cost discipline isn't optional with AI in operations. You log every call, or you discover the bill at the end of the month."                                        |
| 4   | What's next? (Phase 2)               | Defer to Gary's close (Slide 8 covers Phase 2 vision).                                                              | "The pattern we'd carry forward is: ship discipline first, automation second. We did that here, and it's portable."                                                              |
| 5   | Can I use it for my region?          | Yes architecturally. Current data is Borderplex; new region = data work, not code.                                  | "The lesson: tenant-scoped resolution at the architecture level lets you serve multiple regions without forking. That pattern travels."                                          |
| 6   | How current is the data?             | Ingestion schedule; latest week 2026-04-20.                                                                         | "The lesson: temporal periods as first-class data citizens. The system tells you exactly which window an answer covers — that's an honesty pattern any analytics product needs." |

### The pivot pattern

Every answer — Tier 1 or Tier 2 — ends with one of these phrasings:

- _"...and what travels from that is..."_
- _"...the lesson I'd teach another team is..."_
- _"...the pattern that's portable is..."_
- _"...what we learned that's not LaborPulse-specific is..."_

That's the through-line that connects your answer to Gary's Slide 5 (the 8 transferable patterns). When Gary lands the offer on Slide 7, the audience is already hearing it from you.

### Voice discipline

- **Use "I" / "we" — first person engineering perspective.** Not "the system" or "the team." You did this work; own it in voice.
- **Concrete examples beat abstract claims.** "We caught the leakage two days ago" beats "we have eval discipline."
- **Honest framing earns trust.** "I'd do X differently next time" makes you more credible than "everything went perfectly."
- **Stay engineer-voice, not salesperson-voice.** The selling is Gary's job on the close. Your job is to be the proof that the patterns travel.

### Owner assignments

Tier 1 (volunteer leads) — split however the pair prefers; nothing is locked:

| Tier 1 Q | Lead                           |
| -------- | ------------------------------ |
| T1       | Fabian                         |
| T2       | Gary - move to sandwich slides |
| T3       | Angel                          |
| T4       | Nestor                         |
| T5       | Fatima                         |

Tier 2 (3/3 split if asked) — same split as the original 6 questions, kept here for continuity:

| Tier 2 Q | Owner  |
| -------- | ------ |
| 1        | Fabian |
| 2        | Nestor |
| 3        | Angel  |
| 4        | Fatima |
| 5        | Fabian |
| 6        | Nestor |

### Reference runbooks

- [WEEK-11-stakeholder-qa-prep-angel-fabian-runbook.md](../WEEK-11-stakeholder-qa-prep-angel-fabian-runbook.md)
- [WEEK-11-stakeholder-qa-prep-fatima-nestor-runbook.md](../WEEK-11-stakeholder-qa-prep-fatima-nestor-runbook.md)

> **Note for Pair A + B:** the runbooks were written against the original Tier 2 capability framing. Use them for the underlying technical content, but the demo-day delivery uses the Tier 1 + pivot pattern above. The runbooks themselves are worth a follow-up update post-demo to incorporate the priority shift.

**Format reminder:** real cite → honest limitation → concrete transferable lesson. The third beat is the new emphasis.

<!-- LOCKED source="IMP-034 Q&A buffer + instructor-use/week-11/presentations/presentation-outline-week-11-day-02-monday-meetup.md slide 7" -->
<!-- LOCKED source="Gary's post-merge iteration 2026-05-04: transferable-skills priority over JIE capabilities" -->

---

## §7 — Hand-Off OUT: Back to Gary

After Pair A + B finish their last Q&A answer, Pair D bridges back:

> **Pair D (Juan or Enrique):** _"And we'll hand it back to Gary to wrap up."_

Then Gary takes over with the Waifinder deck close (Slides 4-8: What Just Happened · 8 Patterns · Case Study · Offer · Contact).

<!-- TBD owner=pair-d+gary what="refine the cue tone after Tue rehearsal — should feel like a natural pass, not awkward" deadline="Tue 5/5 EOD" -->

---

## §8 — Pre-Demo Morning Checklist

Cribbed from [IMP-034 Pre-demo checklist (May 6 morning)](./IMP-034-may-6-demo-question-guide-juan-enrique-reading.md#pre-demo-checklist-may-6-morning). The full list lives there — don't duplicate.

JIE-repo-specific additions:

- [ ] Pull latest on JIE: `cd ~/repos/cfa-projects/building-with-agents-curriculum/job-intelligence-engine && git checkout development && git pull` — confirm baseline includes commit `369315c+` (the PR#355 hotfix) and PR#358 leakage-removal merge
- [ ] Restart JIE FastAPI: `python scripts/run_analytics_api.py` — listening on `:8000`
- [ ] Pull latest on wfd-os: `cd ../wfd-os && git checkout demo/week-11-snapshot && git pull` — snapshot commit `3fcc7d0`
- [ ] Restart wfd-os: `honcho start portal laborpulse-api` from wfd-os repo root
- [ ] Auth: `python scripts/dev-login.py` to set the `wfdos_session` cookie

Local-stack setup details: [IMP-033](./IMP-033-wfd-os-local-setup-and-jie-qa-wiring-juan-enrique-reading.md)

---

## §9 — Backup Plan

Cribbed from [IMP-034 Fallback plan](./IMP-034-may-6-demo-question-guide-juan-enrique-reading.md#fallback-plan-if-a-question-fails-on-demo-day). The full plan lives there — don't duplicate.

**The line if a live query fails:** _"Let me show you what we captured during testing."_ — no apology, no debugging live.

**Backup materials paths:**

| Asset                                             | Path                                                                                                                                                                                       |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Pre-captured verbatim responses (all 5 questions) | questions are [here](https://github.com/Building-With-Agents/curriculum/blob/main/lesson-framework/week-11/readings/IMP-034-may-6-demo-question-guide-juan-enrique-reading.md) in the repo |
| Screenshots of all 5 demo answers                 | <!-- TBD owner=pair-d what="zipped folder dropped in discord with one screenshot per question" deadline="Tue 5/5 lunch" -->                                                                |

**Backup-switch drill:** Practice this at Tuesday's rehearsal. Gary will simulate a live failure mid-demo. Pair D switches to the backup materials within 10 seconds, no apology.

---

## §10 — Capture-and-Classify Template

The note-taker fills this **during** the demo (not after — memory is unreliable). Note-taker assignment in §11.

| #   | Audience question / comment (verbatim) | Classification                                | Pair owner for follow-up    |
| --- | -------------------------------------- | --------------------------------------------- | --------------------------- |
| 1   | <!-- fill during demo -->              | prompt fix · data fix · UI fix · out of scope | <!-- assign during demo --> |
| 2   | <!-- fill during demo -->              | prompt fix · data fix · UI fix · out of scope | <!-- assign during demo --> |
| 3   | <!-- fill during demo -->              | prompt fix · data fix · UI fix · out of scope | <!-- assign during demo --> |
| 4   | <!-- fill during demo -->              | prompt fix · data fix · UI fix · out of scope | <!-- assign during demo --> |
| 5   | <!-- fill during demo -->              | prompt fix · data fix · UI fix · out of scope | <!-- assign during demo --> |

Add rows as needed. Items that LaborPulse handled well validate the golden corpus; items that got weak answers go into the corpus next week.

<!-- LOCKED source="instructor-use/week-11/presentations/presentation-outline-week-11-day-02-monday-meetup.md slide 11" -->

---

## §11 — Roles + Handoffs

Visual handoff diagram (read top to bottom):

```
┌─────────────────────────────────────────────────────────────┐
│  GARY — Waifinder deck open (Slides 1-3)            ~5 min  │
│  Issues frame · meta problem · bridge to live demo          │
└─────────────────────────────────────────────────────────────┘
              ↓ §2 hand-off in
┌─────────────────────────────────────────────────────────────┐
│  PAIR D — Intro to LaborPulse (§3)                  ~3 min  │
│  Juan or Enrique narrates                                   │
└─────────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────────┐
│  PAIR D — Live demo, 5 questions (§4)              ~15 min  │
│  Q1: ___________  Q2: ___________  Q3: ___________          │
│  Q4: ___________  Q5: ___________                           │
│  + 1 transferable-skill callout per question                │
└─────────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────────┐
│  PAIR C — Metrics review (§5)                       ~3 min  │
│  Bryan / Emilio                                             │
└─────────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────────┐
│  PAIR A + B — Q&A (§6)                              ~5 min  │
│  6 questions split 3/3 — under 90 sec each                  │
└─────────────────────────────────────────────────────────────┘
              ↓ §7 hand-off out
┌─────────────────────────────────────────────────────────────┐
│  GARY — Waifinder deck close (Slides 4-8)         ~5-7 min  │
│  Discipline · 8 patterns · case study · offer · contact     │
└─────────────────────────────────────────────────────────────┘

Throughout:
  Note-taker:        Pair A or B (§10 capture-and-classify)
  Backup-switcher:   Pair D
  Discord traffic:   Gary
```

### Role assignments

| Role                          | Owner                                                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------ | --- |
| Open / close (Waifinder deck) | Gary                                                                                                                     |
| Intro narration (§3)          | Juan                                                                                                                     |
| Question 1 narration          | Juan                                                                                                                     |
| Question 2 narration          | Enrique                                                                                                                  |
| Question 3 narration          | Enrique                                                                                                                  |
| Question 4 narration          | Juan                                                                                                                     |
| Question 5 narration          | Enrique                                                                                                                  | >   |
| Metrics presenter (§5)        | **Bryan** (HeatMap, latency (ingest-process vs. Q&A)) **Emilio** (improvement overtime)                                  |
| Q&A 1, 2, 3                   | Fabian, Nestor, Angel                                                                                                    |
| Q&A 4, 5, 6                   | Fatima, Fabian, Nestor                                                                                                   |
| Note-taker (verbatim)         | Fatima                                                                                                                   |
| Backup-switcher               | <!-- TBD owner=juan & enrique pair-d what="who hits the backup material if a live query fails" deadline="Tue 5/5 AM" --> |
| Discord traffic / Teams link  | Gary                                                                                                                     |

---

## Appendix — Source Files

- [IMP-033 — wfd-os local setup + JIE Q&A wiring](./IMP-033-wfd-os-local-setup-and-jie-qa-wiring-juan-enrique-reading.md)
- [IMP-034 — May 6 demo question guide (5 locked questions, verbatim answers, narration)](./IMP-034-may-6-demo-question-guide-juan-enrique-reading.md)
- [Week 11 lesson framework](../../week-11-demo-polish-and-stakeholder-demo.md)
- [Pair A Q&A prep runbook](../WEEK-11-stakeholder-qa-prep-angel-fabian-runbook.md)
- [Pair B Q&A prep runbook](../WEEK-11-stakeholder-qa-prep-fatima-nestor-runbook.md)
- [Pair C eval run runbook](../WEEK-11-final-eval-run-bryan-emilio-runbook.md)
- [Pair D demo curation runbook](../WEEK-11-demo-curation-polish-juan-enrique-runbook.md)
- [Day 2 Monday meetup deck](../../../instructor-use/week-11/presentations/presentation-outline-week-11-day-02-monday-meetup.md) — slide 7 (Q&A questions), slide 8 (metrics format), slide 11 (capture-and-classify)
- Marketing voice rules: [`marketing/README.md`](../../../marketing/README.md)
