// ============================================================
// T-63 — store slice: permissions / safety (concern #14 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts; the module-scope
// `toast(...)` helper calls become `get().showToast(...)` since the toasts
// slice now owns that action. `addFolderWithPath`/`addCommandWithText` were
// textually under a separate "settings helpers" banner in the original
// Actions interface, but write the same allowedFolders/allowedCommands
// fields as addFolder/addCommand/removeFolder/removeCommand (and the
// original *implementation* already grouped them together), so they land
// here by state ownership.
// ============================================================
import type { StateCreator } from "zustand";
import type { AskRule, OpTool, SafetyLevel } from "../../data/types";
import type { Store } from "../store";

export interface PermissionsState {
  safety: SafetyLevel;
  askRules: AskRule[];
  allowedFolders: string[];
  allowedCommands: string[];
  opTools: OpTool[];
}

export interface PermissionsActions {
  setSafety: (level: SafetyLevel) => void;
  toggleAskRule: (id: string) => void;
  removeFolder: (f: string) => void;
  removeCommand: (c: string) => void;
  addFolder: () => void;
  addCommand: () => void;
  toggleOpTool: (id: string) => void;
  addFolderWithPath: (path: string) => void;
  addCommandWithText: (cmd: string) => void;
}

export const createPermissionsSlice: StateCreator<
  Store,
  [],
  [],
  PermissionsState & PermissionsActions
> = (set, get) => ({
  safety: "standard",
  askRules: [],
  allowedFolders: [],
  allowedCommands: [],
  opTools: [],

  setSafety: (level) => set({ safety: level }),
  toggleAskRule: (id) =>
    set((s) => ({
      askRules: s.askRules.map((r) =>
        r.id === id ? { ...r, on: !r.on } : r
      ),
    })),
  removeFolder: (f) =>
    set((s) => ({
      allowedFolders: s.allowedFolders.filter((x) => x !== f),
    })),
  removeCommand: (c) =>
    set((s) => ({
      allowedCommands: s.allowedCommands.filter((x) => x !== c),
    })),
  addFolder: () => {
    // Legacy no-arg path: kept for back-compat with tests / other callers.
    // Settings UI now uses addFolderWithPath instead.
    set((s) => ({ allowedFolders: [...s.allowedFolders, "~/new-folder"] }));
    get().showToast("Added — edit the path in your config", "success");
  },
  addFolderWithPath: (path) => {
    const trimmed = path.trim();
    if (!trimmed) {
      get().showToast("Enter a folder path first", "blocked");
      return;
    }
    set((s) => ({ allowedFolders: [...s.allowedFolders, trimmed] }));
    get().showToast("Folder added", "success");
  },
  addCommand: () => {
    // Legacy no-arg path: kept for back-compat with tests / other callers.
    set((s) => ({ allowedCommands: [...s.allowedCommands, "cmd"] }));
  },
  addCommandWithText: (cmd) => {
    const trimmed = cmd.trim();
    if (!trimmed) {
      get().showToast("Enter a command first", "blocked");
      return;
    }
    set((s) => ({ allowedCommands: [...s.allowedCommands, trimmed] }));
    get().showToast("Command added", "success");
  },
  toggleOpTool: (id) =>
    set((s) => ({
      opTools: s.opTools.map((t) =>
        t.id === id ? { ...t, on: !t.on } : t
      ),
    })),
});
