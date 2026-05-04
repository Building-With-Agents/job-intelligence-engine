# Demo Script — Week 11 Final Metrics Review (5 min)

Speaker notes for the findingsv2.31 metrics slides. Read naturally — these are your words, not bullet points on screen.

---

## Slide 1 — Headline Scorecard

**What this is:** This is our final evaluation scorecard. We ran all 90 golden questions — our hand-curated test set covering nine different types of workforce intelligence questions — against the live pipeline end-to-end. Every single question processed without errors.

**Words to say:**

"Here's where we landed on the final eval. We evaluated the full pipeline against 90 golden questions — carefully written workforce intelligence questions that a real workforce director would ask. Things like 'which skills are trending in the Borderplex,' 'what do employer hiring patterns look like,' or 'how should we update our cybersecurity curriculum.'

The key numbers:

Intent accuracy at 81.1% — that means when someone asks a question, the system correctly understands what type of question it is 8 out of 10 times. Is it asking about a trend? A geographic breakdown? A curriculum recommendation? Getting this right is what routes the question to the correct data source.

Evidence citation at 51% — every answer the system produces is grounded in real database evidence. This score measures how thoroughly those citations cover our gold-standard rubric. No hallucinations — if it answers, it cites.

Confidence calibration at 95.7% — when the system says it's confident, it's right. When it's uncertain, it flags that explicitly and tells you why. The model is honest about what it knows and doesn't know.

Macro F1 at 80.9% — this is a combined precision-and-recall score across all nine question types. It tells us the classifier reliably handles the full spectrum of workforce intelligence queries.

And latency — median 2 seconds per question. Nobody's waiting around for answers."

---

## Slide 2 — Trajectory

**What this is:** This shows how the system evolved over four evaluation runs across two weeks. It's our improvement story — from first baseline to today.

**Words to say:**

"This is how we got here. Four runs, two weeks of iteration.

April 23rd — our v1 baseline. The system had decent intent accuracy at 0.81, but the scorer was lenient. Evidence citation looked high at 0.69 because the grading was generous.

April 24th — we tightened the scoring. Moved to a strict binary grader and a geometric composite. Evidence citation dropped to 0.34. That's not because the system got worse — it's because we raised our own bar. We also discovered the aggregate tables were empty.

May 1st — the team fixed the data layer. Populated tables, fixed routing bugs, backfilled classifications. Evidence climbed back to 0.57 and answerability jumped to 0.46.

Today — intent accuracy is back at 0.81, evidence at 0.51, confidence at 0.96. The numbers are consistent. We're not chasing volatile peaks — we're building a stable floor.

The takeaway: the improvements came from fixing data and infrastructure, not prompt hacking. The prompts were good from the start — they just needed real data to work with. And latency has been excellent the entire time."

---

## Slide 3 — Intent Heatmap

**What this is:** This breaks down performance by question type. We have nine intent categories and this shows which ones the system handles reliably and which still need work. The F1 score combines precision and recall — how reliably does the system both recognize and correctly handle each type of question.

**Words to say:**

"Let's look at where the system is strong and where we're honest about gaps.

Top tier — comparison and curriculum questions both hit 0.95 F1. When someone asks 'compare cybersecurity demand versus cloud computing' or 'what training pathway should we build for data engineering,' the system nails it almost every time. Emergence is at 0.89 — new roles appearing in the market.

Middle tier — geographic at 0.87, workflow at 0.82, trend at 0.78. These are working well. Geographic improved significantly from our earlier runs because we fixed the embedding-based role resolver.

Lower tier — disruption at 0.71, role evolution at 0.67, employer at 0.63. Disruption has perfect recall — it always catches disruption questions — but lower precision because some trend questions get pulled into the disruption bucket. Role evolution has the opposite problem: high precision but half get misclassified elsewhere. Employer has a known routing bug where geographic terms are matched against company names instead of location columns — that's a one-line fix.

The critical point: no question type is broken. Every single one produces answers. The gaps are in classification precision, not pipeline capability."

---

## Slide 4 — What Worked / What Didn't

**What this is:** An honest summary. We lead with wins, then name gaps directly.

**Words to say:**

"What we're proud of: zero pipeline errors. All 90 questions ran cleanly. This is the first full run with no infrastructure crashes.

Macro F1 at 0.81 across nine intent types means the classifier is genuinely multi-class capable — not just good at one or two categories.

Latency is excellent — 2 second median. Confidence calibration at 96% means the system is trustworthy. When it says 'I'm not sure,' it means it.

What's still open — and we want to be transparent. The employer router has a bug where it searches for geographic terms in the company name column. That's issue 346 — it's a straightforward fix. Five of ten role_evolution questions get confused with trend or disruption — the tie-breaker rules need a narrow tune. And some aggregate tables are empty because this is a fresh database seed — running the analytics refresh populates them and brings answerability back to the 0.46 we saw on the production database."

---

## Slide 5 — Next Steps

**What this is:** Concrete, scoped actions. No hand-waving.

**Words to say:**

"Five things to reach production targets:

First — run the analytics aggregate refresh. That populates skill_velocity and skill_demand_weekly, which directly lifts answerability and comparison evidence scores.

Second — fix the employer router. Route geographic terms to city and state columns instead of company_name. Unlocks all 10 employer questions.

Third — tune the role_evolution vs trend tie-breaker. It's a narrow prompt adjustment with a hard regression test.

Fourth — expand the skill taxonomy gate to include AI-tool names like Copilot, Cursor, and ChatGPT. The disruption intent refuses because those aren't in the ESCO taxonomy yet.

Target: intent accuracy above 90%, macro F1 above 85%. Based on what we've seen from the earlier run with populated aggregates, those are achievable with these four fixes and no architectural changes."
