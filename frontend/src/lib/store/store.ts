// ============================================================
// Hachi Web App — Zustand store
// Single store; persist only operator + theme.
// Actions mirror prototype logic methods (dc.html 1800–2160).
//
// T-63: this file is now a composition root. Every concern (state fields +
// actions) lives in its own file under ./slices/, one owner per concern —
// this file only assembles them (plus `hydrate`, which spans all of them by
// design, and the persist/localStorage wiring). See
// docs/audit/2026-07-19/tasks/T-63-frontend-store-slices.md.
// ============================================================

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { USE_MOCKS, registerReconnectCallback } from "../data/client";
// NOTE (T-22 / BB-123): frontend/src/lib/data/fixtures.ts (the ~400-line demo-fixture
// corpus) is deliberately NOT imported here at module scope. It used to be, and every
// binding was referenced unconditionally inside a domainDefaults() helper (removed by
// T-63 -- every slice now inlines its own live-only defaults directly) via a
// USE_MOCKS-gated ternary (fixture-or-live-default) -- but USE_MOCKS is a *computed*
// boolean (client.ts), not a literal Vite/Rollup can constant-fold, so the bundler could
// never prove those ternary arms dead and shipped the whole fixture corpus (incl. a
// hardcoded `priya@studio.co` recipient and a fabricated $0.02 cost) to every production
// build, mock mode or not. Fixtures are now reachable only via the dynamic `import(...)`
// inside seedMockFixtures() below, on the mock-only path -- see scripts/check_bundle.py,
// which fails the gate if this ever regresses back to a static import.
import { createActivitySlice, type ActivityState } from "./slices/activity";
import { createApprovalsSlice, type ApprovalsActions, type ApprovalsState } from "./slices/approvals";
import { createAuthoringSlice, type AuthoringActions, type AuthoringState } from "./slices/authoring";
import { createBudgetSlice, type BudgetActions, type BudgetState } from "./slices/budget";
import { createChatSlice, type ChatActions, type ChatState } from "./slices/chat";
import { createDataModeSlice, type DataModeState } from "./slices/dataMode";
import { createEvalLoopSlice, type EvalLoopState } from "./slices/evalLoop";
import { createHomeUiSlice, type HomeUiState } from "./slices/homeUi";
import {
  createIntegrationsSlice,
  type IntegrationsActions,
  type IntegrationsState,
} from "./slices/integrations";
import { createLiveRunSlice, type LiveRunActions, type LiveRunState } from "./slices/liveRun";
import { createMemorySlice, type MemoryActions, type MemoryState } from "./slices/memory";
import { createOgSlice, type OgActions, type OgState } from "./slices/og";
import { createPaletteSlice, type PaletteActions, type PaletteState } from "./slices/palette";
import {
  createPermissionsSlice,
  type PermissionsActions,
  type PermissionsState,
} from "./slices/permissions";
import { createPersistSlice, type PersistActions, type PersistState } from "./slices/persist";
import { createProviderSlice, type ProviderActions, type ProviderState } from "./slices/provider";
import { createReviewSlice, type ReviewState } from "./slices/review";
import { createSessionsSlice, type SessionsActions, type SessionsState } from "./slices/sessions";
import { createSetupSlice, type SetupActions, type SetupState } from "./slices/setup";
import { capMessages } from "./slices/shared";
import { createSkillsSlice, type SkillsActions, type SkillsState } from "./slices/skills";
import { createToastsSlice, type ToastsActions, type ToastsState } from "./slices/toasts";
import {
  createWorkQueueSlice,
  type WorkQueueActions,
  type WorkQueueState,
} from "./slices/workQueue";

// ============================================================
// State shape — one owner per concern (T-63). See each ./slices/* file for
// the fields it owns; this interface only assembles them.
// ============================================================
interface State
  extends ToastsState,
    PaletteState,
    PersistState,
    DataModeState,
    EvalLoopState,
    ActivityState,
    HomeUiState,
    ReviewState,
    OgState,
    AuthoringState,
    IntegrationsState,
    PermissionsState,
    SessionsState,
    SkillsState,
    MemoryState,
    BudgetState,
    ProviderState,
    SetupState,
    ApprovalsState,
    WorkQueueState,
    LiveRunState,
    ChatState {}

// ============================================================
// Actions shape — one owner per concern (T-63), plus `hydrate`, which is
// intentionally NOT part of any single slice: its whole job is to accept a
// patch shaped like (a subset of) the FULL State, so it lives at the
// composition root instead.
// ============================================================
interface Actions
  extends ToastsActions,
    PaletteActions,
    PersistActions,
    OgActions,
    AuthoringActions,
    IntegrationsActions,
    PermissionsActions,
    SessionsActions,
    SkillsActions,
    MemoryActions,
    BudgetActions,
    ProviderActions,
    SetupActions,
    ApprovalsActions,
    WorkQueueActions,
    LiveRunActions,
    ChatActions {
  // live-data hydration (foundation)
  // Shallow-merge a slice of gateway-derived state into the store. Domain
  // loaders call this after mapping a gateway response to UI types.
  hydrate: (patch: Partial<State>) => void;
}

// ============================================================
// Composition root type — the slice files import this (type-only) to type
// their own `set`/`get` against the FULL store, not just their own slice.
// This is a type-only back-edge (erased at compile time, zero runtime
// import), the standard zustand "slices pattern" -- see
// https://zustand.docs.pmnd.rs/guides/typescript#slices-pattern.
// ============================================================
export type Store = State & Actions;

