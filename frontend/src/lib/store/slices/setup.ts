// ============================================================
// T-63 — store slice: setup wizard (concern #18 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts; the module-scope
// `toast(...)` helper calls become `get().showToast(...)` since the toasts
// slice now owns that action. `setupNext` cross-writes `safety`
// (permissions slice) and `routines` (work-queue slice) via the
// Store-typed `set`, exactly as it did before the split.
// ============================================================
import type { StateCreator } from "zustand";
import type { SafetyLevel } from "../../data/types";
import type { Store } from "../store";
import { nextId } from "./shared";

export interface SetupState {
  setup: {
    open: boolean;
    step: number;
    key: string;
    safety: SafetyLevel;
    routine: boolean;
    google: boolean;
    channel: boolean;
    /** 'onboard' = full wizard; 'edit-provider' = provider/key step only */
    mode: 'onboard' | 'edit-provider';
    /** True when the backend reports an API key is already configured in the environment */
    keyAlreadySet: boolean;
    /** Onboarding wizard tier; gates which setup steps + integrations appear. Distinct from `safety`. */
    tier: 'lightning' | 'recommended' | 'enhanced' | 'power';
    /** True once the wizard has been completed end-to-end; openSetup resets to step 0 when true. */
    completed: boolean;
  };
}

export interface SetupActions {
  openSetup: () => void;
  openSetupEditProvider: () => void;
  closeSetup: () => void;
  setSetupStep: (n: number) => void;
  setSetupField: (k: string, v: unknown) => void;
  setupBack: () => void;
  setupNext: () => void;
  toggleSetupRoutine: () => void;
  toggleSetupGoogle: () => void;
  toggleSetupChannel: () => void;
  setSetupKey: (v: string) => void;
}

export const createSetupSlice: StateCreator<Store, [], [], SetupState & SetupActions> = (
  set,
  get
) => ({
  setup: { open: false, step: 0, key: "", safety: "standard", routine: true, google: false, channel: false, mode: 'onboard' as const, keyAlreadySet: false, tier: 'lightning', completed: false },

  openSetup: () =>
    set((s) => ({
      setup: {
        ...s.setup,
        open: true,
        // Preserve partial progress so the user resumes where they left off.
        // Reset to step 0 only when starting fresh or after completing the wizard.
        step: s.setup.step > 0 && !s.setup.completed ? s.setup.step : 0,
        mode: 'onboard' as const,
      },
    })),
  openSetupEditProvider: () =>
    set((s) => ({ setup: { ...s.setup, open: true, step: 1, mode: 'edit-provider' as const } })),
  closeSetup: () =>
    set((s) => ({ setup: { ...s.setup, open: false } })),
  setSetupStep: (n) =>
    set((s) => ({ setup: { ...s.setup, step: n } })),
  setSetupField: (k, v) =>
    set((s) => ({ setup: { ...s.setup, [k]: v } })),
  setSetupKey: (v) =>
    set((s) => ({ setup: { ...s.setup, key: v } })),
  toggleSetupRoutine: () =>
    set((s) => ({ setup: { ...s.setup, routine: !s.setup.routine } })),
  toggleSetupGoogle: () =>
    set((s) => ({ setup: { ...s.setup, google: !s.setup.google } })),
  toggleSetupChannel: () =>
    set((s) => ({ setup: { ...s.setup, channel: !s.setup.channel } })),
  setupBack: () =>
    set((s) => ({
      setup: { ...s.setup, step: Math.max(0, s.setup.step - 1) },
    })),
  setupNext: () => {
    const su = get().setup;
    // edit-provider mode: step 1 is the only step; Save closes the wizard.
    if (su.mode === 'edit-provider') {
      set((s) => ({ setup: { ...s.setup, open: false } }));
      get().showToast("Provider updated — Hachi will use it on next restart", "success");
      return;
    }
    const step = su.step;
    if (step >= 4) {
      set((s) => {
        const hasBriefing = s.routines.some((r) => r.title === "Morning briefing");
        const newRoutines =
          su.routine && !hasBriefing
            ? [
                {
                  id: nextId(),
                  title: "Morning briefing",
                  schedule: "weekdays 8:00",
                  next: "tomorrow 8:00",
                  cost: 0.04,
                  paused: false,
                },
                ...s.routines,
              ]
            : s.routines;
        return {
          setup: { ...s.setup, open: false, completed: true },
          safety: su.safety,
          routines: newRoutines,
        };
      });
      get().showToast("All set — Hachi is on watch", "success");
    } else {
      set((s) => ({ setup: { ...s.setup, step: s.setup.step + 1 } }));
    }
  },
});
