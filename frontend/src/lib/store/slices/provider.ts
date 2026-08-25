// ============================================================
// T-63 — store slice: provider / config-loading (concern #15 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts; the module-scope
// `toast(...)` helper call in simulateReconnect becomes `get().showToast(...)`
// since the toasts slice now owns that action. `setProvider` was textually
// under the "settings" banner and `simulateReconnect` under its own
// "reconnect" banner in the original Actions interface, but both write
// this slice's own fields (provider / reconnecting), so they land here by
// state ownership -- `setLoaded`/`setReconnecting` were already grouped
// with `hydrate` under "live-data hydration" and also write this slice's
// fields.
// ============================================================
import type { StateCreator } from "zustand";
import type { Provider } from "../../data/types";
import type { Store } from "../store";

export interface ProviderState {
  provider: Provider;
  /** True once loadConfig has received a real response (success or error-with-defaults). */
  configLoaded: boolean;
  /** Per-domain settled flags — true once the first load completes (success or error). */
  loaded: { approvals: boolean; work: boolean; review: boolean; memory: boolean; integrations: boolean };
  reconnecting: boolean;
}

export interface ProviderActions {
  setLoaded: (domain: keyof ProviderState['loaded']) => void;
  setReconnecting: (v: boolean) => void;
  setProvider: (p: Partial<Provider>) => void;
  // reconnect
  simulateReconnect: () => void;
}

// module-level timer, private to this slice (moved verbatim from store.ts)
let _rc: ReturnType<typeof setTimeout> | null = null;

export const createProviderSlice: StateCreator<
  Store,
  [],
  [],
  ProviderState & ProviderActions
> = (set, get) => ({
  provider: { name: "anthropic", model: "claude-sonnet-4-5", key: "" },
  configLoaded: false,
  loaded: { approvals: false, work: false, review: false, memory: false, integrations: false },
  reconnecting: false,

  setLoaded: (domain) => set((s) => ({ loaded: { ...s.loaded, [domain]: true } })),
  setReconnecting: (v) => set({ reconnecting: v }),
  setProvider: (p) =>
    set((s) => ({ provider: { ...s.provider, ...p } })),

  simulateReconnect: () => {
    set({ reconnecting: true, palette: { open: false, query: "" } });
    if (_rc) clearTimeout(_rc);
    _rc = setTimeout(() => {
      set({ reconnecting: false });
      get().showToast("Reconnected — Hachi is back", "success");
    }, 3800);
  },
});
