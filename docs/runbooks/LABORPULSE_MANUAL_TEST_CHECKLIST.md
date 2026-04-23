# Labor Pulse — manual test checklist (deployed `/laborpulse`)

Use this against your **production** (or staging) deployment. Replace `ORIGIN` with the site base URL (e.g. `https://your-app.vercel.app`) and record results in the **Pass/Fail** column.

| # | Step | Pass/Fail | Notes |
|---|------|-----------|-------|
| 0 | Open `ORIGIN/laborpulse` in a clean profile or incognito (optional: clear `sessionStorage` for key `laborpulse_session_id`). | | |
| 1 | **Progressive streaming** — Submit a question that returns a non-trivial answer (e.g. an example chip). **Expected:** assistant text **grows over time** (tokens/chunks), not a single paste after a long pause. **Fail if:** full answer appears only after the stream ends with no visible incremental updates. | | |
| 2 | **First thumbs-up + DB** — After the answer finishes, click **thumbs up** once. Query Postgres (see [SQL below](#sql-verify-qa_feedback)). **Expected:** **exactly one** row for that `(session_id, message_id)` with `question`, `answer`, and `feedback = 'up'` matching the UI (trimmed). Copy `session_id` from DevTools → Application → Session Storage, or from the row. | | |
| 3 | **Second thumbs-up (no duplicate)** — Click **thumbs up** again on the **same** assistant message. Re-run the SQL. **Expected:** still **one** row for that `(session_id, message_id)` (upsert updates `updated_at`, does not insert a second row). `COUNT(*)` grouped by `session_id, message_id` must remain `1`. | | |
| 4 | **Follow-up chip** — Click a **follow-up** chip under that answer. **Expected:** the **input field** shows the chip text; after send, a **new** assistant message streams in (new `message_id` in the DOM/state). | | |
| 5 | **Offline mid-stream** — Start a new question; while the assistant is still streaming, open DevTools → **Network** → set **Offline** (or “Slow 3G” + abort). **Expected:** an **error banner** (retry), **not** a blank page or uncaught React error overlay. | | |
| 6 | **Vercel / SSE** — In Vercel → Project → **Logs** (Runtime), filter around the test window for routes like `/api/laborpulse` or `/laborpulse`. **Expected:** no pattern of **stuck** or endlessly repeating requests after you navigate away or after stream completion; no unhandled errors tied to each closed stream. (Exact wording depends on Vercel’s UI; you are checking that streams **close** and connections do not leak as hung serverless invocations.) | | |

## SQL — verify `qa_feedback`

Connect to the **same** database the app uses for `LABORPULSE_DATABASE_URL` / `PYTHON_DATABASE_URL` on Vercel.

After step 2, use `session_id` from session storage and `message_id` from the assistant bubble’s React state if exposed, or locate the latest row:

```sql
-- Latest feedback rows (adjust LIMIT)
SELECT session_id, message_id, LEFT(question, 80) AS q, LEFT(answer, 80) AS a,
       feedback, created_at, updated_at
FROM dbo.qa_feedback
ORDER BY updated_at DESC
LIMIT 20;
```

**Exact row check** (steps 2–3):

```sql
SELECT COUNT(*) AS row_count
FROM dbo.qa_feedback
WHERE session_id = '<paste_session_id>'
  AND message_id = '<paste_message_id>';
-- Expected: 1 after first thumbs-up; still 1 after second thumbs-up
```

```sql
SELECT session_id, message_id, question, answer, feedback, created_at, updated_at
FROM dbo.qa_feedback
WHERE session_id = '<paste_session_id>'
  AND message_id = '<paste_message_id>';
```

**Upsert behavior:** second thumbs-up should bump `updated_at` (and may refresh `question`/`answer` text if the client resends them); `created_at` stays the same.

## Sending the link to Ritu

When all rows in the table pass, send Ritu the **exact** URL:

`ORIGIN/laborpulse`

(Replace `ORIGIN` with your deployment hostname, including `https://`.)

## References (repo)

- Feedback API: `src/app/api/laborpulse/feedback/route.ts` — `INSERT ... ON CONFLICT (session_id, message_id) DO UPDATE`
- Table: `dbo.qa_feedback` — primary key `(session_id, message_id)` (`common/data_store/migrations.py`)
- Chat UI: `src/app/laborpulse/page.tsx` — SSE consumer, thumbs, follow-up chips, stream error banner
