# Golden Questions Authoring Form

A local Streamlit form for filling in the scoring criteria on each golden question — `context`, `ideal_answer_summary`, `must_include`, `must_not_include`, `difficulty`.

Seeded with the 90 JIE-aligned questions from `lesson-framework/week-09/readings/reading-jsis-query-reference.md` in the `curriculum-planning` repo. Each pair authors the scoring criteria for their assigned intents; the output lives in `golden_questions.json` in this folder and is copied into `eval/qa_golden_questions.json` for the Langfuse upload.

## Run it

From the JIE repo root:

```bash
streamlit run tools/golden-questions-form/app.py
```

Streamlit opens at `http://localhost:8501`.

## How it works

- **`golden_questions.json` is both seed and persistent store.** The file ships with 90 questions pre-populated (IDs `gq-001` through `gq-090`). Every save writes back to the same file — atomically via a `.tmp` + `os.replace` to avoid partial writes.
- **Filter by intent** — sidebar dropdown narrows the record list. Pick your assigned intent, then pick a record, and the form loads that question for editing.
- **Add new questions** — select `+ New question` in the record picker. The form suggests the next `gq-NNN` ID based on the max currently in the file.
- **Add new intents or difficulty values** — the intent and difficulty dropdowns each have a `+ Add new…` option that reveals a text input. The new value is saved on the record; the dropdown re-populates from file contents on the next render, so any new value becomes selectable for future records.
- **Validation is Pydantic-based.** The `GoldenQuestion` model requires `id`, `question`, and `intent` to be non-empty. Everything else has a default. Rows that fail validation show a banner warning but don't prevent the app from loading — so broken hand-edits can be fixed through the form.

## Authoring workflow — form + Cursor LLM, under close scrutiny

The scoring criteria (`ideal_answer_summary`, `must_include`, `must_not_include`) are the ground truth against which the eval harness measures the pipeline's answers. Garbage in → garbage out — generic or hallucinated criteria make the eval meaningless.

**Recommended workflow:**

