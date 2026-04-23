import { NextResponse } from "next/server";
import { Pool } from "pg";

const REQUIRED_FIELDS = [
  "question",
  "answer",
  "session_id",
  "message_id",
  "timestamp",
] as const;

type RequiredField = (typeof REQUIRED_FIELDS)[number];

function resolveDatabaseUrl(): string {
  const raw =
    process.env.LABORPULSE_DATABASE_URL ?? process.env.PYTHON_DATABASE_URL;
  if (typeof raw !== "string" || !raw.trim()) {
    throw new Error(
      "LABORPULSE_DATABASE_URL or PYTHON_DATABASE_URL must be set for /api/laborpulse/log",
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
  fields: readonly RequiredField[],
): RequiredField[] {
  const bad: RequiredField[] = [];
  for (const key of fields) {
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

  const invalid = invalidStringFields(body, REQUIRED_FIELDS);
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
  const session_id = (body.session_id as string).trim();
  const message_id = (body.message_id as string).trim();
  const timestamp = (body.timestamp as string).trim();

  let confidence: number | null = null;
  if (body.confidence !== undefined && body.confidence !== null) {
    if (typeof body.confidence !== "number" || !Number.isFinite(body.confidence)) {
      return NextResponse.json(
        { error: "confidence must be a finite number when provided." },
        { status: 400 },
      );
    }
    confidence = body.confidence;
  }

  if (Number.isNaN(Date.parse(timestamp))) {
    return NextResponse.json(
      { error: "timestamp must be a valid ISO-8601 date string." },
      { status: 400 },
    );
  }

  let p: Pool;
  try {
    p = getPool();
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Database configuration error.";
    return NextResponse.json({ error: msg }, { status: 503 });
  }

  const sql = `
INSERT INTO dbo.conversation_log (question, answer, session_id, message_id, confidence)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (session_id, message_id) DO NOTHING
`;

  try {
    await p.query(sql, [question, answer, session_id, message_id, confidence]);
    return NextResponse.json({ ok: true });
  } catch (e) {
    const msg =
      e instanceof Error ? e.message : "Failed to persist conversation log.";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
