// ============================================================
// T-63 — store slice: memory (concern #6 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts; the module-scope
// `toast(...)` helper calls become `get().showToast(...)` since the toasts
// slice now owns that action.
//
// `gardenerDiffs` is a field the spec's original 22-concern/75-field table
// (docs/audit/2026-07-19/tasks/T-63-frontend-store-slices.md sec 3.4) does
// not list at all -- see this task's report. It is hydrate-only (no
// dedicated action; the memoryGardener domain loader writes it directly via
// hydrate()) and is read exclusively by the Memory route, so it is grouped
// here with the other memory-review fields.
//
// `approveProposed`/`rejectProposed` write `proposed`, which is owned by the
// review slice (concern #7) -- they were textually under the original
// file's "// memory" banner alongside the fact-editing actions, so they stay
// here and cross-write `proposed` via the Store-typed `set`.
// ============================================================
import type { StateCreator } from "zustand";
import type { GardenerProposal, MemGroup, RecallHit } from "../../data/types";
import type { Store } from "../store";
import { nextId } from "./shared";

export interface MemoryState {
  memGroups: MemGroup[];
  recall: RecallHit[];
  editingFactId: string | null;
  editingText: string;
  /** T-08 A3: MemoryGardener pending_review proposals (GET /memory/diffs). */
  gardenerDiffs: GardenerProposal[];
}

export interface MemoryActions {
  addFact: (groupId?: string, text?: string) => void;
  editFact: (id: string, text: string) => void;
  deleteFact: (id: string) => void;
  startEditFact: (id: string) => void;
  cancelEdit: () => void;
  saveFact: (groupId: string, factId: string) => void;
  delFact: (groupId: string, factId: string) => void;
  setEditText: (v: string) => void;
  approveProposed: () => void;
  rejectProposed: () => void;
}

export const createMemorySlice: StateCreator<Store, [], [], MemoryState & MemoryActions> = (
  set,
  get
) => ({
  memGroups: [],
  recall: [],
  editingFactId: null,
  editingText: "",
  gardenerDiffs: [],

  addFact: (groupId = "you") => {
    const fid = "fn" + nextId();
    set((s) => ({
      memGroups: s.memGroups.map((g) =>
        g.id === groupId
          ? {
              ...g,
              facts: [
                { id: fid, text: "", source: "added by you" },
                ...g.facts,
              ],
            }
          : g
      ),
      editingFactId: fid,
      editingText: "",
    }));
  },
  editFact: (id, text) =>
    set((s) => ({
      memGroups: s.memGroups.map((g) => ({
        ...g,
        facts: g.facts.map((f) =>
          f.id === id ? { ...f, text } : f
        ),
      })),
    })),
  deleteFact: (id) =>
    set((s) => ({
      memGroups: s.memGroups.map((g) => ({
        ...g,
        facts: g.facts.filter((f) => f.id !== id),
      })),
    })),
  startEditFact: (id) => {
    const all = get().memGroups.flatMap((g) => g.facts);
    const f = all.find((x) => x.id === id);
    set({ editingFactId: id, editingText: f?.text ?? "" });
  },
  cancelEdit: () => set({ editingFactId: null, editingText: "" }),
  setEditText: (v) => set({ editingText: v }),
  saveFact: (groupId, factId) => {
    const t = get().editingText.trim();
    set((s) => ({
      editingFactId: null,
      editingText: "",
      memGroups: s.memGroups.map((g) =>
        g.id === groupId
          ? {
              ...g,
              facts: g.facts.map((f) =>
                f.id === factId ? { ...f, text: t || f.text } : f
              ),
            }
          : g
      ),
    }));
    get().showToast("Saved — Hachi remembers it", "success");
  },
  delFact: (groupId, factId) => {
    set((s) => ({
      memGroups: s.memGroups.map((g) =>
        g.id === groupId
          ? { ...g, facts: g.facts.filter((f) => f.id !== factId) }
          : g
      ),
    }));
    get().showToast("Forgotten — Hachi removed that fact", "success");
  },
  approveProposed: () => {
    set({ proposed: null });
    get().showToast("Updated — Hachi remembers the new version", "success");
  },
  rejectProposed: () => {
    set({ proposed: null });
    get().showToast("Kept as-is — Hachi won't change it", "success");
  },
});
