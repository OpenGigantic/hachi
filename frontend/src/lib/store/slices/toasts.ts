// ============================================================
// T-63 — store slice: toasts (concern #16 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts.
// ============================================================
import type { StateCreator } from "zustand";
import type { Toast, ToastItem } from "../../data/types";
import type { Store } from "../store";
import { nextId } from "./shared";

export interface ToastsState {
  toasts: ToastItem[];
}

export interface ToastsActions {
  showToast: (message: string, tone?: Toast["tone"], action?: Toast["action"]) => void;
  dismissToast: (id: string) => void;
}

export const createToastsSlice: StateCreator<Store, [], [], ToastsState & ToastsActions> = (
  set
) => ({
  toasts: [],

  // Auto-dismiss timing lives in ToastHost (not here) so it can pause while the
  // toast is hovered and use a longer duration for error/"blocked" tones.
  showToast: (message, tone, action) => {
    const id = String(nextId());
    set((s) => {
      const next = [...s.toasts, { id, message, tone, action }];
      return { toasts: next.length > 3 ? next.slice(-3) : next };
    });
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
});
