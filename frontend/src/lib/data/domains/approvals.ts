// ============================================================
// Hachi Web App — approvals + permission audit domain
// Wires: GET /approvals, POST /approvals/{request_id}, GET /permissions/audit
// ============================================================

import { apiGet, apiSend, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
import { invalidate } from "../cache";
import { useStore } from "../../store/store";
import type { Approval, AuditEntry } from "../types";

// ============================================================
// 1. Raw gateway shapes (mirror Python dicts exactly)
// ============================================================

interface GatewayPendingApproval {
  request_id: string;
  operation?: string;
  tool?: string;
  target?: string;
  reason?: string;
  policy_summary?: string;
}

interface GatewayApprovalsResponse {
  pending: GatewayPendingApproval[];
}

interface GatewayApprovalActionResponse {
  resolved: boolean;
}

interface GatewayPermissionAudit {
  operation?: string;
  tool?: string;
  target?: string;
  decision?: string;
  reason?: string;
}

// ============================================================
// 2. Stable numeric-id mapping (gateway uses string request_id)
//    Module-level: survives across re-renders, stays stable per session.
// ============================================================

/** gateway request_id string → stable UI numeric id */
const _idByRequestId = new Map<string, number>();
let _idCounter = 1000; // start above fixture ids (1, 2, …)

function stableNumericId(requestId: string): number {
  const existing = _idByRequestId.get(requestId);
  if (existing !== undefined) return existing;
  const n = ++_idCounter;
  _idByRequestId.set(requestId, n);
  return n;
}

/** Inverse lookup: UI numeric id → gateway request_id (for POST URL) */
function requestIdForUiId(uiId: number): string | undefined {
  for (const [rid, nid] of _idByRequestId) {
    if (nid === uiId) return rid;
  }
  return undefined;
}

// ============================================================
// 3. Risk heuristic: derive "low" | "medium" | "high" from operation/tool
// ============================================================

function deriveRisk(operation: string, tool: string): Approval["risk"] {
  const combined = `${operation} ${tool}`.toLowerCase();
  if (/shell|network|delete|rm |exec|subprocess|send_email|post|write_file|trash/.test(combined)) {
    return "high";
  }
  if (/write|update|patch|modify|create|upload|send/.test(combined)) {
    return "medium";
  }
  return "low";
}

// ============================================================
// 4. Icon picker: derive an icon name from the operation/tool string
// ============================================================

function deriveIcon(operation: string, tool: string): string {
  const combined = `${operation} ${tool}`.toLowerCase();
  if (/mail|email|gmail/.test(combined)) return "mail";
  if (/shell|exec|terminal|command/.test(combined)) return "terminal";
  if (/file|read|write|patch/.test(combined)) return "file";
  if (/network|http|fetch|url|api/.test(combined)) return "globe";
  if (/delete|rm |trash|remove/.test(combined)) return "trash";
  if (/calendar/.test(combined)) return "calendar";
  if (/slack|message|chat/.test(combined)) return "message-square";
  if (/search/.test(combined)) return "search";
  return "shield";
}

// ============================================================
// 5. Adapters: gateway shape → UI types (pure, total)
// ============================================================

function adaptApproval(raw: GatewayPendingApproval): Approval {
  const operation = raw.operation ?? "unknown";
  const tool = raw.tool ?? "";
  const target = raw.target ?? "";

  // The gateway's PermissionRequest model (src/hachi/core/permissions.py) has no
  // expiry/timeout/ttl/expires_at field — the 180 s server-side timeout is enforced
  // via threading.Event.wait() and never communicated to the client. We cannot
  // fabricate a meaningful countdown. expiresAt=0 / totalMs=0 signals "no expiry
  // sent", which the ApprovalCard uses to hide the countdown bar entirely rather
  // than show a misleading 0/0 display.
  return {
    id: stableNumericId(raw.request_id),
    icon: deriveIcon(operation, tool),
    request: `${tool || operation}`,
    why: raw.reason ?? "Hachi needs this to continue",
    action: `${tool || operation} → ${target}`,
    op: operation,
    policy: raw.policy_summary ?? "asks first",
    touches: target ? [target] : [],
    risk: deriveRisk(operation, tool),
    scope: operation,
    expiresAt: 0,   // no server-sent expiry — countdown bar hidden (see comment above)
    totalMs: 0,
  };
}

function adaptAuditEntry(raw: GatewayPermissionAudit): AuditEntry {
  const rawDecision = raw.decision ?? "automatic";

  // Normalise decision to one of the UI union members where possible
  let decision: AuditEntry["decision"];
  if (rawDecision === "approve" || rawDecision === "allowed") {
    decision = "allowed";
  } else if (rawDecision === "allow_once" || rawDecision === "allowed once") {
    decision = "allowed once";
  } else if (rawDecision === "deny" || rawDecision === "declined") {
    decision = "declined";
  } else if (rawDecision === "automatic") {
    decision = "automatic";
  } else {
    decision = rawDecision; // passthrough for any future gateway values
  }

  return {
    // Gateway's PermissionAuditSnapshot carries no timestamp field — render "—"
    // rather than fabricate the fetch time as if it were the decision time.
    ts: "—",
    op: raw.operation ?? raw.tool ?? "unknown",
    target: raw.target ?? "",
    policy: raw.reason ?? raw.operation ?? "",
    decision,
  };
}

// ============================================================
// 6. Loader: fetch both endpoints → hydrate → never throw
// ============================================================

// Tracks whether this domain has ever received real data from the gateway.
// In live mode: until the first successful fetch, fixtures must NOT be shown,
// so on failure we clear the slice instead of leaving seeded fixtures in place.
let _hasEverSucceeded = false;

export async function loadApprovals(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures

  try {
    const [approvalsRes, auditRes] = await Promise.all([
      apiGet<GatewayApprovalsResponse>("/approvals"),
      apiGet<GatewayPermissionAudit[]>("/permissions/audit", { limit: 50 }),
    ]);

    const approvals: Approval[] = (approvalsRes.pending ?? []).map(adaptApproval);
    const auditLog: AuditEntry[] = (Array.isArray(auditRes) ? auditRes : []).map(adaptAuditEntry);

    useStore.getState().hydrate({ approvals, auditLog });
    _hasEverSucceeded = true;
  } catch {
    if (!_hasEverSucceeded) {
      // First-load failure: clear fixtures so we never show fabricated data as real.
      useStore.getState().hydrate({ approvals: [], auditLog: [] });
    }
    // Reconnecting flag is now managed centrally by client.ts via the
    // registered callback — no per-domain setReconnecting calls needed here.
  } finally {
    useStore.getState().setLoaded("approvals");
  }
}

// Self-register on import so bootstrapLiveData() picks it up
registerLoader("approvals", loadApprovals);

// ============================================================
// 7. Mutation wrappers
//    Each POSTs to the gateway FIRST and mutates the store only once the
//    gateway confirms the decision (postDecision's outcome is "ok"). This
//    used to be optimistic-first — see BB-106: an optimistic mutation ahead
//    of a decision the gateway never receives leaves the UI showing
//    "allowed"/"declined" while the agent stays blocked until the 180s
//    server-side timeout auto-denies it. The gateway call is skipped (and
//    always reports "ok") when USE_MOCKS is set.
// ============================================================

/**
 * Outcome of a single decision POST.
 *  - "ok"         — 2xx from the gateway (or USE_MOCKS, which never calls it).
 *  - "unknown-id" — the numeric UI id has no known request_id (id-map miss).
 *                   NOT silent: the caller must surface this, not proceed as
 *                   if the gateway had been told.
 *  - "failed"     — apiSend threw (network error OR a decoded 4xx/5xx
 *                   ApiError — a non-2xx status is NOT a success).
 */
type DecisionOutcome = "ok" | "unknown-id" | "failed";

async function postDecision(
  uiId: number,
  action: "approve" | "deny" | "allow_once",
  rationale?: string
): Promise<DecisionOutcome> {
  let outcome: DecisionOutcome = "ok";
  if (!USE_MOCKS) {
    const requestId = requestIdForUiId(uiId);
    if (requestId) {
      try {
        await apiSend<GatewayApprovalActionResponse>(
          "POST",
          `/approvals/${requestId}`,
          { action, rationale: rationale ?? "" }
        );
      } catch {
        // A rejected apiSend is either a genuine network failure or a decoded
        // 4xx/5xx ApiError — either way it is NOT a success, and the caller
        // must not treat it as one (see BB-106). client.ts's reconnect
        // callback only covers network errors; HTTP-status failures reach
        // only here, so this is the one place that can report them.
        outcome = "failed";
      }
    } else {
      // Unknown request_id (id-map miss). Report it honestly instead of
      // returning as if the gateway had been told (see BB-106).
      outcome = "unknown-id";
    }
  }
  // Mark the approvals domain stale so a revisit to /approvals refetches
  // the updated pending list rather than showing the now-stale snapshot.
  // Fires on every outcome, including failure — a stale cached list is wrong
  // after a failed decision too.
  invalidate("approvals");
  return outcome;
}

const DECISION_FAILED_MESSAGE =
  "Couldn't send that decision — Hachi is still waiting. Try again.";

/**
 * Allow the request once (maps to "allow_once" on the gateway).
 * Non-optimistic: awaits the gateway POST first and mutates the store only
 * on success, so a failed or unmapped decision never removes the card while
 * pretending it worked (see BB-106).
 */
export async function approve(id: number): Promise<void> {
  const outcome = await postDecision(id, "allow_once");
  if (outcome === "ok") {
    useStore.getState().allowApproval(id);
    return;
  }
  useStore.getState().showToast(DECISION_FAILED_MESSAGE, "blocked");
}

/**
 * Always allow this operation (maps to "approve" on the gateway).
 * Non-optimistic — see approve().
 */
export async function allowAlways(id: number): Promise<void> {
  const outcome = await postDecision(id, "approve");
  if (outcome === "ok") {
    useStore.getState().alwaysApproval(id);
    return;
  }
  useStore.getState().showToast(DECISION_FAILED_MESSAGE, "blocked");
}

/**
 * Decline the request (maps to "deny" on the gateway).
 * Non-optimistic — see approve().
 */
export async function decline(id: number, reason?: string): Promise<void> {
  const outcome = await postDecision(id, "deny", reason);
  if (outcome === "ok") {
    useStore.getState().declineApproval(id, reason);
    return;
  }
  useStore.getState().showToast(DECISION_FAILED_MESSAGE, "blocked");
}

/**
 * Decline all pending requests.
 * Resolves each id individually and clears from the store only the ones the
 * gateway actually confirmed — the bulk store.declineAll() action is NOT
 * called optimistically, so a partial failure leaves the still-pending
 * approvals on screen instead of disappearing along with the rest.
 */
export async function declineAll(): Promise<void> {
  // Snapshot the current ids before any store mutation.
  const ids = useStore.getState().approvals.map((a) => a.id);
  const outcomes = await Promise.allSettled(ids.map((id) => postDecision(id, "deny")));
  let failedCount = 0;
  outcomes.forEach((result, i) => {
    if (result.status === "fulfilled" && result.value === "ok") {
      useStore.getState().declineApproval(ids[i]);
    } else {
      failedCount += 1;
    }
  });
  if (failedCount > 0) {
    useStore.getState().showToast(
      failedCount === 1
        ? "Couldn't send 1 decision — Hachi is still waiting. Try again."
        : `Couldn't send ${failedCount} decisions — Hachi is still waiting. Try again.`,
      "blocked"
    );
  }
}

/**
 * Resolve a pending approval by its gateway request_id (allow once).
 * Returns false when the id is unknown (never seen by the /approvals
 * loader) instead of silently minting a numeric id for it — an id we've
 * never actually seen from the gateway has no business being posted as if
 * it were real (see T-18 / BB-106).
 */
export async function approveByRequestId(requestId: string): Promise<boolean> {
  const uiId = _idByRequestId.get(requestId);
  if (uiId === undefined) return false;
  await approve(uiId);
  return true;
}

/**
 * Decline a pending approval by its gateway request_id. Returns false if
 * unknown — see approveByRequestId().
 */
export async function declineByRequestId(requestId: string, reason?: string): Promise<boolean> {
  const uiId = _idByRequestId.get(requestId);
  if (uiId === undefined) return false;
  await decline(uiId, reason);
  return true;
}

// ============================================================
// 8. Real-time Leash sync
//    chat.ts calls loadApprovals() on the SSE `permission.requested` and
//    `permission.decided` events so the Leash screen mirrors the authoritative
//    /approvals registry (real request_ids → cards remain approvable). No
//    synthetic-id entries are created — the registry is the single source of
//    truth, which keeps the approve/deny POST (/approvals/{request_id}) working.
// ============================================================
