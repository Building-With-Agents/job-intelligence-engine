import type { NextRequest } from "next/server";

function requireJieApiUrl(): string {
  const raw = process.env.JIE_API_URL;
  if (typeof raw !== "string" || !raw.trim()) {
    throw new Error(
      "[api/laborpulse] JIE_API_URL is required but missing or empty. Set JIE_API_URL to the Job Intelligence Engine FastAPI base URL (scheme + host, no path), e.g. https://jie-api.example.com",
    );
  }
  return raw.trim().replace(/\/+$/, "");
}

const JIE_API_BASE = requireJieApiUrl();

const UPSTREAM_QUERY_URL = `${JIE_API_BASE}/analytics/query`;

const SSE_HEADERS = {
  "Content-Type": "text/event-stream",
  "Cache-Control": "no-cache, no-transform",
  Connection: "keep-alive",
  "X-Accel-Buffering": "no",
} as const;

type ChatTurn = { role?: unknown; content?: unknown };

function extractLastUserQuestion(body: unknown): string | null {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return null;
  }
  const messages = (body as { messages?: unknown }).messages;
  if (!Array.isArray(messages)) {
    return null;
  }
  const list = messages as ChatTurn[];
  const last = list.findLast(
    (m) =>
      m &&
      typeof m === "object" &&
      m.role === "user" &&
      typeof m.content === "string",
  );
  if (!last || typeof last.content !== "string") {
    return null;
  }
  const trimmed = last.content.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** Split answer into streaming tokens (words + whitespace runs) without dropping spacing. */
function* answerToTokenChunks(answer: string): Generator<string> {
  if (!answer) {
    return;
  }
  const re = /\S+|\s+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(answer)) !== null) {
    yield m[0];
  }
}

type AnalyticsQueryJson = {
  answer?: string;
  follow_up_questions?: string[];
  confidence?: number;
};

const PING_INTERVAL_MS = 10_000;

function buildSyntheticSseStream(
  data: AnalyticsQueryJson,
  signal: AbortSignal | undefined,
): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const answer = typeof data.answer === "string" ? data.answer : "";

  return new ReadableStream({
    async start(controller) {
      let closed = false;
      const pingInterval = setInterval(() => {
        if (closed || signal?.aborted) {
          return;
        }
        try {
          controller.enqueue(
            encoder.encode(`event: ping\ndata: {}\n\n`),
          );
        } catch {
          /* stream closing */
        }
      }, PING_INTERVAL_MS);

      try {
        for (const chunk of answerToTokenChunks(answer)) {
          if (signal?.aborted) {
            return;
          }
          const line = `data: ${JSON.stringify({ token: chunk })}\n\n`;
          controller.enqueue(encoder.encode(line));
          await new Promise<void>((resolve) => {
            setTimeout(resolve, 0);
          });
        }
        if (signal?.aborted) {
          return;
        }
        const followUps = Array.isArray(data.follow_up_questions)
          ? data.follow_up_questions.filter(
              (q): q is string => typeof q === "string" && q.trim().length > 0,
            )
          : [];
        const confidence =
          typeof data.confidence === "number" && Number.isFinite(data.confidence)
            ? data.confidence
            : 0;
        const stopLine = `data: ${JSON.stringify({
          type: "message_stop",
          follow_up_questions: followUps,
          confidence,
        })}\n\n`;
        controller.enqueue(encoder.encode(stopLine));
      } catch {
        /* non-fatal */
      } finally {
        closed = true;
        clearInterval(pingInterval);
        try {
          controller.close();
        } catch {
          /* already closed */
        }
      }
    },
  });
}

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Request body must be valid JSON." }, { status: 400 });
  }

  const question = extractLastUserQuestion(body);
  if (!question) {
    return Response.json(
      { error: "Missing or empty last user message in messages array." },
      { status: 400 },
    );
  }

  const rawSession = request.headers.get("X-Session-Id");
  const correlation_id = rawSession?.trim() ? rawSession.trim() : undefined;

  const upstreamPayload: { question: string; correlation_id?: string } = {
    question,
  };
  if (correlation_id !== undefined) {
    upstreamPayload.correlation_id = correlation_id;
  }

  let upstream: Response;
  try {
    upstream = await fetch(UPSTREAM_QUERY_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(upstreamPayload),
      signal: request.signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      return new Response(null, { status: 499 });
    }
    return Response.json(
      { error: "Upstream request to JIE analytics failed." },
      { status: 502 },
    );
  }

  const contentType = upstream.headers.get("Content-Type") ?? "";

  if (!upstream.ok) {
    const errText = await upstream.text().catch(() => "");
    return new Response(errText || upstream.statusText, {
      status: upstream.status,
      headers: contentType.includes("application/json")
        ? { "Content-Type": "application/json" }
        : { "Content-Type": "text/plain; charset=utf-8" },
    });
  }

  let data: AnalyticsQueryJson;
  try {
    data = (await upstream.json()) as AnalyticsQueryJson;
  } catch {
    return Response.json(
      { error: "Upstream returned invalid JSON." },
      { status: 502 },
    );
  }

  const stream = buildSyntheticSseStream(data, request.signal);

  return new Response(stream, {
    status: 200,
    headers: SSE_HEADERS,
  });
}
