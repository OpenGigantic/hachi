// ============================================================
// Hachi Web App — setup / onboarding domain
//
// Endpoints:
//   GET  /setup        → { state: OnboardingState, needs_setup: boolean, environment: object }
//   POST /setup/apply  → { ok, plan, state }
//
// Wires the wizard's setup store slice (key, safety, google, channel, routine)
// to the real gateway. The wizard's optimistic control flow (step navigation,
// toggles) is left intact in store.ts. This module only:
//   1. Loads real onboarding state on mount and hydrates the slice
//   2. Posts the collected plan on Finish
// ============================================================

import { apiGet, apiSend, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
import { useStore } from "../../store/store";
import type { SafetyLevel } from "../types";

// ---- gateway shapes (mirror Python dataclasses exactly) ----

/** OnboardingState from hachi.core.onboarding */
interface GatewayOnboardingState {
  setup_version?: number;
  selected_tier?: string | null;
  completed_steps?: string[];
  last_seen_release?: string | null;
  migration_markers?: Record<string, string>;
}

interface GatewaySetupResponse {
  state: GatewayOnboardingState;
  needs_setup: boolean;
  environment: {
    anthropic_env?: boolean;
    openai_env?: boolean;
    groq_env?: boolean;
    together_env?: boolean;
    deepseek_env?: boolean;
    openrouter_env?: boolean;
    gemini_env?: boolean;
    ollama_cli?: boolean;
    project_dir?: string | null;
  };
}

interface GatewayApplyResponse {
  ok: boolean;
  plan: {
    tier: string;
    steps: string[];
    answers: Record<string, unknown>;
    enable_memory: boolean;
    show_integrations: boolean;
    show_advanced: boolean;
  };
  state: GatewayOnboardingState;
}

// ---- plan schema for POST /setup/apply ----

/**
 * Map wizard SafetyLevel → gateway permission_tier.
 * guarded  → sandbox   (ask before almost everything)
 * standard → guided    (ask before risky actions)
 * open     → unrestricted (full access)
 */
function safetyToPermissionTier(safety: SafetyLevel): string {
  switch (safety) {
    case "guarded": return "sandbox";
    case "open":    return "unrestricted";
    default:        return "guided";
  }
}

/** Build the plan payload that apply_setup expects from the wizard's answers. */
interface WizardAnswers {
  key: string;
  safety: SafetyLevel;
  routine: boolean;
  google: boolean;
  channel: boolean;
}

function buildApplyPlan(answers: WizardAnswers): Record<string, unknown> {
  const permissionTier = safetyToPermissionTier(answers.safety);

  // Provider block: always anthropic (the only provider the wizard exposes).
  // api_key_env is the conventional env-var name the gateway resolves the key
  // from; the raw key itself is sent separately as `api_key` (below).
  const provider: Record<string, string> = {
    kind: "anthropic",
    model: "claude-haiku-4-5",
    api_key_env: "ANTHROPIC_API_KEY",
  };
  // If the user typed a non-empty key, send the RAW secret as `api_key`. The
  // gateway persists it to the OS secret store + process env under
  // ANTHROPIC_API_KEY and records only the env-var name in config — the raw key
  // never lands in a config file. (Sending it as `api_key_ref` was the old bug:
  // that field is a reference *name*, not a secret, so the key was dropped.)
  const rawKey = answers.key.trim();
  if (rawKey) {
    provider.api_key = rawKey;
  }

  // Integrations block: only include google if the user connected it.
  // "channel" (Slack/WhatsApp) has no capability id in the gateway config —
  // it's purely a UX toggle with no backend analog yet, so we omit it.
  const integrations: Record<string, { enabled: boolean }> = {};
  if (answers.google) {
    integrations["gmail"] = { enabled: true };
  }

  return {
    tier: "lightning",
    steps: ["welcome", "provider", "safety", "finish"],
    answers: {
      provider,
      permission_tier: permissionTier,
      ...(Object.keys(integrations).length > 0 ? { integrations } : {}),
    },
    enable_memory: true,
    show_integrations: false,
    show_advanced: false,
  };
}

// ---- loader ----

/**
 * Load onboarding state from the gateway and hydrate the setup slice.
 *
 * Conservative approach: only sets field values the wizard doesn't already
 * derive from its own defaults. Does NOT force the modal open — only sets
 * a marker so consumers can check `needs_setup` if they choose to act on it.
 *
 * Safety level mapping (reverse of safetyToPermissionTier):
 *   sandbox       → guarded
 *   guided        → standard
 *   unrestricted  → open
 */
export async function loadSetup(): Promise<void> {
  if (USE_MOCKS) return;

  try {
    const raw = await apiGet<GatewaySetupResponse>("/setup");

    const store = useStore.getState();
    const currentSetup = store.setup;

    // Derive safety level from the completed_steps / selected_tier.
    // The gateway does not return the current safety tier directly in
    // OnboardingState, but if setup is complete we leave the slice's current
    // value alone (user may have already changed it). Only pre-fill when the
    // wizard has never been touched (key is empty = wizard hasn't run yet).
    let safetyFromGateway: SafetyLevel | null = null;
    const tier = raw.state?.selected_tier ?? null;
    if (tier) {
      // Map the setup tier to a sensible default safety level.
      // "lightning" / "recommended" → standard; "enhanced"/"power" → open.
      if (tier === "enhanced" || tier === "power") {
        safetyFromGateway = "open";
      } else {
        safetyFromGateway = "standard";
      }
    }

    // Only patch the slice if the wizard hasn't been started (key blank means
    // the user hasn't touched it in this session).
    const shouldPatch = !currentSetup.key;

    const patchedSetup = shouldPatch
      ? {
          ...currentSetup,
          // Pre-set safety from what the gateway reports, if available.
          safety: safetyFromGateway ?? currentSetup.safety,
        }
      : currentSetup;

    store.hydrate({
      setup: patchedSetup,
    });

    store.setReconnecting(false);
  } catch {
    useStore.getState().setReconnecting(true);
    // Leave the seeded fixtures / current setup state in place.
  }
}

registerLoader("setup", loadSetup);

// ---- mutation wrapper ----

/**
 * POST /setup/apply — call on the final wizard step (Finish).
 *
 * In mock mode: short-circuits immediately (returns a mock-ok result).
 * In live mode: builds the plan from wizard answers and posts it.
 *
 * Errors are caught and surfaced via setReconnecting(true); they do NOT
 * throw so the wizard's optimistic close flow still runs.
 *
 * Returns the gateway response (or null on error / mock).
 */
export async function applySetup(answers: WizardAnswers): Promise<GatewayApplyResponse | null> {
  if (USE_MOCKS) {
    // Short-circuit: let the optimistic store flow handle everything.
    return null;
  }

  const plan = buildApplyPlan(answers);

  try {
    const result = await apiSend<GatewayApplyResponse>("POST", "/setup/apply", { plan });
    // Clear reconnecting flag if it was set.
    useStore.getState().setReconnecting(false);
    return result;
  } catch {
    // Non-fatal: the wizard already optimistically updated the store.
    // Flag offline so the reconnect banner appears, but don't block the UI.
    useStore.getState().setReconnecting(true);
    return null;
  }
}
