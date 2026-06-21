// ============================================================
// Hachi Web App — Work domain loader
// Wires jobs + schedules to the real gateway.
//
// Endpoints:
//   GET /jobs/active       → Job[]          → Run[]
//   GET /jobs/failed?limit=20 → JobFailure[]  → FailedTask[]
//   GET /schedules         → { schedules: Schedule[] } → Routine[]
//   PUT /schedules/{id}/enabled body { enabled }
//   DELETE /schedules/{id}
// ============================================================

import { apiGet, apiSend, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
import { useStore } from "../../store/store";
import type { Run, FailedTask, Routine } from "../types";

// ---- Gateway shapes (mirror Python dataclasses / dict exactly) ----

interface GatewayJob {
  job_id: string;
  kind: string;
  label: string;
  status: string;
}

interface GatewayJobFailure {
  job_id: string;
  kind: string;
  label: string;
  attempt: number;
  error: string;
  duration_seconds: number;
  timestamp: string;
}

interface GatewaySchedule {
  schedule_id: string;
  name: string;
  task: string;
  kind: "interval" | "cron";
  interval_seconds?: number | null;
  cron?: string | null;
  timezone: string;
  condition_skill?: string | null;
  enabled: boolean;
  created_at: string;
  last_run?: string | null;
  next_run?: string | null;
}

interface GatewaySchedulesResponse {
  schedules: GatewaySchedule[];
}

// ---- Stable numeric-id mapper ----
// Maps string gateway ids → stable numeric React keys.
// The string id is preserved on the UI object in a side field so mutations
// can resolve it back without keeping a separate Map in module scope.

const _idCounter = (() => {
  let n = 5000; // start above fixture range to avoid collisions
  const map = new Map<string, number>();
  return (strId: string): number => {
    let existing = map.get(strId);
    if (existing === undefined) {
      existing = ++n;
      map.set(strId, existing);
    }
    return existing;
  };
})();

// Parallel map from numeric UI id → string gateway schedule_id for mutations.
const _scheduleIdMap = new Map<number, string>();

// ---- Pure adapters ----

function adaptJob(raw: GatewayJob): Run {
  const numId = _idCounter(raw.job_id);
  // Map gateway status to UI status
  const statusMap: Record<string, Run["status"]> = {
    running: "working",
    active: "working",
    waiting: "on-watch",
    blocked: "blocked",
    done: "done",
  };
  const status: Run["status"] = statusMap[raw.status] ?? "on-watch";

  return {
    id: numId,
    title: raw.label || raw.kind || raw.job_id,
    status,
    progress: status === "working" ? 0.5 : status === "done" ? 1.0 : 0,
    spent: 0,
    budget: 1.0,
    meta: raw.kind,
    when: "running",
  };
}

function adaptJobFailure(raw: GatewayJobFailure): FailedTask {
  const numId = _idCounter(raw.job_id);
  const when = raw.timestamp
    ? new Date(raw.timestamp).toLocaleTimeString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "unknown";

  return {
    id: numId,
    title: raw.label || raw.kind || raw.job_id,
    status: "blocked",
    when,
    cost: 0,
    error: raw.error ?? "Unknown error",
    owner: raw.kind ?? undefined,
  };
}

function humanizeSchedule(raw: GatewaySchedule): string {
  if (raw.kind === "cron" && raw.cron) {
    // Simple humanization for common cron patterns
    const parts = raw.cron.split(" ");
    if (parts.length >= 5) {
      const [min, hour, dom, month, dow] = parts;
      if (dom === "*" && month === "*") {
        const dayStr =
          dow === "*"
            ? "every day"
            : dow === "1-5"
            ? "weekdays"
            : dow === "1,2,3,4,5"
            ? "weekdays"
            : `day ${dow}`;
        const timeStr = hour !== "*" && min !== "*" ? ` at ${hour.padStart(2, "0")}:${min.padStart(2, "0")}` : "";
        return `${dayStr}${timeStr}`;
      }
    }
    return raw.cron;
  }
  if (raw.kind === "interval" && raw.interval_seconds != null) {
    const secs = raw.interval_seconds;
    if (secs < 60) return `every ${secs}s`;
    if (secs < 3600) return `every ${Math.round(secs / 60)}m`;
    if (secs < 86400) return `every ${Math.round(secs / 3600)}h`;
    return `every ${Math.round(secs / 86400)}d`;
  }
  return "scheduled";
}

function humanizeNextRun(nextRun: string | null | undefined): string {
  if (!nextRun) return "—";
  try {
    const d = new Date(nextRun);
    const now = Date.now();
    const diff = d.getTime() - now;
    if (diff < 0) return "overdue";
    if (diff < 60000) return "< 1 min";
    if (diff < 3600000) return `in ${Math.round(diff / 60000)} min`;
    if (diff < 86400000) return `in ${Math.round(diff / 3600000)}h`;
    return d.toLocaleDateString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" });
  } catch {
    return nextRun;
  }
}

function adaptSchedule(raw: GatewaySchedule): Routine {
  const numId = _idCounter(raw.schedule_id);
  _scheduleIdMap.set(numId, raw.schedule_id);

  return {
    id: numId,
    title: raw.name || raw.task || raw.schedule_id,
    schedule: humanizeSchedule(raw),
    next: humanizeNextRun(raw.next_run),
    cost: 0,
    paused: !raw.enabled,
  };
}

// ---- Loader ----

// Tracks whether this domain has ever received real data from the gateway.
// In live mode: until the first successful fetch, seeded fixtures must NOT be
// shown, so on failure we clear the slice instead of leaving them in place.
let _hasEverSucceeded = false;

export async function loadWork(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures
  try {
    const [rawJobs, rawFailed, rawSchedules] = await Promise.all([
      apiGet<GatewayJob[]>("/jobs/active"),
      apiGet<GatewayJobFailure[]>("/jobs/failed", { limit: 20 }),
      apiGet<GatewaySchedulesResponse>("/schedules"),
    ]);

    const runs: Run[] = (rawJobs ?? []).map(adaptJob);
    const failed: FailedTask[] = (rawFailed ?? []).map(adaptJobFailure);
    const routines: Routine[] = (rawSchedules?.schedules ?? []).map(adaptSchedule);

    useStore.getState().hydrate({ runs, failed, routines });
    _hasEverSucceeded = true;
  } catch {
    if (!_hasEverSucceeded) {
      // First-load failure: clear seeded fixtures so we never present
      // fabricated data as if it were real gateway data.
      useStore.getState().hydrate({ runs: [], failed: [], routines: [] });
    }
    // Reconnecting flag is now managed centrally by client.ts via the
    // registered callback — no per-domain setReconnecting calls needed here.
  }
}

registerLoader("work", loadWork);

// ---- Mutation wrappers ----

/**
 * Toggle a routine's enabled state via PUT /schedules/{id}/enabled.
 * Applies optimistic store update immediately.
 */
export async function pauseRoutineLive(numericId: number): Promise<void> {
  // Optimistic: toggle in store (the store's pauseRoutine does this)
  useStore.getState().pauseRoutine(numericId);
  if (USE_MOCKS) return;

  const scheduleId = _scheduleIdMap.get(numericId);
  if (!scheduleId) return; // fixture or unknown id — store update was enough

  // Read the NEW paused state from store after the optimistic toggle
  const routine = useStore.getState().routines.find((r) => r.id === numericId);
  const enabled = routine ? !routine.paused : true;

  try {
    await apiSend<unknown>("PUT", `/schedules/${scheduleId}/enabled`, { enabled });
  } catch {
    // Revert the optimistic update on failure.
    // Network errors surface the reconnecting banner via client.ts callback.
    useStore.getState().pauseRoutine(numericId);
  }
}

/**
 * Delete a schedule via DELETE /schedules/{id}.
 * Removes from routines store optimistically.
 */
export async function deleteRoutineLive(numericId: number): Promise<void> {
  if (USE_MOCKS) {
    // Optimistic removal for mock mode
    useStore.getState().hydrate({
      routines: useStore.getState().routines.filter((r) => r.id !== numericId),
    });
    return;
  }

  const scheduleId = _scheduleIdMap.get(numericId);

  // Optimistic removal
  const previousRoutines = useStore.getState().routines;
  useStore.getState().hydrate({
    routines: previousRoutines.filter((r) => r.id !== numericId),
  });

  if (!scheduleId) return; // fixture — optimistic removal was enough

  try {
    await apiSend<unknown>("DELETE", `/schedules/${scheduleId}`);
  } catch {
    // Revert on failure.
    // Network errors surface the reconnecting banner via client.ts callback.
    useStore.getState().hydrate({ routines: previousRoutines });
  }
}
