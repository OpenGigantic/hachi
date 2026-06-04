import { useEffect, useState } from "react";
import { toast } from "sonner";

import { connectGatewaySession } from "@/lib/api/tokoboros.functions";
import {
  useGatewayEventStreamQuery,
  useGatewayReadinessQuery,
  useGatewayRecentSessionsQuery,
} from "@/hooks/use-gateway-queries";
import type { RuntimeEventDto } from "@/types/api";
import { Skeleton } from "@/components/ui/skeleton";

function formatTime(event: RuntimeEventDto) {
  return event.time ?? event.timestamp ?? "";
}

export function LogsScreen() {
  const [sessionId, setSessionId] = useState("");
  const [pending, setPending] = useState(false);
  const readinessQuery = useGatewayReadinessQuery();
  const recentSessionsQuery = useGatewayRecentSessionsQuery(1);
  const { events, pollingError } = useGatewayEventStreamQuery(sessionId);

  useEffect(() => {
    if (sessionId) {
      return;
    }
    const recentRows = recentSessionsQuery.data ?? [];
    const lastSessionId = String(recentRows[0]?.session_id ?? recentRows[0]?.id ?? "");
    if (lastSessionId) {
      setSessionId(lastSessionId);
    }
  }, [recentSessionsQuery.data, sessionId]);

  useEffect(() => {
    if (readinessQuery.error || recentSessionsQuery.error) {
      toast.error("Failed to load log viewer state.");
    }
  }, [readinessQuery.error, recentSessionsQuery.error]);

  const loading = readinessQuery.isPending || recentSessionsQuery.isPending;
  const ready = readinessQuery.data?.ready ?? false;
  const blockers = readinessQuery.data?.blockers ?? [];

  async function startSession() {
    setPending(true);
    try {
      const result = await connectGatewaySession({
        data: {
          clientId: "web-logs",
          surface: "console",
          capabilities: ["events"],
        },
      });
      const connectedSessionId = result.session_id;
      setSessionId(connectedSessionId);
      toast.success("Connected. Streaming logs.");
    } catch {
      toast.error("Unable to start a log session.");
    } finally {
      setPending(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-80 w-full" />
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
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-lg font-semibold text-[#d5e7ff]">System Logs</div>
            <div className="mt-1 text-xs text-[#94adcd]">
              Session: {sessionId || "not connected"}
            </div>
          </div>
          <button
            type="button"
            onClick={startSession}
            disabled={pending}
            className="rounded bg-[#4fdbc8] px-3 py-1.5 text-xs font-semibold text-[#00161e] disabled:opacity-40"
          >
            {pending ? "Connecting..." : "Start session"}
          </button>
        </div>
      </div>

      {pollingError ? (
        <div className="rounded border border-amber-300/30 bg-amber-400/10 p-3 text-xs text-amber-100">
          Reconnect in progress. Last poll failed, retrying automatically.
        </div>
      ) : null}

      <div className="rounded border border-[#304a65] bg-[#081421] p-4">
        <div className="mb-3 text-xs uppercase tracking-widest text-[#94adcd]">
          Live event stream
        </div>
        {events.length === 0 ? (
          <div className="rounded border border-[#304a65] bg-[#070f18] p-4 text-sm text-[#94adcd]">
            No events yet. Start a session or wait for activity.
          </div>
        ) : (
          <div
            role="log"
            aria-live="polite"
            className="max-h-[60vh] space-y-2 overflow-auto rounded border border-[#304a65] bg-[#070f18] p-3"
          >
            {events.map((event, index) => (
              <div
                key={`${event.sequence ?? index}-${event.event_type ?? event.name ?? "event"}`}
                className="rounded border border-[#304a65] bg-[#091827] px-2 py-1.5 text-xs text-[#d5e7ff]"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold text-[#4fdbc8]">
                    {event.event_type ?? event.name ?? "event"}
                  </span>
                  <span className="text-[#94adcd]">{formatTime(event)}</span>
                </div>
                <pre className="mt-1 overflow-auto text-[11px] text-[#c7dcf7]">
                  {JSON.stringify(event.payload, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
