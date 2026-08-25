// ============================================================
// T-63 — store slice: command palette (concern #17 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts.
// ============================================================
import type { StateCreator } from "zustand";
import type { Store } from "../store";

export interface PaletteState {
  palette: { open: boolean; query: string };
}

export interface PaletteActions {
  openPalette: () => void;
  closePalette: () => void;
  setPaletteQuery: (q: string) => void;
}

export const createPaletteSlice: StateCreator<Store, [], [], PaletteState & PaletteActions> = (
  set
) => ({
  palette: { open: false, query: "" },

  openPalette: () => set({ palette: { open: true, query: "" } }),
  closePalette: () => set((s) => ({ palette: { ...s.palette, open: false } })),
  setPaletteQuery: (q) => set((s) => ({ palette: { ...s.palette, query: q } })),
});
