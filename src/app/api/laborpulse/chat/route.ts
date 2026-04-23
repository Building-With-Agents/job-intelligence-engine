import { NextResponse } from "next/server";

/** Local stub — set `NEXT_PUBLIC_LABORPULSE_API_URL` to your real backend in production. */
export async function POST(request: Request) {
  const sessionId = request.headers.get("X-Session-Id") ?? "";

  let body: { messages?: { role: string; content: string }[] } = {};
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const n = body.messages?.length ?? 0;
  return NextResponse.json({
    reply: `Stub response (${n} message${n === 1 ? "" : "s"}). Session prefix: ${sessionId.slice(0, 8) || "—"}. Point NEXT_PUBLIC_LABORPULSE_API_URL at your API when ready.`,
  });
}
