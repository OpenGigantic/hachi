// ============================================================
// Hachi Web App — approvals + permission audit domain
// Wires: GET /approvals, POST /approvals/{request_id}, GET /permissions/audit
// ============================================================

import { apiGet, apiSend, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
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
    expiresAt: 0,   // no server-sent expiry — countdown disabled
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
    ts: new Date().toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }),
    op: raw.operation ?? raw.tool ?? "unknown",
    target: raw.target ?? "",
    policy: raw.reason ?? raw.operation ?? "",
    decision,
  };
}

// ============================================================
// 6. Loader: fetch both endpoints → hydrate → never throw
// ============================================================

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
    useStore.getState().setReconnecting(false);
  } catch {
    // Leave fixtures in place; flag reconnecting so UI degrades gracefully
    useStore.getState().setReconnecting(true);
  }
}

// Self-register on import so bootstrapLiveData() picks it up
registerLoader("approvals", loadApprovals);

// ============================================================
// 7. Mutation wrappers
//    Each does the optimistic store update first, then POSTs to gateway.
//    The optimistic update is unconditional; gateway call is skipped on mocks.
// ============================================================

async function postDecision(
  uiId: number,
  action: "approve" | "deny" | "allow_once",
  rationale?: string
): Promise<void> {
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
        useStore.getState().setReconnecting(true);
      }
    }
    // If requestId is unknown (e.g. fixture item), skip the POST — the
    // store action has already updated the UI optimistically.
  }
}

/**
 * Allow the request once (maps to "allow_once" on the gateway).
 * Optimistic: calls store.allowApproval immediately.
 */
export async function approve(id: number): Promise<void> {
  useStore.getState().allowApproval(id);
  await postDecision(id, "allow_once");
}

/**
 * Always allow this operation (maps to "approve" on the gateway).
 * Optimistic: calls store.alwaysApproval immediately.
 */
export async function allowAlways(id: number): Promise<void> {
  useStore.getState().alwaysApproval(id);
  await postDecision(id, "approve");
}

/**
 * Decline the request (maps to "deny" on the gateway).
 * Optimistic: calls store.declineApproval immediately.
 */
export async function decline(id: number, reason?: string): Promise<void> {
  useStore.getState().declineApproval(id, reason);
  await postDecision(id, "deny", reason);
}

/**
 * Decline all pending requests.
 * Optimistic: calls store.declineAll immediately, then fires individual POSTs.
 */
export async function declineAll(): Promise<void> {
  // Snapshot the current ids before the store action clears them
  const ids = useStore.getState().approvals.map((a) => a.id);
  useStore.getState().declineAll();
  // Fire individual denies in parallel; failures are soft (reconnecting flag)
  await Promise.allSettled(ids.map((id) => postDecision(id, "deny")));
}
