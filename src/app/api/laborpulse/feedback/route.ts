import { NextResponse } from "next/server";
import { Pool } from "pg";

const REQUIRED_FIELDS = [
  "question",
  "answer",
  "feedback",
  "session_id",
  "message_id",
] as const;

type RequiredField = (typeof REQUIRED_FIELDS)[number];

function resolveDatabaseUrl(): string {
  const raw =
    process.env.LABORPULSE_DATABASE_URL ?? process.env.PYTHON_DATABASE_URL;
  if (typeof raw !== "string" || !raw.trim()) {
    throw new Error(
      "LABORPULSE_DATABASE_URL or PYTHON_DATABASE_URL must be set for /api/laborpulse/feedback",
    );
  }
  return raw.trim().replace(/^postgresql\+psycopg2:/i, "postgresql:");
}

let pool: Pool | null = null;

function getPool(): Pool {
  if (!pool) {
    pool = new Pool({
      connectionString: resolveDatabaseUrl(),
      max: 10,
    });
  }
  return pool;
}

function missingRequiredFields(
  body: Record<string, unknown>,
): RequiredField[] {
  const missing: RequiredField[] = [];
  for (const key of REQUIRED_FIELDS) {
    const v = body[key];
    if (v === undefined || v === null) {
      missing.push(key);
    }
  }
  return missing;
}

function invalidStringFields(
  body: Record<string, unknown>,
): RequiredField[] {
  const bad: RequiredField[] = [];
  for (const key of REQUIRED_FIELDS) {
    const v = body[key];
    if (typeof v !== "string" || v.trim() === "") {
      bad.push(key);
    }
  }
  return bad;
}

export async function POST(request: Request) {
  let parsed: unknown;
  try {
    parsed = await request.json();
  } catch {
    return NextResponse.json(
      { error: "Request body must be valid JSON." },
      { status: 400 },
    );
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return NextResponse.json(
      { error: "Request body must be a JSON object." },
      { status: 400 },
    );
  }

  const body = parsed as Record<string, unknown>;
  const missing = missingRequiredFields(body);
  if (missing.length > 0) {
    return NextResponse.json(
      {
        error: `Missing required field(s): ${missing.join(", ")}.`,
      },
      { status: 400 },
    );
  }

  const invalid = invalidStringFields(body);
  if (invalid.length > 0) {
    return NextResponse.json(
      {
        error: `Required field(s) must be non-empty strings: ${invalid.join(", ")}.`,
      },
      { status: 400 },
    );
  }

  const question = (body.question as string).trim();
  const answer = (body.answer as string).trim();
  const feedback = (body.feedback as string).trim();
  const session_id = (body.session_id as string).trim();
  const message_id = (body.message_id as string).trim();

  let p: Pool;
  try {
    p = getPool();
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Database configuration error.";
    return NextResponse.json({ error: msg }, { status: 503 });
  }

  const sql = `
INSERT INTO dbo.qa_feedback (session_id, message_id, question, answer, feedback)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (session_id, message_id)
DO UPDATE SET
  question = EXCLUDED.question,
  answer = EXCLUDED.answer,
  feedback = EXCLUDED.feedback,
  updated_at = NOW()
RETURNING session_id, message_id, question, answer, feedback, created_at, updated_at
`;

  try {
    const result = await p.query(sql, [
      session_id,
      message_id,
      question,
      answer,
      feedback,
    ]);
    const row = result.rows[0] as {
      session_id: string;
      message_id: string;
      question: string;
      answer: string;
      feedback: string;
      created_at: Date;
      updated_at: Date;
    };

    return NextResponse.json({
      qa_feedback: {
        session_id: row.session_id,
        message_id: row.message_id,
        question: row.question,
        answer: row.answer,
        feedback: row.feedback,
        created_at: row.created_at.toISOString(),
        updated_at: row.updated_at.toISOString(),
      },
    });
  } catch (e) {
    const msg =
      e instanceof Error ? e.message : "Failed to persist feedback.";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
