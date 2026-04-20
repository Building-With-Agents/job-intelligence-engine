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

export async function POST(request: NextRequest) {
  const contentType =
    request.headers.get("Content-Type") ?? "application/json";

  let upstream: Response;
  try {
    const streamBody = request.body;
    upstream = await fetch(
      UPSTREAM_QUERY_URL,
      streamBody != null
        ? ({
            method: "POST",
            headers: {
              "Content-Type": contentType,
            },
            body: streamBody,
            signal: request.signal,
            duplex: "half",
          } as RequestInit & { duplex: "half" })
        : {
            method: "POST",
            headers: {
              "Content-Type": contentType,
            },
            signal: request.signal,
          },
    );
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      return new Response(null, { status: 499 });
    }
    return Response.json(
      { error: "Upstream request to JIE analytics failed." },
      { status: 502 },
    );
  }

  if (upstream.body == null) {
    return new Response(null, { status: upstream.status });
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
