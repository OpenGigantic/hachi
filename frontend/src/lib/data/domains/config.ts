// ============================================================
// Hachi Web App — config / settings domain
//
// Wires: GET /config, GET /models, PUT /config/provider,
//        POST /config/provider/validate, PUT /config/permissions,
//        PUT /config/memory
//
// Mapping:
//   permissions.tier sandbox  → safety "guarded"
//   permissions.tier guided   → safety "standard"
//   permissions.tier unrestricted → safety "open"
//   provider.kind → provider.name; provider.model → provider.model
//   key always stays "" (never surface secrets)
//   allowedFolders / allowedCommands → keep fixtures (no backing field)
//   askRules / opTools → keep fixtures (no backing field)
// ============================================================

import { apiGet, apiSend, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
import { useStore } from "../../store/store";
import type { SafetyLevel, Provider } from "../types";

// ---- Local types mirroring the gateway Python dataclasses ----

interface GatewayProviderSettings {
  kind?: string;
  model?: string;
  base_url?: string | null;
  api_key_ref?: string | null;
  api_key_env?: string | null;
}

interface GatewayPermissionsSettings {
  tier?: "sandbox" | "guided" | "unrestricted";
}

interface GatewayMemorySettings {
  enable_session_history?: boolean;
  enable_semantic_memory?: boolean;
}

interface GatewayConfig {
  provider?: GatewayProviderSettings;
  permissions?: GatewayPermissionsSettings;
  memory?: GatewayMemorySettings;
  // other keys exist (meta, agent, capabilities, channels) but are not mapped
  [key: string]: unknown;
}

interface GatewayModel {
  id: string;
  label: string;
  provider: string;
  recommended: boolean;
}

interface ValidateProviderResponse {
  ok: boolean;
  errors: string[];
  warnings: string[];
}

// Module-level model list (not stored in Zustand — avoids types.ts changes)
let _availableModels: GatewayModel[] = [];

// Tracks whether this domain has ever received real data from the gateway.
// In live mode: until the first successful fetch, seeded fixtures must NOT be
// shown, so on failure we clear the slice instead of leaving them in place.
let _hasEverSucceeded = false;

/** Returns the last fetched model list (empty until loadConfig resolves). */
export function getAvailableModels(): GatewayModel[] {
  return _availableModels;
}

// ---- Adapters ----

/** gateway tier → UI SafetyLevel */
function adaptTier(tier?: string): SafetyLevel {
  if (tier === "sandbox") return "guarded";
  if (tier === "unrestricted") return "open";
  return "standard"; // "guided" and fallback
}

/** UI SafetyLevel → gateway tier */
function safetyToTier(level: SafetyLevel): "sandbox" | "guided" | "unrestricted" {
  if (level === "guarded") return "sandbox";
  if (level === "open") return "unrestricted";
  return "guided"; // "standard"
}

/** gateway provider → UI Provider (never surface secrets) */
function adaptProvider(raw?: GatewayProviderSettings): Provider {
  return {
    name: raw?.kind ?? "anthropic",
    model: raw?.model ?? "claude-haiku-4-5",
    key: "", // intentionally blank — never expose secrets to the browser
  };
}

// ---- Loader ----

/**
 * Load effective config + models from the gateway and hydrate the store.
 * Never throws. On error: setReconnecting(true) and leave existing fixtures.
 */
export async function loadConfig(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures

  try {
    // Fetch config and models concurrently
    const [rawConfig, rawModels] = await Promise.all([
      apiGet<GatewayConfig>("/config"),
      apiGet<GatewayModel[]>("/models"),
    ]);

    // Stash models in module-level variable (no new store field needed)
    _availableModels = Array.isArray(rawModels) ? rawModels : [];

    // Map config → store slice
    const safety = adaptTier(rawConfig?.permissions?.tier);
    const provider = adaptProvider(rawConfig?.provider);

    useStore.getState().hydrate({ safety, provider });
    useStore.getState().setReconnecting(false);
    _hasEverSucceeded = true;
  } catch {
    if (!_hasEverSucceeded) {
      // First-load failure: reset to safe defaults so we never present
      // the fixture provider/safety config as if it were real gateway data.
      useStore.getState().hydrate({
        safety: "standard",
        provider: { name: "anthropic", model: "claude-haiku-4-5", key: "" },
      });
    }
    // Whether or not this is the first load, flag reconnecting so the UI
    // shows the reconnecting indicator. Last-known-good real data is
    // preserved after the first success.
    useStore.getState().setReconnecting(true);
  }
}

// Self-register so bootstrapLiveData() picks this up on app mount
registerLoader("config", loadConfig);

// ---- Mutation wrappers ----

/**
 * Update the safety level optimistically, then persist to the gateway.
 * Reverse: UI SafetyLevel → gateway tier via safetyToTier().
 */
export async function setSafetyLive(level: SafetyLevel): Promise<void> {
  // Optimistic update
  useStore.getState().setSafety(level);
  if (USE_MOCKS) return;
  try {
    await apiSend<GatewayConfig>("PUT", "/config/permissions", {
      permissions: { tier: safetyToTier(level) },
    });
  } catch {
    useStore.getState().setReconnecting(true);
  }
}

/**
 * Update the provider optimistically, then persist to the gateway.
 * Patch is a partial Provider (UI shape); we send only the diff.
 */
export async function setProviderLive(patch: Partial<Provider>): Promise<void> {
  // Optimistic update
  useStore.getState().setProvider(patch);
  if (USE_MOCKS) return;
  try {
    // Map UI fields → gateway fields (never send key — it's always "")
    const gwPatch: Partial<GatewayProviderSettings> = {};
    if (patch.name !== undefined) gwPatch.kind = patch.name;
    if (patch.model !== undefined) gwPatch.model = patch.model;
    await apiSend<GatewayConfig>("PUT", "/config/provider", {
      provider: gwPatch,
    });
  } catch {
    useStore.getState().setReconnecting(true);
  }
}

/**
 * Validate a proposed provider config without persisting.
 * Returns { ok, errors, warnings } — or a failed-safe fallback on error.
 */
export async function validateProvider(
  patch: Partial<Provider>
): Promise<ValidateProviderResponse> {
  if (USE_MOCKS) return { ok: true, errors: [], warnings: [] };
  try {
    const gwPatch: Partial<GatewayProviderSettings> = {};
    if (patch.name !== undefined) gwPatch.kind = patch.name;
    if (patch.model !== undefined) gwPatch.model = patch.model;
    return await apiSend<ValidateProviderResponse>(
      "POST",
      "/config/provider/validate",
      { provider: gwPatch }
    );
  } catch {
    useStore.getState().setReconnecting(true);
    return { ok: false, errors: ["Could not reach gateway"], warnings: [] };
  }
}

/**
 * Add an allowed folder path and persist to the gateway.
 * Only adds to the UI list on 2xx; shows an error toast on failure.
 * In mock mode, delegates directly to the store action (no network call).
 */
export async function addFolderLive(path: string): Promise<void> {
  const { addFolderWithPath, showToast } = useStore.getState();
  if (USE_MOCKS) {
    addFolderWithPath(path);
    return;
  }
  try {
    await apiSend<unknown>("POST", "/config/permissions/folders", { path });
    addFolderWithPath(path);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : "Failed to add folder — please try again";
    showToast(msg, "blocked");
  }
}

/**
 * Add an allowed command and persist to the gateway.
 * Only adds to the UI list on 2xx; shows an error toast on failure.
 * In mock mode, delegates directly to the store action (no network call).
 */
export async function addCommandLive(command: string): Promise<void> {
  const { addCommandWithText, showToast } = useStore.getState();
  if (USE_MOCKS) {
    addCommandWithText(command);
    return;
  }
  try {
    await apiSend<unknown>("POST", "/config/permissions/commands", { command });
    addCommandWithText(command);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : "Failed to add command — please try again";
    showToast(msg, "blocked");
  }
}

/**
 * Toggle memory flags and persist to the gateway.
 */
export async function setMemoryLive(
  patch: Partial<{ enable_session_history: boolean; enable_semantic_memory: boolean }>
): Promise<void> {
  if (USE_MOCKS) return;
  try {
    await apiSend<GatewayConfig>("PUT", "/config/memory", { memory: patch });
  } catch {
    useStore.getState().setReconnecting(true);
  }
}
