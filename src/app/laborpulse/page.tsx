"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, SendHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

const SESSION_STORAGE_KEY = "laborpulse_session_id";

const EXAMPLE_QUESTIONS = [
  "Which skills are trending fastest in regional tech postings this quarter?",
  "How has demand for AI-related roles changed over the last 90 days?",
  "What salary ranges are typical for mid-level software engineering roles?",
] as const;

type ChatRole = "user" | "assistant";

type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
};

function parseAssistantReply(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const o = data as Record<string, unknown>;
  if (typeof o.reply === "string") return o.reply;
  if (typeof o.text === "string") return o.text;
  if (typeof o.content === "string") return o.content;
  if (o.message && typeof o.message === "object") {
    const m = o.message as Record<string, unknown>;
    if (typeof m.content === "string") return m.content;
  }
  return null;
}

export default function LaborPulsePage() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    let id = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!id) {
      id = crypto.randomUUID();
      sessionStorage.setItem(SESSION_STORAGE_KEY, id);
    }
    setSessionId(id);
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);

  const apiUrl =
    process.env.NEXT_PUBLIC_LABORPULSE_API_URL ?? "/api/laborpulse/chat";

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || !sessionId) return;

      setError(null);
      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: trimmed,
      };
      const nextThread = [...messages, userMsg];
      setMessages(nextThread);
      setInput("");
      setPending(true);

      try {
        const res = await fetch(apiUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Session-Id": sessionId,
          },
          body: JSON.stringify({
            messages: nextThread.map(({ role, content }) => ({
              role,
              content,
            })),
          }),
        });

        const raw = await res.json().catch(() => null);
        if (!res.ok) {
          const errText =
            raw && typeof raw === "object" && "error" in raw
              ? String((raw as { error: unknown }).error)
              : res.statusText;
          throw new Error(errText || `Request failed (${res.status})`);
        }

        const reply = parseAssistantReply(raw);
        if (!reply) {
          throw new Error("Unexpected response from server.");
        }

        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: reply,
          },
        ]);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Something went wrong.");
        setMessages((prev) => prev.slice(0, -1));
      } finally {
        setPending(false);
      }
    },
    [apiUrl, messages, sessionId],
  );

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void sendMessage(input);
  };

  const showExamples = messages.length === 0 && !pending;

  return (
    <div className="flex min-h-dvh flex-col bg-muted/30">
      <header className="shrink-0 border-b bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="mx-auto flex max-w-3xl flex-col gap-1 px-4 py-4">
          <h1 className="text-lg font-semibold tracking-tight">Labor Pulse</h1>
          <p className="text-sm text-muted-foreground">
            Ask questions about workforce signals and job-market intelligence.
          </p>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col">
        <ScrollArea className="min-h-0 flex-1">
          <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6 pb-32">
            {showExamples && (
              <Card className="border-dashed bg-background/60 shadow-sm">
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">Try asking</CardTitle>
                  <CardDescription>
                    Pick a starter question or type your own below.
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
                  {EXAMPLE_QUESTIONS.map((q) => (
                    <Button
                      key={q}
                      type="button"
                      variant="secondary"
                      size="sm"
                      className="h-auto justify-start whitespace-normal text-left text-xs font-normal leading-snug sm:max-w-[calc(50%-0.25rem)]"
                      onClick={() => void sendMessage(q)}
                      disabled={!sessionId || pending}
                    >
                      {q}
                    </Button>
                  ))}
                </CardContent>
              </Card>
            )}

            <ul className="flex flex-col gap-4">
              {messages.map((m) => (
                <li
                  key={m.id}
                  className={cn(
                    "flex w-full",
                    m.role === "user" ? "justify-end" : "justify-start",
                  )}
                >
                  <div
                    className={cn(
                      "max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm",
                      m.role === "user"
                        ? "bg-primary text-primary-foreground"
                        : "border bg-card text-card-foreground",
                    )}
                  >
                    {m.content}
                  </div>
                </li>
              ))}
            </ul>

            {pending && (
              <div className="flex justify-start">
                <div className="flex items-center gap-2 rounded-2xl border bg-card px-4 py-3 text-sm text-muted-foreground shadow-sm">
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                  Thinking…
                </div>
              </div>
            )}

            {error && (
              <p
                className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                role="alert"
              >
                {error}
              </p>
            )}

            <div ref={endRef} />
          </div>
        </ScrollArea>
      </div>

      <div className="fixed inset-x-0 bottom-0 z-10 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] pt-3 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <form
          ref={formRef}
          onSubmit={onSubmit}
          className="mx-auto flex max-w-3xl gap-2 px-4 pb-4"
        >
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              sessionId
                ? "Ask about hiring trends, skills, or roles…"
                : "Loading session…"
            }
            disabled={!sessionId || pending}
            className="min-h-10 flex-1 rounded-xl"
            autoComplete="off"
            aria-label="Message"
          />
          <Button
            type="submit"
            size="icon"
            className="size-10 shrink-0 rounded-xl"
            disabled={!sessionId || pending || !input.trim()}
            aria-label="Send message"
          >
            <SendHorizontal className="size-4" />
          </Button>
        </form>
      </div>
    </div>
  );
}
