"use client";

import {
  useCallback,
  useEffect,
  useOptimistic,
  useRef,
  useState,
  useTransition,
} from "react";
import {
  Loader2,
  RotateCcw,
  SendHorizontal,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";

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
  /** Set from FastAPI SSE when `confidence` is present (typically final event). */
  confidence?: number;
};

type StreamBanner = {
  message: string;
  /** Messages to resend (ends with the user turn; assistant was removed on failure). */
  retryThread: ChatMessage[];
};

type FeedbackVote = "up" | "down";

const FEEDBACK_API =
  process.env.NEXT_PUBLIC_LABORPULSE_FEEDBACK_API_URL ??
  "/api/laborpulse/feedback";

const LOG_API =
  process.env.NEXT_PUBLIC_LABORPULSE_LOG_API_URL ?? "/api/laborpulse/log";

function questionBeforeAssistant(messages: ChatMessage[], index: number): string {
  for (let j = index - 1; j >= 0; j--) {
    if (messages[j].role === "user") return messages[j].content;
  }
  return "";
}

function parseServerFeedback(raw: string): FeedbackVote {
  const t = raw.trim().toLowerCase();
  if (t === "down") return "down";
  return "up";
}

/** Normalize API `follow_up_questions` to trimmed non-empty strings, or null if invalid. */
function normalizeFollowUpQuestions(raw: unknown): string[] | null {
  if (!Array.isArray(raw)) return null;
  const out: string[] = [];
  for (const x of raw) {
    if (typeof x === "string") {
      const t = x.trim();
      if (t) out.push(t);
    }
  }
  return out;
}

const LOW_CONFIDENCE_THRESHOLD = 0.6;

/** Idle deadline (ms); timer is implemented as a resettable setInterval per spec. */
const SSE_IDLE_MS = 15_000;

function parseConfidence(raw: unknown): number | undefined {
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "string") {
    const n = Number(raw.trim());
    if (Number.isFinite(n)) return n;
  }
  return undefined;
}

/**
 * Parse one SSE event block (lines between blank-line separators).
 * Resets idle heartbeat on any well-formed frame, including `event: ping`.
 */
function dispatchSseEventBlock(
  rawEvent: string,
  handlers: {
    onData: (value: unknown) => void;
    onFrame?: () => void;
  },
): void {
  let eventType = "message";
  const dataLines: string[] = [];
  for (const line of rawEvent.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      eventType = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  handlers.onFrame?.();
  if (eventType === "ping") {
    return;
  }
  const dataPayload = dataLines.join("\n");
  if (dataPayload === "" || dataPayload === "[DONE]") {
    return;
  }
  try {
    handlers.onData(JSON.parse(dataPayload));
  } catch {
    /* ignore non-JSON data lines */
  }
}

/**
 * Incrementally reads SSE from a fetch response body without buffering the full stream.
 * Uses a resettable 15s idle timer: if no SSE frame arrives in time, `onIdleTimeout` runs.
 */
async function consumeSseStream(
  stream: ReadableStream<Uint8Array>,
  handlers: {
    onData: (value: unknown) => void;
    signal?: AbortSignal;
    onIdleTimeout?: () => void;
  },
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  let idleInterval: ReturnType<typeof setInterval> | null = null;
  const armIdleTimer = () => {
    if (idleInterval !== null) {
      clearInterval(idleInterval);
    }
    idleInterval = setInterval(() => {
      handlers.onIdleTimeout?.();
    }, SSE_IDLE_MS);
  };

  armIdleTimer();

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
        if (rawEvent.trim() === "") continue;
        dispatchSseEventBlock(rawEvent, {
          onData: handlers.onData,
          onFrame: armIdleTimer,
        });
      }
    }

    if (buffer.trim()) {
      dispatchSseEventBlock(buffer, {
        onData: handlers.onData,
        onFrame: armIdleTimer,
      });
    }
  } finally {
    if (idleInterval !== null) {
      clearInterval(idleInterval);
    }
    reader.releaseLock();
  }
}

