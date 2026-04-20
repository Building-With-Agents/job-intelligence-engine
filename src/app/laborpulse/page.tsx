"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useTransition,
} from "react";
import { Loader2, RotateCcw, SendHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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

type StreamBanner = {
  message: string;
  /** Messages to resend (ends with the user turn; assistant was removed on failure). */
  retryThread: ChatMessage[];
};

/**
 * Incrementally reads SSE from a fetch response body without buffering the full stream.
 */
async function consumeSseStream(
  stream: ReadableStream<Uint8Array>,
  handlers: {
    onData: (value: unknown) => void;
    signal?: AbortSignal;
  },
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (handlers.signal?.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop() ?? "";

      for (const rawEvent of parts) {
        for (const line of rawEvent.split(/\r?\n/)) {
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (payload === "" || payload === "[DONE]") continue;
          try {
            handlers.onData(JSON.parse(payload));
          } catch {
            /* ignore non-JSON lines */
          }
        }
      }
    }

    if (buffer.trim()) {
      for (const line of buffer.split(/\r?\n/)) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "" || payload === "[DONE]") continue;
        try {
          handlers.onData(JSON.parse(payload));
        } catch {
          /* ignore */
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export default function LaborPulsePage() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamBanner, setStreamBanner] = useState<StreamBanner | null>(null);
  const [, startTransition] = useTransition();
  const endRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

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
  }, [messages, isStreaming]);

  const apiUrl =
    process.env.NEXT_PUBLIC_LABORPULSE_API_URL ?? "/api/laborpulse";

  const stripFailedAssistant = useCallback(() => {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.role === "assistant") return prev.slice(0, -1);
      return prev;
    });
  }, []);

  const runQuery = useCallback(
    async (threadForApi: ChatMessage[]) => {
      if (!sessionId || threadForApi.length === 0) return;

      setStreamBanner(null);
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;

      const assistantId = crypto.randomUUID();
      const assistantPlaceholder: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
      };

      setMessages([...threadForApi, assistantPlaceholder]);
      setIsStreaming(true);

      let sawMessageStop = false;

      const fail = (message: string) => {
        stripFailedAssistant();
        setStreamBanner({
          message,
          retryThread: threadForApi,
        });
        setIsStreaming(false);
      };

      try {
        const res = await fetch(apiUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Session-Id": sessionId,
          },
          body: JSON.stringify({
            messages: threadForApi.map(({ role, content }) => ({ role, content })),
          }),
          signal: ac.signal,
        });

        if (!res.ok) {
          const errBody = await res.text().catch(() => "");
          fail(
            errBody.trim() ||
              `Request failed (${res.status} ${res.statusText})`,
          );
          return;
        }

        if (!res.body) {
          fail("No response body from server.");
          return;
        }

        await consumeSseStream(res.body, {
          signal: ac.signal,
          onData: (value) => {
            if (!value || typeof value !== "object") return;
            const o = value as Record<string, unknown>;

            if (o.type === "message_stop") {
              sawMessageStop = true;
              setIsStreaming(false);
              return;
            }

            if (typeof o.token === "string" && o.token.length > 0) {
              const token = o.token;
              startTransition(() => {
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantId
                      ? { ...m, content: m.content + token }
                      : m,
                  ),
                );
              });
            }
          },
        });

        if (!sawMessageStop) {
          fail(
            "The connection ended before the reply finished. You can retry.",
          );
          return;
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          stripFailedAssistant();
          setIsStreaming(false);
          return;
        }
        fail(
          e instanceof Error ? e.message : "Something went wrong. Try again.",
        );
      } finally {
        if (abortRef.current === ac) abortRef.current = null;
      }
    },
    [apiUrl, sessionId, stripFailedAssistant],
  );

  const beginNewQuery = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || !sessionId || isStreaming) return;

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: trimmed,
      };
      const threadForApi = [...messages, userMsg];
      setInput("");
      void runQuery(threadForApi);
    },
    [sessionId, isStreaming, messages, runQuery],
  );

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    startTransition(() => {
      beginNewQuery(input);
    });
  };

  const onRetry = () => {
    if (!streamBanner) return;
    startTransition(() => {
      setStreamBanner(null);
      void runQuery(streamBanner.retryThread);
    });
  };

  const showExamples = messages.length === 0 && !isStreaming;

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
            {streamBanner && (
              <div
                className="flex flex-col gap-3 rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
                role="alert"
              >
                <p className="text-sm text-destructive">{streamBanner.message}</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="shrink-0 border-destructive/40 text-destructive hover:bg-destructive/10"
                  onClick={onRetry}
                >
                  <RotateCcw className="mr-2 size-4" aria-hidden />
                  Retry
                </Button>
              </div>
            )}

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
                      onClick={() =>
                        startTransition(() => {
                          beginNewQuery(q);
                        })
                      }
                      disabled={!sessionId || isStreaming}
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
                    {m.role === "assistant" &&
                      isStreaming &&
                      m.content === "" && (
                        <span className="inline-flex items-center gap-2 text-muted-foreground">
                          <Loader2 className="size-4 animate-spin" aria-hidden />
                          Thinking…
                        </span>
                      )}
                  </div>
                </li>
              ))}
            </ul>

            <div ref={endRef} />
          </div>
        </ScrollArea>
      </div>

      <div className="fixed inset-x-0 bottom-0 z-10 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] pt-3 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <form
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
            disabled={!sessionId || isStreaming}
            className="min-h-10 flex-1 rounded-xl"
            autoComplete="off"
            aria-label="Message"
          />
          <Button
            type="submit"
            size="icon"
            className="size-10 shrink-0 rounded-xl"
            disabled={!sessionId || isStreaming || !input.trim()}
            aria-label="Send message"
          >
            <SendHorizontal className="size-4" />
          </Button>
        </form>
      </div>
    </div>
  );
}
