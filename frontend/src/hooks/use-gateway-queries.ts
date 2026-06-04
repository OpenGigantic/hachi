import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  getGatewayConfig,
  getGatewayEvents,
  getGatewayModels,
  getGatewayReadiness,
  getGatewayRecentSessions,
} from "@/lib/api/tokoboros.functions";
import type { RecentSessionDto, RuntimeEventDto, SessionEventsResponse } from "@/types/api";

const GATEWAY_QUERY_RETRY_COUNT = 2;
const GATEWAY_QUERY_STALE_TIME_MS = 10_000;
const EVENTS_POLL_INTERVAL_MS = 1_500;
const EVENTS_FETCH_LIMIT = 100;
const EVENTS_KEEP_LIMIT = 500;

export const gatewayQueryKeys = {
  readiness: ["gateway", "readiness"] as const,
  recentSessions: (limit: number) => ["gateway", "recent-sessions", limit] as const,
  config: ["gateway", "config"] as const,
  models: ["gateway", "models"] as const,
  events: (sessionId: string) => ["gateway", "events", sessionId] as const,
};

export function useGatewayReadinessQuery() {
  return useQuery({
    queryKey: gatewayQueryKeys.readiness,
    queryFn: () => getGatewayReadiness(),
    staleTime: GATEWAY_QUERY_STALE_TIME_MS,
    retry: GATEWAY_QUERY_RETRY_COUNT,
    refetchOnWindowFocus: false,
  });
}

export function useGatewayRecentSessionsQuery(limit = 1) {
  return useQuery({
    queryKey: gatewayQueryKeys.recentSessions(limit),
    queryFn: async () => {
      const response = await getGatewayRecentSessions({ data: { limit } });
      return response as RecentSessionDto[];
    },
    staleTime: GATEWAY_QUERY_STALE_TIME_MS,
    retry: GATEWAY_QUERY_RETRY_COUNT,
    refetchOnWindowFocus: false,
  });
}

export function useGatewayConfigQuery() {
  return useQuery({
    queryKey: gatewayQueryKeys.config,
    queryFn: () => getGatewayConfig(),
    staleTime: GATEWAY_QUERY_STALE_TIME_MS,
    retry: GATEWAY_QUERY_RETRY_COUNT,
    refetchOnWindowFocus: false,
  });
}

export function useGatewayModelsQuery() {
  return useQuery({
    queryKey: gatewayQueryKeys.models,
    queryFn: () => getGatewayModels(),
    staleTime: GATEWAY_QUERY_STALE_TIME_MS,
    retry: GATEWAY_QUERY_RETRY_COUNT,
    refetchOnWindowFocus: false,
  });
}

export function useGatewayEventStreamQuery(sessionId: string) {
  const [events, setEvents] = useState<RuntimeEventDto[]>([]);
  const [pollingError, setPollingError] = useState(false);
  const cursorRef = useRef(0);

  useEffect(() => {
    cursorRef.current = 0;
    setEvents([]);
    setPollingError(false);
  }, [sessionId]);

  const streamQuery = useQuery({
    queryKey: gatewayQueryKeys.events(sessionId),
    enabled: Boolean(sessionId),
    queryFn: async () => {
      return (await getGatewayEvents({
        data: { sessionId, after: cursorRef.current, limit: EVENTS_FETCH_LIMIT },
      })) as SessionEventsResponse;
    },
    retry: GATEWAY_QUERY_RETRY_COUNT,
    refetchInterval: EVENTS_POLL_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    const batch = streamQuery.data;
    if (!batch) {
      return;
    }

    const incoming = batch.events;
    if (incoming.length > 0) {
      setEvents((previous) => {
        const appended: RuntimeEventDto[] = [];
        for (const event of incoming) {
          const sequence = event.sequence ?? -1;
          if (sequence >= 0 && sequence <= cursorRef.current) {
            continue;
          }
          appended.push(event);
        }

        if (appended.length === 0) {
          return previous;
        }

        const merged = [...previous, ...appended];
        return merged.slice(-EVENTS_KEEP_LIMIT);
      });
    }

    cursorRef.current = Math.max(cursorRef.current, batch.next_sequence);
    setPollingError(false);
  }, [streamQuery.data]);

  useEffect(() => {
    if (streamQuery.error) {
      setPollingError(true);
    }
  }, [streamQuery.error]);

  return {
    events,
    pollingError,
    isPolling: streamQuery.fetchStatus === "fetching",
  };
}
