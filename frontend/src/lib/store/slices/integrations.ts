// ============================================================
// T-63 — store slice: integrations / MCP (concern #9 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts; the module-scope
// `toast(...)` helper call becomes `get().showToast(...)` since the toasts
// slice now owns that action.
// ============================================================
import type { StateCreator } from "zustand";
import type { Integration, McpServer, McpTool } from "../../data/types";
import type { Store } from "../store";

export interface IntegrationsState {
  integrations: Integration[];
  mcpServers: McpServer[];
  mcpTools: McpTool[];
}

export interface IntegrationsActions {
  connectIntegration: (id: string) => void;
  disconnectIntegration: (id: string) => void;
}

export const createIntegrationsSlice: StateCreator<
  Store,
  [],
  [],
  IntegrationsState & IntegrationsActions
> = (set, get) => ({
  integrations: [],
  mcpServers: [],
  mcpTools: [],

  connectIntegration: (id) => {
    set((s) => ({
      integrations: s.integrations.map((i) => {
        if (i.id !== id) return i;
        if (i.status === "connected") return i;
        if (i.status === "degraded")
          return {
            ...i,
            status: "connected" as const,
            risky: false,
            can: i.can.replace(" — broad access", ""),
          };
        return {
          ...i,
          status: "connected" as const,
          can:
            i.name === "Notion" ? "read & update pages" : i.can,
        };
      }),
    }));
    get().showToast("Connected — Hachi can reach it now", "success");
  },
  disconnectIntegration: (id) => {
    set((s) => ({
      integrations: s.integrations.map((i) =>
        i.id === id
          ? { ...i, status: "none" as const, can: "not connected", risky: false }
          : i
      ),
    }));
    get().showToast("Disconnected", "success");
  },
});
