import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  connectGatewaySession,
  getGatewayEvents,
  getGatewayReadiness,
  getGatewayRecentSessions,
  getGatewaySessionState,
  sendGatewayPrompt,
} from "@/lib/api/tokoboros.functions";
import { Skeleton } from "@/components/ui/skeleton";

interface SessionWorkbenchProps {
  surface: "main" | "console" | "operator";
}

const POLL_MAX_CONSECUTIVE_ERRORS = 3;

export function SessionWorkbench({ surface }: SessionWorkbenchProps) {
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [blockers, setBlockers] = useState<string[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [prompt, setPrompt] = useState("");
  const [reply, setReply] = useState("");
  const [events, setEvents] = useState<
    Array<{ event_type?: string; name?: string; time?: string }>
  >([]);
  const [nextSequence, setNextSequence] = useState(0);
  const [pending, setPending] = useState(false);
  const [recent, setRecent] = useState<Array<Record<string, unknown>>>([]);
  const [sessionState, setSessionState] = useState<Record<string, unknown> | null>(null);
  const nextSequenceRef = useRef(0);
  const pollFailureCountRef = useRef(0);

  useEffect(() => {
    let mounted = true;
    void (async () => {
      try {
        const [readiness, recentRows] = await Promise.all([
          getGatewayReadiness(),
          getGatewayRecentSessions({ data: { limit: 8 } }),
        ]);
        if (!mounted) {
          return;
        }
        setReady(readiness.ready);
        setBlockers(readiness.blockers);
        setRecent(recentRows);
      } catch {
        if (!mounted) {
          return;
        }
        toast.error("Unable to load gateway readiness.");
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    })();

    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    nextSequenceRef.current = nextSequence;
  }, [nextSequence]);

  useEffect(() => {
    if (!sessionId) {
      nextSequenceRef.current = 0;
      pollFailureCountRef.current = 0;
      setEvents([]);
      setNextSequence(0);
      return;
    }

    nextSequenceRef.current = 0;
    pollFailureCountRef.current = 0;
    setEvents([]);
    setNextSequence(0);
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) {
      return;
    }

    let cancelled = false;

    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const batch = await getGatewayEvents({
            data: { sessionId, after: nextSequenceRef.current, limit: 50 },
          });

          if (cancelled) {
            return;
          }

          if (batch.events.length > 0) {
            setEvents((rows) => [
              ...rows,
              ...batch.events.map((event) => ({
                event_type: event.event_type,
                name: event.name,
                time: event.time ?? event.timestamp,
              })),
            ]);
          }

          const resolvedNext = Math.max(nextSequenceRef.current, batch.next_sequence);
          nextSequenceRef.current = resolvedNext;
          setNextSequence(resolvedNext);
          pollFailureCountRef.current = 0;
        } catch {
          if (cancelled) {
            return;
          }

          pollFailureCountRef.current += 1;
          if (pollFailureCountRef.current >= POLL_MAX_CONSECUTIVE_ERRORS) {
            toast.error("Session event stream was lost. Reconnect to continue.");
            setSessionId("");
            setSessionState(null);
            setEvents([]);
            setNextSequence(0);
            nextSequenceRef.current = 0;
            pollFailureCountRef.current = 0;
            return;
          }

          toast.warning("Event polling temporarily failed. Retrying.");
        }
      })();
    }, 1500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [sessionId]);

  const canConnect = ready && !sessionId;
  const title = useMemo(() => {
    if (surface === "operator") return "Operator Workbench";
    if (surface === "console") return "Console";
    return "Main Session";
  }, [surface]);

  async function connect() {
    if (pending || sessionId) {
      return;
    }

    setPending(true);
    try {
      const result = await connectGatewaySession({
        data: {
          clientId: `web-${surface}`,
          surface,
          capabilities: ["events", "chat", "session_state"],
        },
      });
      setSessionId(result.session_id);
      nextSequenceRef.current = 0;
      pollFailureCountRef.current = 0;
      setNextSequence(0);
      const state = await getGatewaySessionState({
        data: { sessionId: result.session_id },
      });
      setSessionState(state as unknown as Record<string, unknown>);
    } catch {
      toast.error("Connect failed. Verify gateway runtime mode is enabled.");
    } finally {
      setPending(false);
    }
  }

  async function submitPrompt() {
    if (!sessionId || pending || !prompt.trim()) {
      return;
    }

    setPending(true);
    try {
      const response = await sendGatewayPrompt({
        data: {
          sessionId,
          prompt,
        },
      });
      setReply(response.response);
      setPrompt("");
    } catch {
      toast.error("Chat request failed.");
    } finally {
      setPending(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (!ready) {
    return (
      <div className="rounded border border-red-400/30 bg-red-500/5 p-4 text-sm text-red-100">
        <div className="text-base font-semibold">Gateway not ready</div>
        <div className="mt-2">Blockers: {blockers.length ? blockers.join(", ") : "unknown"}</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded border border-[#304a65] bg-[#081421] p-4">
        <div className="text-lg font-semibold text-[#d5e7ff]">{title}</div>
        <div className="mt-1 text-xs text-[#94adcd]">
          Session ID: {sessionId || "not connected"}
        </div>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            disabled={!canConnect || pending}
            onClick={connect}
            className="rounded border border-[#4fdbc8]/40 bg-[#4fdbc8]/10 px-3 py-1.5 text-xs font-semibold text-[#4fdbc8] disabled:opacity-40"
          >
            {pending ? "Connecting..." : "Connect"}
          </button>
        </div>
      </div>

      <div className="rounded border border-[#304a65] bg-[#081421] p-4">
        <label className="text-xs uppercase tracking-widest text-[#94adcd]">Prompt</label>
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          className="mt-2 h-24 w-full rounded border border-[#304a65] bg-[#070f18] p-2 text-sm text-[#d5e7ff]"
          placeholder="Type a message for Tokoboros..."
        />
        <div className="mt-2 flex justify-end">
          <button
            type="button"
            disabled={!sessionId || pending || !prompt.trim()}
            onClick={submitPrompt}
            className="rounded bg-[#4fdbc8] px-3 py-1.5 text-xs font-semibold text-[#00161e] disabled:opacity-40"
          >
            {pending ? "Sending..." : "Send"}
          </button>
        </div>
        {reply ? (
          <div className="mt-4 rounded border border-[#304a65] bg-[#070f18] p-3 text-sm text-[#d5e7ff]">
            {reply}
          </div>
        ) : null}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded border border-[#304a65] bg-[#081421] p-4">
          <div className="text-xs uppercase tracking-widest text-[#94adcd]">Recent sessions</div>
          {recent.length === 0 ? (
            <div className="mt-3 text-sm text-[#94adcd]">No persisted sessions yet.</div>
          ) : (
            <ul className="mt-3 space-y-2 text-xs text-[#d5e7ff]">
              {recent.map((row, index) => (
                <li key={index} className="rounded border border-[#304a65] bg-[#070f18] px-2 py-1">
                  {String(row.session_id ?? row.id ?? "session")} · {String(row.model ?? "unknown")}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded border border-[#304a65] bg-[#081421] p-4">
          <div className="text-xs uppercase tracking-widest text-[#94adcd]">Runtime events</div>
          {events.length === 0 ? (
            <div className="mt-3 text-sm text-[#94adcd]">No events yet.</div>
          ) : (
            <ul className="mt-3 max-h-48 space-y-2 overflow-auto text-xs text-[#d5e7ff]">
              {events.slice(-20).map((event, index) => (
                <li key={index} className="rounded border border-[#304a65] bg-[#070f18] px-2 py-1">
                  {event.event_type ?? event.name ?? "event"} {event.time ? `· ${event.time}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {sessionState ? (
        <div className="rounded border border-[#304a65] bg-[#081421] p-4 text-xs text-[#d5e7ff]">
          <div className="text-xs uppercase tracking-widest text-[#94adcd]">Session snapshot</div>
          <pre className="mt-2 overflow-auto text-[11px]">
            {JSON.stringify(sessionState, null, 2)}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
