// Regression lock for audit finding L1/gap 2 investigation — the scripted demo
// run queue (store.ts's _runQueue/runTick/finishRun, which produces the
// hardcoded "Summarised 6 PRs — 2 need review. Posted to #eng." result) must
// NEVER fire when dataMode === 'live'. It is confined to mock/demo mode.
//
// This was investigated as part of BB-36 (Work screen live-wiring audit) and
// found to already be correctly gated — every entry point into the scripted
// queue (openRun, runAllowLeash, runAlwaysAllow, runDenyLeash, stopRun) has an
// explicit `dataMode === 'live'` early-return that fully replaces the mock
// behaviour before the scripted queue could ever be seeded. No code change
// was needed; this test pins the invariant so a future edit can't silently
// reintroduce fake data into a real run.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Approval } from "../data/types";

vi.mock("../data/client", () => ({
  USE_MOCKS: false,
  apiGet: vi.fn().mockResolvedValue(undefined),
  apiSend: vi.fn().mockResolvedValue(undefined),
  registerReconnectCallback: vi.fn(),
  isNetworkError: () => false,
  API_BASE: "/api",
  authHeaders: () => ({}),
  ApiError: class ApiError extends Error {},
}));

vi.mock("../data/sse", () => ({
  streamSSE: vi.fn(),
}));

vi.mock("../notifications", () => ({
  ensureNotificationPermission: vi.fn(),
  notifyApprovalPending: vi.fn(),
}));

import { useStore } from "./store";

const FAKE_RESULT = "Summarised 6 PRs — 2 need review. Posted to #eng.";

beforeEach(() => {
  useStore.getState().hydrate({
    dataMode: "live",
    runView: { id: 1, title: "Weekly PR summary", status: "working", progress: 0.5, spent: 0, budget: 1 },
    runLines: [{ text: "hachi ▸ run weekly-pr-summary --watch" }],
    runDone: false,
    runStreaming: true,
    runLeash: null,
    runSpent: 0,
    finished: [],
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("live run — scripted demo queue must never fire (BB-36 gap-2 investigation)", () => {
  it("openRun() in live mode does not seed the mock queue or touch runView", () => {
    const before = useStore.getState().runView;
    useStore.getState().openRun(1, "work");
    // Live branch just early-returns (runView untouched, no mock seeding).
    expect(useStore.getState().runView).toBe(before);
    expect(useStore.getState().finished.some((f) => f.result === FAKE_RESULT)).toBe(false);
  });

  it("runDenyLeash() in live mode marks done WITHOUT the hardcoded demo result", () => {
    useStore.getState().runDenyLeash();
    const state = useStore.getState();
    expect(state.runDone).toBe(true);
    // archiveRun() (which produces the fake result) must NOT have run in live mode —
    // the live branch does not push into `finished` at all.
    expect(state.finished.some((f) => f.result === FAKE_RESULT)).toBe(false);
  });

  it("stopRun() in live mode marks done WITHOUT the hardcoded demo result", () => {
    useStore.getState().stopRun();
    const state = useStore.getState();
    expect(state.runDone).toBe(true);
    expect(state.finished.some((f) => f.result === FAKE_RESULT)).toBe(false);
  });

  it("runAllowLeash() in live mode does not start the scripted mock interval", () => {
    useStore.getState().hydrate({ runLeash: { request: "x", action: "y", why: "z" } });
    useStore.getState().runAllowLeash();
    // Live branch clears the leash + resumes streaming without scheduling runTick.
    const state = useStore.getState();
    expect(state.runLeash).toBeNull();
    expect(state.finished.some((f) => f.result === FAKE_RESULT)).toBe(false);
  });
});

// ---- T1 / BB-77: tickApprovals must not auto-decline live approvals with expiresAt=0 ----

function approvalWith(overrides: Partial<Approval>): Approval {
  return {
    id: 1,
    icon: "mail",
    request: "test request",
    why: "test why",
    action: "test action",
    op: "test-op",
    policy: "asks first",
    touches: [],
    risk: "low",
    scope: "test",
    expiresAt: Date.now() + 60000,
    totalMs: 60000,
    ...overrides,
  };
}

describe("tickApprovals — live sentinel and mock expiry (BB-77)", () => {
  it("live approval with expiresAt=0 survives ticking", () => {
    useStore.setState({
      dataMode: "live",
      approvals: [approvalWith({ expiresAt: 0 })],
      history: [],
    });
    useStore.getState().tickApprovals();
    expect(useStore.getState().approvals).toHaveLength(1);
    expect(useStore.getState().history).toHaveLength(0);
  });

  it("mock past-expiry approval is auto-declined", () => {
    useStore.setState({
      dataMode: "mock",
      approvals: [approvalWith({ expiresAt: Date.now() - 1000 })],
      history: [],
    });
    useStore.getState().tickApprovals();
    expect(useStore.getState().approvals).toHaveLength(0);
    expect(useStore.getState().history[0].decision).toMatch(/timed out/);
  });
});
