// ============================================================
// Hachi Web App — approvals domain regression tests
//
// T-17 (CF-4b): in-context approval decisions must resolve by the gateway's
//               own request_id, not by fuzzy-matching display text that is
//               permanently the fallback "Permission requested" — which made
//               the matcher fall through to approvals[0] unconditionally.
// T-18 (CF-4c): a rejected or unmapped decision POST must never be reported
//               to the user as a success (bare `catch {}` / silent id-miss).
// ============================================================

import { beforeEach, describe, expect, it, vi } from "vitest";

const apiGet = vi.fn();
const apiSend = vi.fn();

// Canonical mock block (see store.test.ts:17-37). approvals.ts is imported
// by chat.ts and vice versa (store.ts statically imports chat.ts's live
// functions, and chat.ts statically imports useStore + approvals.ts), so
// importing either module here pulls in ../client, ../sse and
// ../../notifications at module scope — all three need mocking or the
// import graph explodes / performs real network calls.
vi.mock("../client", () => ({
  USE_MOCKS: false,
  apiGet: (...args: unknown[]) => apiGet(...args),
  apiSend: (...args: unknown[]) => apiSend(...args),
  registerReconnectCallback: vi.fn(),
  isNetworkError: () => false,
  API_BASE: "/api",
  authHeaders: () => ({}),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  },
}));

vi.mock("../sse", () => ({
  streamSSE: vi.fn(),
}));

vi.mock("../../notifications", () => ({
  ensureNotificationPermission: vi.fn(),
  notifyApprovalPending: vi.fn(),
}));

import { useStore } from "../../store/store";
import { loadApprovals, approve, declineAll } from "./approvals";
import { allowLeashLive, handleSseMessage } from "./chat";

interface RawPending {
  request_id: string;
  operation?: string;
  tool?: string;
  target?: string;
  reason?: string;
}

/** Point the mocked apiGet at a pending-approvals fixture (and an empty audit log). */
function mockPending(items: RawPending[]): void {
  apiGet.mockImplementation((path: string) => {
    if (path === "/approvals") return Promise.resolve({ pending: items });
    if (path === "/permissions/audit") return Promise.resolve([]);
    return Promise.resolve(undefined);
  });
}

beforeEach(() => {
  apiGet.mockReset();
  apiSend.mockReset();
  mockPending([]);
  useStore.setState({
    approvals: [],
    toasts: [],
    inContext: null,
    history: [],
  } as unknown as Parameters<typeof useStore.setState>[0]);
});