export const useStore = create<State & Actions>()(
  persist(
    (set, get, api) => ({
      // ---- slices (T-63) — one owner per concern ----
      ...createToastsSlice(set, get, api),
      ...createPaletteSlice(set, get, api),
      ...createPersistSlice(set, get, api),
      ...createDataModeSlice(set, get, api),
      ...createEvalLoopSlice(set, get, api),
      ...createActivitySlice(set, get, api),
      ...createHomeUiSlice(set, get, api),
      ...createReviewSlice(set, get, api),
      ...createOgSlice(set, get, api),
      ...createAuthoringSlice(set, get, api),
      ...createIntegrationsSlice(set, get, api),
      ...createPermissionsSlice(set, get, api),
      ...createSessionsSlice(set, get, api),
      ...createSkillsSlice(set, get, api),
      ...createMemorySlice(set, get, api),
      ...createBudgetSlice(set, get, api),
      ...createProviderSlice(set, get, api),
      ...createSetupSlice(set, get, api),
      ...createApprovalsSlice(set, get, api),
      ...createWorkQueueSlice(set, get, api),
      ...createLiveRunSlice(set, get, api),
      ...createChatSlice(set, get, api),

      // live-data hydration (foundation)
      //
      // T-63 / BB-NNN: `patch` is declared Partial<State> (a real state-only
      // type, not a lie) -- but that declaration is a compile-time-only
      // promise. TypeScript types are erased at runtime, so a caller that
      // bypasses the static check (a dynamically-built object, `as any`,
      // etc.) could still hand hydrate a key that collides with an action
      // name, and a plain `set()` would happily overwrite that action with
      // whatever value came with it. The old implementation additionally
      // cast to `Partial<State & Actions>` before calling set(), which made
      // that hole part of the *declared* contract instead of an oversight.
      //
      // Fix: validate at the boundary instead of widening past it. Every
      // action on this store is a function and no State field ever is, so
      // "does this key currently hold a function" is a simple,
      // self-maintaining whitelist -- it needs no separate list of valid
      // keys to keep in sync as slices are added or removed. Only keys that
      // both (a) already exist on the live store and (b) are not functions
      // are forwarded to set(); everything else (including an
      // action-colliding key) is silently dropped, exactly as a value
      // genuinely typed Partial<State> would require.
      hydrate: (patch) => {
        const current = get() as unknown as Record<string, unknown>;
        const filtered: Record<string, unknown> = {};
        for (const key of Object.keys(patch)) {
          if (key in current && typeof current[key] !== "function") {
            filtered[key] = (patch as Record<string, unknown>)[key];
          }
        }
        const safePatch = filtered as Partial<State>;
        if (Array.isArray(safePatch.messages)) {
          set({ ...safePatch, messages: capMessages(safePatch.messages) });
          return;
        }
        set(safePatch);
      },
    }),
    {
      name: "hachi-store",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        operator: state.operator,
        theme: state.theme,
      }),
    }
  )
);

// Wire the client's reconnect callback to the store's setReconnecting action.
// This runs once when the store module loads (module scope), avoiding a circular
// import (client.ts ← store.ts ← client.ts) by using a callback injection pattern.
registerReconnectCallback((v) => useStore.getState().setReconnecting(v));

/**
 * Mock-mode-only fixture seed (T-22 / BB-123). Every slice's own defaults are
 * LIVE ([]/0/null) unconditionally -- this is the only place fixtures.ts is ever
 * reached, and it is a dynamic `import()` rather than a module-scope import.
 *
 * Two early-return guards, not one:
 *  - `__HACHI_USE_MOCKS__` is a vite.config.ts `define` LITERAL (true/false baked in at
 *    build time). Checking it first lets Rollup prove the `await import(...)` below is
 *    unreachable in a mocks-off build and drop fixtures.ts from the build entirely --
 *    not just leave it as an unreferenced chunk. See vite-env.d.ts for why the runtime
 *    `USE_MOCKS` check alone can't get this: it's derived via
 *    `String(x).toLowerCase() === "true"`, which no minifier constant-folds.
 *  - `USE_MOCKS` (client.ts) is the actual source of truth and stays as a second,
 *    defensive check -- if this function is ever reached somewhere the `define` didn't
 *    apply, behaviour is still correct, just without the dead-code-elimination benefit.
 *
 * Call once from wherever mock mode boots (routes/__root.tsx, guarded by
 * `dataMode === "mock"`) -- see scripts/check_bundle.py for the build-time guard that
 * fails the gate if this ever regresses back to a static import.
 */
export async function seedMockFixtures(): Promise<void> {
  if (!__HACHI_USE_MOCKS__) return;
  if (!USE_MOCKS) return;
  const f = await import("../data/fixtures");
  useStore.getState().hydrate({
    spent: f.initialSpent,
    budget: f.initialBudget,
    taskSpent: f.initialTaskSpent,
    dailyCap: f.initialBudget,
    approvals: f.initApprovals(),
    history: f.history,
    runs: f.runs,
    routines: f.routines,
    finished: f.finished,
    failed: f.failed,
    memGroups: f.memGroups,
    blocked: f.blocked,
    proposed: f.proposed,
    skills: f.skills,
    kennel: f.kennel,
    integrations: f.integrations,
    mcpServers: f.mcpServers,
    mcpTools: f.mcpTools,
    ogNodes: f.ogNodes,
    authTargets: f.authTargets,
    authSource: f.authSource,
    spendTurns: f.spendTurns,
    spendTidies: f.spendTidies,
    spendModels: f.spendModels,
    packRoster: f.packRoster,
    events: f.events,
    toolCalls: f.toolCalls,
    auditLog: f.auditLog,
    recall: f.recall,
    sessions: f.sessions,
    skillUsage: f.skillUsage,
    askRules: f.askRules,
    allowedFolders: f.allowedFolders,
    allowedCommands: f.allowedCommands,
    opTools: f.opTools,
  });
}
