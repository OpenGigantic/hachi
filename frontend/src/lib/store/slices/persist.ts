// ============================================================
// T-63 — store slice: persist / operator+theme (concern #1 of 22).
// The only two fields the store's `persist` middleware actually saves to
// localStorage (see store.ts's `partialize`). Moved verbatim from
// frontend/src/lib/store/store.ts.
// ============================================================
import type { StateCreator } from "zustand";
import type { Store } from "../store";

export interface PersistState {
  operator: boolean;
  theme: "light" | "dark" | "auto";
}

export interface PersistActions {
  setOperator: (v: boolean) => void;
  setTheme: (t: "light" | "dark" | "auto") => void;
  toggleTheme: () => void;
  toggleOperator: () => void;
}

export const createPersistSlice: StateCreator<Store, [], [], PersistState & PersistActions> = (
  set,
  get
) => ({
  // persisted defaults (will be overridden by storage)
  operator: false,
  theme: "auto",

  setOperator: (v) => set({ operator: v }),
  setTheme: (t) => {
    if (t === 'auto') {
      document.documentElement.removeAttribute('data-theme');
    } else {
      document.documentElement.setAttribute('data-theme', t);
    }
    set({ theme: t });
  },
  toggleTheme: () => {
    const current = get().theme;
    const systemDark = typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches;
    const effectiveDark = current === 'dark' || (current === 'auto' && systemDark);
    const t = effectiveDark ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', t);
    set({ theme: t });
  },
  toggleOperator: () => set((s) => ({ operator: !s.operator })),
});