export default function LaborPulsePage() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamBanner, setStreamBanner] = useState<StreamBanner | null>(null);
  const [connectionLost, setConnectionLost] = useState(false);
  const [lostRetryThread, setLostRetryThread] = useState<ChatMessage[] | null>(
    null,
  );
  const [committedFeedback, setCommittedFeedback] = useState<
    Record<string, FeedbackVote>
  >({});
  const [optimisticFeedback, addOptimisticFeedback] = useOptimistic(
    committedFeedback,
    (
      state: Record<string, FeedbackVote>,
      action: { messageId: string; vote: FeedbackVote },
    ) => ({
      ...state,
      [action.messageId]: action.vote,
    }),
  );
  const [pendingFeedbackId, setPendingFeedbackId] = useState<string | null>(
    null,
  );
  const [feedbackInlineError, setFeedbackInlineError] = useState<
    Record<string, string>
  >({});
  /** Follow-up chips from SSE, keyed by assistant message id (per answer). */
  const [followUpsByMessageId, setFollowUpsByMessageId] = useState<
    Record<string, string[]>
  >({});
  const [, startTransition] = useTransition();
  const endRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const feedbackInflightRef = useRef<Set<string>>(new Set());
  /** True when idle timeout aborted the stream (vs user navigation). */
  const staleStreamRef = useRef(false);

  useEffect(() => {
    let id = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!id) {
      id = crypto.randomUUID();
      sessionStorage.setItem(SESSION_STORAGE_KEY, id);
    }
    setSessionId(id);
  }, []);

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isStreaming]);

  useEffect(() => {
    const assistantIds = new Set(
      messages.filter((m) => m.role === "assistant").map((m) => m.id),
    );
    setFollowUpsByMessageId((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const k of Object.keys(next)) {
        if (!assistantIds.has(k)) {
          delete next[k];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [messages]);

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
      setConnectionLost(false);
      setLostRetryThread(null);
      staleStreamRef.current = false;
      abortControllerRef.current?.abort();
      abortControllerRef.current = new AbortController();
      const ac = abortControllerRef.current;

      const assistantId = crypto.randomUUID();
      const assistantPlaceholder: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
      };

      setMessages([...threadForApi, assistantPlaceholder]);
      setIsStreaming(true);

      let accumulatedAnswer = "";
      let sawMessageStop = false;

      const fail = (message: string) => {
        stripFailedAssistant();
        setConnectionLost(false);
        setLostRetryThread(null);
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
          signal: abortControllerRef.current!.signal,
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
          signal: abortControllerRef.current!.signal,
          onIdleTimeout: () => {
            staleStreamRef.current = true;
            abortControllerRef.current?.abort();
            stripFailedAssistant();
            setConnectionLost(true);
            setLostRetryThread(threadForApi);
            setIsStreaming(false);
          },
          onData: (value) => {
            if (!value || typeof value !== "object") return;
            const o = value as Record<string, unknown>;

            if (Object.prototype.hasOwnProperty.call(o, "follow_up_questions")) {
              const normalized = normalizeFollowUpQuestions(o.follow_up_questions);
              if (normalized !== null) {
                setFollowUpsByMessageId((prev) => ({
                  ...prev,
                  [assistantId]: normalized,
                }));
              }
            }

            if (Object.prototype.hasOwnProperty.call(o, "confidence")) {
              const conf = parseConfidence(o.confidence);
              if (conf !== undefined) {
                setMessages((prev) =>
                  prev.map((msg) =>
                    msg.id === assistantId ? { ...msg, confidence: conf } : msg,
                  ),
                );
              }
            }

            if (o.type === "message_stop") {
              sawMessageStop = true;
              setIsStreaming(false);

              const questionText =
                [...threadForApi]
                  .findLast((m) => m.role === "user")
                  ?.content.trim() ?? "";
              if (questionText && sessionId) {
                const payload: Record<string, string | number> = {
                  question: questionText,
                  answer: accumulatedAnswer,
                  session_id: sessionId,
                  message_id: assistantId,
                  timestamp: new Date().toISOString(),
                };
                const conf = parseConfidence(o.confidence);
                if (conf !== undefined) {
                  payload.confidence = conf;
                }
                void fetch(LOG_API, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify(payload),
                })
                  .then((res) => {
                    if (!res.ok) {
                      console.error(
                        "[laborpulse/log]",
                        res.status,
                        res.statusText,
                      );
                    }
                  })
                  .catch((err) => {
                    console.error("[laborpulse/log]", err);
                  });
              }
              return;
            }

            if (typeof o.token === "string" && o.token.length > 0) {
              const token = o.token;
              accumulatedAnswer += token;
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

        if (!sawMessageStop && !staleStreamRef.current) {
          fail(
            "The connection ended before the reply finished. You can retry.",
          );
          return;
        }
        staleStreamRef.current = false;
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          if (staleStreamRef.current) {
            staleStreamRef.current = false;
            return;
          }
          stripFailedAssistant();
          setIsStreaming(false);
          return;
        }
        fail(
          e instanceof Error ? e.message : "Something went wrong. Try again.",
        );
      } finally {
        if (abortControllerRef.current === ac) {
          abortControllerRef.current = null;
        }
      }
    },
    [apiUrl, sessionId, stripFailedAssistant],
  );

  /**
   * Appends a user turn and POSTs full `messages` + that turn to `/api/laborpulse`.
   * @param keepChipInInput — when true (follow-up chip), set input to the question text instead of clearing.
   */
  const sendUserTurn = useCallback(
    (text: string, options?: { keepChipInInput?: boolean }) => {
      const trimmed = text.trim();
      if (!trimmed || !sessionId || isStreaming) return;

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: trimmed,
      };
      const threadForApi = [...messages, userMsg];
      if (options?.keepChipInInput) {
        setInput(trimmed);
      } else {
        setInput("");
      }
      void runQuery(threadForApi);
    },
    [sessionId, isStreaming, messages, runQuery],
  );

  const beginNewQuery = useCallback(
    (text: string) => {
      sendUserTurn(text);
    },
    [sendUserTurn],
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
      setConnectionLost(false);
      setLostRetryThread(null);
      void runQuery(streamBanner.retryThread);
    });
  };

  const onConnectionLostRetry = () => {
    if (!lostRetryThread) return;
    startTransition(() => {
      setConnectionLost(false);
      const thread = lostRetryThread;
      setLostRetryThread(null);
      void runQuery(thread);
    });
  };

  const submitFeedback = useCallback(
    (messageId: string, vote: FeedbackVote, question: string, answer: string) => {
      if (!sessionId) return;
      if (committedFeedback[messageId]) return;
      if (feedbackInflightRef.current.has(messageId)) return;

      feedbackInflightRef.current.add(messageId);

      startTransition(async () => {
        addOptimisticFeedback({ messageId, vote });
        setPendingFeedbackId(messageId);
        setFeedbackInlineError((prev) => {
          const next = { ...prev };
          delete next[messageId];
          return next;
        });

        try {
          const res = await fetch(FEEDBACK_API, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              question,
              answer,
              feedback: vote,
              session_id: sessionId,
              message_id: messageId,
            }),
          });
          const data = (await res.json().catch(() => null)) as {
            error?: string;
            qa_feedback?: { feedback?: string };
          } | null;

          if (!res.ok) {
            throw new Error(
              typeof data?.error === "string"
                ? data.error
                : `Failed to save feedback (${res.status})`,
            );
          }

          const canonical =
            data?.qa_feedback?.feedback != null
              ? parseServerFeedback(String(data.qa_feedback.feedback))
              : vote;
          setCommittedFeedback((prev) => ({
            ...prev,
            [messageId]: canonical,
          }));
        } catch (e) {
          setFeedbackInlineError((prev) => ({
            ...prev,
            [messageId]:
              e instanceof Error ? e.message : "Could not save feedback.",
          }));
        } finally {
          feedbackInflightRef.current.delete(messageId);
          setPendingFeedbackId((current) =>
            current === messageId ? null : current,
          );
        }
      });
    },
    [sessionId, committedFeedback, addOptimisticFeedback],
  );

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

            {connectionLost && lostRetryThread && (
              <div
                className="flex flex-col gap-3 rounded-lg border border-amber-500/50 bg-amber-500/15 px-4 py-3 sm:flex-row sm:items-center sm:justify-between dark:border-amber-400/40 dark:bg-amber-500/10"
                role="alert"
              >
                <p className="text-sm text-amber-950 dark:text-amber-50">
                  Connection lost — the response may be incomplete.
                </p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="shrink-0 border-amber-500/50 text-amber-950 hover:bg-amber-500/20 dark:border-amber-400/40 dark:text-amber-50"
                  onClick={onConnectionLostRetry}
                >
                  <RotateCcw className="mr-2 size-4" aria-hidden />
                  Retry
                </Button>
              </div>
            )}

            <ul className="flex flex-col gap-4">
              {messages.map((m, index) => {
                const vote = optimisticFeedback[m.id];
                const feedbackLocked = Boolean(committedFeedback[m.id]);
                const feedbackPending = pendingFeedbackId === m.id;
                const thumbsDisabled =
                  feedbackLocked || feedbackPending || !sessionId;
                const showAssistantThumbs =
                  m.role === "assistant" && !isStreaming && sessionId;
                const qForFeedback = questionBeforeAssistant(messages, index);
                const followUps = followUpsByMessageId[m.id] ?? [];
                const chipsDisabled = isStreaming;

                return (
                  <li
                    key={m.id}
                    className={cn(
                      "flex w-full",
                      m.role === "user" ? "justify-end" : "justify-start",
                    )}
                  >
                    <div
                      className={cn(
                        "flex max-w-[85%] flex-col gap-2",
                        m.role === "user" ? "items-end" : "items-start",
                      )}
                    >
                      <div
                        className={cn(
                          "rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm",
                          m.role === "user"
                            ? "bg-primary text-primary-foreground"
                            : "border bg-card text-card-foreground",
                        )}
                      >
                        {m.role === "assistant" &&
                          typeof m.confidence === "number" &&
                          m.confidence < LOW_CONFIDENCE_THRESHOLD && (
                            <div
                              className="mb-3 flex gap-2 rounded-lg border border-amber-500/50 bg-amber-500/15 px-3 py-2 text-xs leading-snug text-amber-950 dark:border-amber-400/40 dark:bg-amber-500/10 dark:text-amber-50"
                              role="status"
                            >
                              <span className="shrink-0" aria-hidden>
                                🟡
                              </span>
                              <p>
                                This answer is based on limited data — treat it
                                as directional.
                              </p>
                            </div>
                          )}
                        {m.content}
                        {m.role === "assistant" &&
                          isStreaming &&
                          m.content === "" && (
                            <span className="inline-flex items-center gap-2 text-muted-foreground">
                              <Loader2
                                className="size-4 animate-spin"
                                aria-hidden
                              />
                              Thinking…
                            </span>
                          )}
                      </div>

                      {m.role === "assistant" && followUps.length > 0 && (
                        <div
                          className="flex max-w-[85%] flex-wrap gap-2 pl-1 pt-1"
                          role="group"
                          aria-label="Suggested follow-up questions"
                        >
                          {followUps.map((chip, chipIndex) => (
                            <Button
                              key={`${m.id}-fu-${chipIndex}`}
                              type="button"
                              variant="secondary"
                              size="sm"
                              className="h-auto max-w-full whitespace-normal rounded-full px-3 py-1.5 text-left text-xs font-normal leading-snug"
                              disabled={chipsDisabled || !sessionId}
                              onClick={() =>
                                sendUserTurn(chip, { keepChipInInput: true })
                              }
                            >
                              {chip}
                            </Button>
                          ))}
                        </div>
                      )}

                      {showAssistantThumbs && (
                        <div className="flex flex-col gap-1 pl-1">
                          <div className="flex items-center gap-1">
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className={cn(
                                "size-8 rounded-full",
                                vote === "up" &&
                                  "bg-primary/15 text-primary hover:bg-primary/20",
                              )}
                              disabled={thumbsDisabled}
                              aria-pressed={vote === "up"}
                              aria-label="Thumbs up"
                              onClick={() =>
                                submitFeedback(
                                  m.id,
                                  "up",
                                  qForFeedback,
                                  m.content,
                                )
                              }
                            >
                              <ThumbsUp
                                className="size-4"
                                aria-hidden
                                strokeWidth={vote === "up" ? 2.5 : 2}
                              />
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className={cn(
                                "size-8 rounded-full",
                                vote === "down" &&
                                  "bg-destructive/15 text-destructive hover:bg-destructive/20",
                              )}
                              disabled={thumbsDisabled}
                              aria-pressed={vote === "down"}
                              aria-label="Thumbs down"
                              onClick={() =>
                                submitFeedback(
                                  m.id,
                                  "down",
                                  qForFeedback,
                                  m.content,
                                )
                              }
                            >
                              <ThumbsDown
                                className="size-4"
                                aria-hidden
                                strokeWidth={vote === "down" ? 2.5 : 2}
                              />
                            </Button>
                          </div>
                          {feedbackInlineError[m.id] && (
                            <p
                              className="max-w-[min(100%,20rem)] text-xs text-destructive"
                              role="alert"
                            >
                              {feedbackInlineError[m.id]}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
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