1. **Open this form** and filter to your assigned intent.
2. **For each question, open a Cursor chat** pointed at the JIE repo (pair's workstream `.cursor/rules/*.mdc` file in context).
3. **Ask Cursor (Sonnet recommended) to draft** a first pass of `ideal_answer_summary` and the `must_include` / `must_not_include` token lists — anchored to specific JIE tables, columns, and temporal buckets rather than generic phrasings.

   Example prompt:

   > *"For this golden question — '[paste question]' — draft an `ideal_answer_summary` that names the specific JIE tables and columns a correct answer must cite, specific percentages or ranges where possible, and a concrete next-step for the wfd_archetype. Then draft `must_include` tokens (checkable phrases) and `must_not_include` tokens (anti-patterns the eval should reject). Do not fabricate numbers; use '[to be filled from data]' placeholders if you don't know the actual value."*

4. **Verify every data claim against the JIE corpus before accepting.** This is the non-negotiable quality gate:

   - Every table name mentioned (`skill_demand_weekly`, `skill_velocity`, `canonical_roles`, `extracted_intelligence`, `job_postings`, `employer_profiles`, `disruption_fingerprints`, etc.) — open the database or the schema and confirm it exists and has data in the relevant period.
   - Every skill, tool, or certification named — grep `config/ingestion_queries.yaml` or run a `SELECT DISTINCT` to confirm it appears in the ingested data.
   - Every employer named — confirm the employer exists in `employer_profiles`.
   - Every temporal period referenced (`pre_chatgpt`, `early_genai`, `post_gpt4`, `agentic_era`) — confirm there are postings in that period for the role family in question.
   - Every percentage or ratio claimed in the `ideal_answer_summary` — either back it with a real query result or rewrite the claim to be qualitative ("should cite demand percentages from `skill_demand_weekly`") rather than quantitative ("should cite 23% Python demand growth").

5. **Paste the Cursor-drafted fields into the form**, edit for voice and accuracy, and save. The form's atomic write means you can't half-save and corrupt the file.

6. **Peer-review within the pair before committing.** One dev authors → the other spot-checks the data claims before the pair considers the question done.

**Fail modes to watch for (and reject in peer review):**

- `ideal_answer_summary` that says "the answer should reference relevant data" — useless, no measurable criterion.
- `must_include` tokens that are too vague to check mechanically (`"good analysis"`, `"clear explanation"`).
- Specific percentages or employer counts that neither author has verified against the database.
- References to tables, columns, or data dimensions that don't exist in the current JIE schema.
- Borderplex geography assumptions that aren't actually ingested (Austin, Albuquerque, state-wide, national — none of these are in the corpus per `config/ingestion_queries.yaml`).

## After your pair finishes authoring — DO NOT overwrite the eval file directly

**Warning:** Your local `golden_questions.json` contains all 90 records — but only the 10–30 records for your assigned intents have your authored scoring criteria. The other 60–80 records are still the untouched seeded placeholders. Copying your local file straight onto `eval/qa_golden_questions.json` will **clobber every other pair's authored criteria**.

The corpus must be assembled by merging all four pairs' contributions first, then copied into `eval/`.

### Step 1 — each pair commits their records to their week-9 branch

Work on your pair's week-9 branch. Edit only the records for your assigned intents, save via the form, then commit:

```bash
git checkout week-9/<your-workstream>
git add tools/golden-questions-form/golden_questions.json
git commit -m "author: <intent> scoring criteria (gq-NNN to gq-MMM)"
git push
```

The commit diff should touch only the records for your assigned intent IDs (see the ID convention in the Week 9 runbook). Records outside your intent range should match the seeded state byte-for-byte.

### Step 2 — assemble the completed corpus (Pair C, designated collector)

Pair C owns the eval harness, so they're the natural assembler. Once every pair has committed, build the integration branch by cherry-picking each pair's authoring commit:

```bash
git checkout development
git pull
git checkout -b publish/week-09-golden-questions-eval-corpus

# Cherry-pick each pair's authoring commit in turn. The commits touch
# disjoint record ranges, so git merges cleanly under normal conditions.
git cherry-pick <pair-A-disruption-sha>
git cherry-pick <pair-A-emergence-sha>
git cherry-pick <pair-B-trend-sha>
git cherry-pick <pair-B-role_evolution-sha>
git cherry-pick <pair-C-geographic-sha>
git cherry-pick <pair-C-comparison-sha>
git cherry-pick <pair-D-employer-sha>
git cherry-pick <pair-D-curriculum-sha>
git cherry-pick <pair-D-workflow-sha>
```

If two pairs edited the same record (they shouldn't — the intents are disjoint), resolve the conflict by picking the version with the verified scoring criteria.

### Step 3 — sanity-check before copying

Confirm all 90 records have authored scoring criteria (not empty placeholders):

```bash
python -c "
import json
data = json.load(open('tools/golden-questions-form/golden_questions.json'))
missing = [r['id'] for r in data if not r.get('ideal_answer_summary','').strip()]
print(f'{len(data) - len(missing)} / {len(data)} records have ideal_answer_summary')
if missing:
    print('Missing:', missing)
"
```

All 90 should have non-empty `ideal_answer_summary`. If any are still empty, chase down the owning pair before proceeding.

### Step 4 — copy to the eval file and open a PR

```bash
cp tools/golden-questions-form/golden_questions.json eval/qa_golden_questions.json
git add eval/qa_golden_questions.json
git commit -m "eval: assemble qa_golden_questions.json corpus for v1-baseline"
git push -u origin publish/week-09-golden-questions-eval-corpus
gh pr create --base development --title "Publish Week 9 golden-questions corpus (v1-baseline)" --body "..."
```

### Step 5 — after the PR merges, upload to Langfuse

```bash
git checkout development && git pull
python scripts/upload_qa_dataset.py
```

## Notes

- The form uses a Pydantic model (`GoldenQuestion` in `app.py`) with `intent` and `difficulty` as free strings — new categories can be added without editing the code. Validation is limited to the required fields (`id`, `question`, `intent`).
- Running the form does not require any database connection; it's pure file I/O. Data verification (step 4 above) is done separately via Cursor / the JIE pipeline tooling.
- If two devs run the form simultaneously against the same file, last-write-wins. The app re-reads the file right before writing to reduce the race, but does not lock. Coordinate with your pair partner to avoid stepping on each other.