describe("in-context approval resolution (CF-4b / T-17)", () => {
  it("resolves the approval the user clicked, not approvals[0]", async () => {
    // Two approvals pending; the numeric-id map is populated the way
    // production populates it — via the real loader/adapter path.
    mockPending([
      {
        request_id: "req-A",
        operation: "write_file",
        tool: "write_file",
        target: "/tmp/a.txt",
        reason: "save draft",
      },
      {
        request_id: "req-B",
        operation: "send_email",
        tool: "send_email",
        target: "priya@studio.co",
        reason: "reply to Priya",
      },
    ]);
    await loadApprovals();
    apiSend.mockResolvedValue({ resolved: true });

    // The leash prompt for the SECOND approval. Deliberately use the literal
    // fallback text that production ALWAYS sends today (chat.ts's field-name
    // mismatch — see "maps the real SSE field names" below) so this test
    // exercises the real end-to-end bug: a text needle that can never match
    // any pending approval's `request` field, alongside the real request_id.
    // If this used matching text instead (e.g. "send_email"), the old fuzzy
    // matcher would coincidentally succeed and this test would pass before
    // the fix — which is exactly the trap the spec warns about.
    useStore.getState().hydrate({
      inContext: { msgId: 1, requestId: "req-B", request: "Permission requested", action: "", why: "" },
    });

    await allowLeashLive();

    expect(apiSend).toHaveBeenCalledWith("POST", "/approvals/req-B", expect.anything());
  });

  it("does not fall back to the only pending approval when the leash's id does not match it", async () => {
    // Exactly ONE approval pending — the counter-intuitive case (R2). The old
    // fuzzy matcher's fallback to approvals[0] is "right by accident" only
    // when the lone pending item happens to be the one the user meant. Here
    // it deliberately ISN'T (the leash carries a different / stale
    // request_id) — silently touching the only pending approval anyway would
    // be wrong, not merely lucky.
    mockPending([
      {
        request_id: "req-A",
        operation: "write_file",
        tool: "write_file",
        target: "/tmp/a.txt",
        reason: "save draft",
      },
    ]);
    await loadApprovals();
    apiSend.mockResolvedValue({ resolved: true });

    // Same fallback text production always sends (see the previous test) —
    // the needle can't match "write_file", so the OLD code's only route to
    // "correctness" here is the bare `approvals[0]` fallback, which happens
    // to be this lone pending item despite not being what the user clicked.
    useStore.getState().hydrate({
      inContext: { msgId: 1, requestId: "req-Z", request: "Permission requested", action: "", why: "" },
    });

    await allowLeashLive();

    expect(apiSend).not.toHaveBeenCalled();
  });

  it("maps the real SSE field names onto the in-context leash", () => {
    handleSseMessage("permission.requested", {
      request_id: "req-X",
      operation: "send_email",
      tool: "send_email",
      target: "priya@studio.co",
      reason: "she's been waiting two days for your reply",
      policy_summary: "asks first",
    });

    const ic = useStore.getState().inContext;
    expect(ic?.request).toBe("send_email");
    expect(ic?.requestId).toBe("req-X");
    expect(ic?.action).toBe("send_email → priya@studio.co");
    expect(ic?.why).toBe("she's been waiting two days for your reply");
  });
});

describe("approval decisions must not fail silently (CF-4c / T-18)", () => {
  it("keeps the approval and warns when the decision POST fails", async () => {
    mockPending([
      {
        request_id: "req-500",
        operation: "shell_exec",
        tool: "shell_exec",
        target: "rm -rf tmp",
        reason: "cleanup",
      },
    ]);
    await loadApprovals();
    apiSend.mockRejectedValue(new Error("500 Internal Server Error"));

    const id = useStore.getState().approvals[0].id;
    await approve(id);

    // The gateway is still blocked — the card must NOT have vanished.
    expect(useStore.getState().approvals).toHaveLength(1);
    expect(useStore.getState().toasts.some((t) => t.tone === "blocked")).toBe(true);
  });

  it("does not silently drop the approval when the request_id is unmapped", async () => {
    mockPending([
      {
        request_id: "req-501",
        operation: "write_file",
        tool: "write_file",
        target: "/tmp/x",
        reason: "save",
      },
    ]);
    await loadApprovals();
    apiSend.mockResolvedValue({ resolved: true });

    await approve(999999); // never registered by stableNumericId

    expect(apiSend).not.toHaveBeenCalled();
    expect(useStore.getState().approvals).toHaveLength(1);
    expect(useStore.getState().toasts.some((t) => t.tone === "blocked")).toBe(true);
  });

  it("declineAll does not clear approvals whose POST failed", async () => {
    mockPending([
      { request_id: "req-600", operation: "write_file", tool: "write_file", target: "/tmp/a", reason: "x" },
      { request_id: "req-601", operation: "send_email", tool: "send_email", target: "b@x.co", reason: "y" },
    ]);
    await loadApprovals();
    const okId = useStore.getState().approvals[0].id; // req-600 — its POST will succeed
    const failId = useStore.getState().approvals[1].id; // req-601 — its POST will fail

    apiSend.mockImplementation((_method: string, path: string) => {
      if (path === "/approvals/req-601") return Promise.reject(new Error("500"));
      return Promise.resolve({ resolved: true });
    });

    await declineAll();

    const remainingIds = useStore.getState().approvals.map((a) => a.id);
    expect(remainingIds).toEqual([failId]);
    expect(remainingIds).not.toContain(okId);
  });
});
