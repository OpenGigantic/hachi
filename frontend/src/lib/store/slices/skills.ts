// ============================================================
// T-63 — store slice: skills / kennel (concern #8 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts; the module-scope
// `toast(...)` helper calls become `get().showToast(...)` since the toasts
// slice now owns that action. `openKennelBrowse` was textually under a
// separate "kennel browse" banner in the original Actions interface, but
// writes the same kennelBrowseOpen/kennelConsent fields as
// askKennel/cancelKennel, so it lands here by state ownership.
// ============================================================
import type { StateCreator } from "zustand";
import { USE_MOCKS } from "../../data/client";
import type { KennelItem, PackMember, Skill, SkillUsage } from "../../data/types";
import type { Store } from "../store";
import { nextId } from "./shared";

// Alias used in consent overlay state
export type KennelConsentItem = KennelItem;

export interface SkillsState {
  skills: Skill[];
  kennel: KennelItem[];
  skillUsage: SkillUsage[];
  packRoster: PackMember[];
  // kennel consent overlay
  kennelConsent: KennelConsentItem | null;
  // kennel browse mode (true = list mode, no specific item selected)
  kennelBrowseOpen: boolean;
}

export interface SkillsActions {
  addSkill: (kennelId: string) => void;
  confirmKennel: () => void;
  cancelKennel: () => void;
  removeSkill: (id: string) => void;
  askKennel: (item: KennelConsentItem) => void;
  // kennel browse
  openKennelBrowse: () => void;
}

export const createSkillsSlice: StateCreator<Store, [], [], SkillsState & SkillsActions> = (
  set,
  get
) => ({
  skills: [],
  kennel: [],
  skillUsage: [],
  packRoster: [],
  kennelConsent: null,
  kennelBrowseOpen: false,

  addSkill: (kennelId) => {
    const k = get().kennel.find((x) => x.id === kennelId);
    if (k) set({ kennelConsent: k });
  },
  askKennel: (item) => set({ kennelConsent: item, kennelBrowseOpen: false }),
  confirmKennel: () => {
    const k = get().kennelConsent;
    if (!k) return;

    if (!USE_MOCKS) {
      // Live mode: delegate to the domain function which POSTs /skills/add,
      // refreshes from gateway, and shows the success/error toast.
      void import("../../data/domains/skills").then(({ addSkillLive }) => {
        void addSkillLive(k.id);
      });
      return;
    }

    // Mock mode: local-only add
    set((s) => ({
      kennelConsent: null,
      skills: [
        ...s.skills,
        {
          id: "kn" + nextId(),
          icon: k.icon,
          name: k.name,
          desc: k.desc,
          uses: "0 times",
          lastUsed: "just added",
          stale: false,
        },
      ],
    }));
    get().showToast("Added " + k.name + " — it's ready to use", "success");
  },
  cancelKennel: () => set({ kennelConsent: null, kennelBrowseOpen: false }),
  openKennelBrowse: () => set({ kennelBrowseOpen: true, kennelConsent: null }),
  removeSkill: (id) => {
    set((s) => ({ skills: s.skills.filter((k) => k.id !== id) }));
    get().showToast("Removed — one fewer thing on Hachi's plate", "success");
  },
});
