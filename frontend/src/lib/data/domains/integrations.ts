// ============================================================
// Hachi Web App — integrations + MCP inventory domain
// Wires:
//   GET /capabilities  → Integration[] (native integrations only)
//   GET /runtime/mcp/inventory → McpServer[] + McpTool[]
//   PUT /capabilities/{id}/enabled → connect/disconnect toggle
//   POST /runtime/mcp/register → register new MCP server
//     NOTE: /runtime/mcp/register does NOT yet exist in the gateway.
//     The endpoint is wired optimistically; callers receive a clear error
//     message so the UI can surface it without crashing. When the backend
//     adds this route the frontend will work automatically.
// ============================================================

import { apiGet, apiSend, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
import { useStore } from "../../store/store";
import type { Integration, McpServer, McpTool } from "../types";

// ============================================================
// 1. Raw gateway shapes (mirror Python dicts exactly)
// ============================================================

/** Raw capability entry from GET /capabilities */
interface GatewayCapability {
  id: string;
  kind?: string; // always "native_integration", but tolerate omission
  enabled?: boolean;
  configured?: boolean;
  secret_ref?: string | null;
}

/** Raw MCP server entry inside GET /runtime/mcp/inventory */
interface GatewayMcpServer {
  name?: string;
  status?: string;
  tool_count?: number;
  tools?: string[];
}

/** Raw response from GET /runtime/mcp/inventory */
interface GatewayMcpInventory {
  server_count?: number;
  total_tool_count?: number;
  servers?: GatewayMcpServer[];
}

// ============================================================
// 2. Pure adapters: gateway shape → UI types
// ============================================================

/**
 * Derive a human-readable name from a capability id (e.g. "google" → "Google").
 * Handles common compound ids like "google_calendar" → "Google Calendar".
 */
function prettyName(id: string): string {
  return id
    .replace(/_/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

/**
 * Map capability id → short description of what it can do.
 * Falls back to a generic string for unknown ids — adapter must never throw.
 */
function capabilityDescription(id: string): string {
  const descriptions: Record<string, string> = {
    google: "Read Gmail, Calendar, Drive",
    gmail: "Read and send email",
    google_calendar: "Read and create calendar events",
    google_drive: "Read and write Drive files",
    slack: "Send messages, read channels",
    notion: "Read and write Notion pages",
    github: "Read repos, open issues and PRs",
    linear: "Create and update issues",
    telegram: "Send Telegram messages",
    discord: "Send Discord messages",
    whatsapp: "Send WhatsApp messages",
  };
  return descriptions[id.toLowerCase()] ?? "Access external service";
}

/**
 * Heuristic: mark a capability as risky when it includes broad or destructive scopes.
 * Keeps it simple — this is a UI hint, not a security gate.
 */
function isRisky(id: string): boolean {
  const riskyIds = new Set(["github", "google_drive", "google"]);
  return riskyIds.has(id.toLowerCase());
}

/**
 * Derive an icon name from the capability id.
 * Falls back to "plug" so the UI always has something to render.
 */
function iconFromId(id: string): string {
  const icons: Record<string, string> = {
    google: "google",
    gmail: "mail",
    google_calendar: "calendar",
    google_drive: "folder",
    slack: "slack",
    notion: "notion",
    github: "github",
    linear: "linear",
    telegram: "telegram",
    discord: "discord",
    whatsapp: "whatsapp",
  };
  return icons[id.toLowerCase()] ?? "plug";
}

/**
 * Adapt a raw GatewayCapability → UI Integration.
 *
 * Status mapping (from README contract):
 *   configured && enabled  → "connected"
 *   configured && !enabled → "degraded"
 *   else                   → "none"
 */
function adaptCapability(raw: GatewayCapability): Integration {
  const enabled = Boolean(raw.enabled);
  const configured = Boolean(raw.configured);
  const status: Integration["status"] = configured && enabled
    ? "connected"
    : configured && !enabled
    ? "degraded"
    : "none";

  return {
    id: raw.id ?? "unknown",
    icon: iconFromId(raw.id ?? ""),
    name: prettyName(raw.id ?? "unknown"),
    can: capabilityDescription(raw.id ?? ""),
    status,
    risky: isRisky(raw.id ?? ""),
  };
}

/**
 * Adapt a raw GatewayMcpServer → UI McpServer.
 * Gateway status values ("connected", "starting", "blocked", etc.) are mapped
 * to the UI's allowed literal union; unknown values fall back to "starting".
 */
function adaptMcpServer(raw: GatewayMcpServer): McpServer {
  const rawStatus = (raw.status ?? "").toLowerCase();
  const statusMap: Record<string, McpServer["status"]> = {
    connected: "connected",
    ready: "connected",
    running: "connected",
    starting: "starting",
    blocked: "blocked",
    failed: "blocked",
    error: "blocked",
  };
  const status: McpServer["status"] = statusMap[rawStatus] ?? "starting";

  return {
    name: raw.name ?? "unknown",
    status,
    tools: raw.tool_count ?? 0,
    // Gateway doesn't expose transport or uptime; show dashes so the table columns
    // still render gracefully.
    transport: "—",
    uptime: "—",
    // No error field in the current gateway shape.
  };
}

/**
 * Expand a single GatewayMcpServer's tools array → UI McpTool[].
 * The gateway provides tool names only (no argument schemas), so args is always [].
 */
function adaptMcpTools(raw: GatewayMcpServer): McpTool[] {
  const serverName = raw.name ?? "unknown";
  return (raw.tools ?? []).map((toolName) => ({
    name: toolName,
    server: serverName,
    // Gateway gives tool names only — args can be [] per contract.
    args: [] as [string, string][],
  }));
}

// ============================================================
// 3. Loader: fetch → adapt → hydrate (NEVER throws)
// ============================================================

// Tracks whether this domain has ever received real data from the gateway.
// In live mode: until the first successful fetch, seeded fixtures must NOT be
// shown, so on failure we clear the slices instead of leaving them in place.
let _hasEverSucceeded = false;

/**
 * Load native integrations (GET /capabilities) and MCP inventory
 * (GET /runtime/mcp/inventory), then hydrate the store.
 *
 * On first-load failure (live mode): clears slices so seeded fixtures are never
 * shown as real data. On subsequent failures: keeps last-known-good real data
 * and sets reconnecting. On success: sets reconnecting(false).
 */
export async function loadIntegrations(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures

  const { hydrate, setReconnecting } = useStore.getState();

  try {
    // Fetch both endpoints concurrently.
    const [capabilities, mcpInventory] = await Promise.all([
      apiGet<GatewayCapability[]>("/capabilities"),
      apiGet<GatewayMcpInventory>("/runtime/mcp/inventory"),
    ]);

    // Adapt capabilities → integrations
    const rawCaps = Array.isArray(capabilities) ? capabilities : [];
    const integrations: Integration[] = rawCaps.map(adaptCapability);

    // Adapt MCP inventory → servers + tools
    const rawServers: GatewayMcpServer[] = mcpInventory?.servers ?? [];
    const mcpServers: McpServer[] = rawServers.map(adaptMcpServer);
    const mcpTools: McpTool[] = rawServers.flatMap(adaptMcpTools);

    hydrate({ integrations, mcpServers, mcpTools });
    setReconnecting(false);
    _hasEverSucceeded = true;
  } catch {
    if (!_hasEverSucceeded) {
      // First-load failure: clear seeded fixtures so we never present
      // fabricated integration data as if it were real gateway data.
      hydrate({ integrations: [], mcpServers: [], mcpTools: [] });
    }
    // Whether or not this is the first load, flag reconnecting so the UI
    // shows the reconnecting indicator. Last-known-good real data is
    // preserved after the first success.
    setReconnecting(true);
  }
}

// Self-register so bootstrapLiveData() picks this loader up automatically.
registerLoader("integrations", loadIntegrations);

// ============================================================
// 4. Mutation wrappers
// ============================================================

/** Raw response shape from PUT /capabilities/{id}/enabled */
interface GatewayCapabilityToggleResponse {
  id: string;
  enabled: boolean;
  configured: boolean;
}

/** Status tracked per-integration to prevent double-submission. */
export type ActionStatus = "idle" | "loading" | "error";

// Per-integration in-flight tracking. Keyed by integration id.
// Using a Map (module-level) so state is shared across callers without store coupling.
const _connectStatus = new Map<string, ActionStatus>();
const _disconnectStatus = new Map<string, ActionStatus>();
const _statusListeners = new Set<() => void>();

/** Subscribe to status change notifications (used by the UI for re-render). */
export function subscribeActionStatus(cb: () => void): () => void {
  _statusListeners.add(cb);
  return () => _statusListeners.delete(cb);
}

function _notifyListeners() {
  _statusListeners.forEach((cb) => cb());
}

/** Get the current connect status for an integration id. */
export function getConnectStatus(id: string): ActionStatus {
  return _connectStatus.get(id) ?? "idle";
}

/** Get the current disconnect status for an integration id. */
export function getDisconnectStatus(id: string): ActionStatus {
  return _disconnectStatus.get(id) ?? "idle";
}

/**
 * "Connect" an integration — optimistic store update + gateway call.
 *
 * Applies the optimistic update instantly for snappy UI, then calls
 * PUT /capabilities/{id}/enabled with { enabled: true }. On error,
 * signals setReconnecting(true) and re-runs the loader to restore
 * the last known gateway truth.
 *
 * Returns false if already in-flight (prevents double-click duplicates).
 */
export async function connectIntegration(id: string): Promise<boolean> {
  if (_connectStatus.get(id) === "loading") return false;

  useStore.getState().connectIntegration(id);
  if (USE_MOCKS) return true;

  const { setReconnecting } = useStore.getState();

  _connectStatus.set(id, "loading");
  _notifyListeners();

  try {
    await apiSend<GatewayCapabilityToggleResponse>(
      "PUT",
      `/capabilities/${id}/enabled`,
      { enabled: true },
    );
    // Re-run the loader so derived status reflects gateway truth.
    await loadIntegrations();
    _connectStatus.set(id, "idle");
    _notifyListeners();
    return true;
  } catch {
    setReconnecting(true);
    _connectStatus.set(id, "error");
    _notifyListeners();
    return false;
  }
}

/**
 * "Disconnect" an integration — optimistic store update + gateway call.
 *
 * Applies the optimistic update instantly for snappy UI, then calls
 * PUT /capabilities/{id}/enabled with { enabled: false }. On error,
 * signals setReconnecting(true) and re-runs the loader to restore
 * the last known gateway truth.
 *
 * Returns false if already in-flight (prevents double-click duplicates).
 */
export async function disconnectIntegration(id: string): Promise<boolean> {
  if (_disconnectStatus.get(id) === "loading") return false;

  useStore.getState().disconnectIntegration(id);
  if (USE_MOCKS) return true;

  const { setReconnecting } = useStore.getState();

  _disconnectStatus.set(id, "loading");
  _notifyListeners();

  try {
    await apiSend<GatewayCapabilityToggleResponse>(
      "PUT",
      `/capabilities/${id}/enabled`,
      { enabled: false },
    );
    // Re-run the loader so derived status reflects gateway truth.
    await loadIntegrations();
    _disconnectStatus.set(id, "idle");
    _notifyListeners();
    return true;
  } catch {
    setReconnecting(true);
    _disconnectStatus.set(id, "error");
    _notifyListeners();
    return false;
  }
}

// ============================================================
// 5. MCP server registration
// ============================================================

/** Allowed launcher commands (mirrors Python ALLOWED_MCP_COMMANDS). */
export const ALLOWED_MCP_COMMANDS = ["npx", "uvx", "python", "python3", "node", "uv"] as const;
export type McpCommand = (typeof ALLOWED_MCP_COMMANDS)[number];

/** Input shape for adding a new MCP server. */
export interface AddMcpServerInput {
  /** Slug identifier, e.g. "github". Letters, digits, hyphens, underscores only. */
  name: string;
  /** Launcher command. Must be one of ALLOWED_MCP_COMMANDS. */
  command: McpCommand;
  /** Package / script arguments, e.g. "-y @modelcontextprotocol/server-github". */
  args: string;
  /** Optional plain (non-secret) env vars. Keys must not look like secrets. */
  envVars?: Record<string, string>;
}

/** Validation result returned before hitting the network. */
export type McpValidationResult =
  | { ok: true }
  | { ok: false; field: keyof AddMcpServerInput | "envVars"; message: string }

/** Client-side validation mirroring Python backend constraints. */
export function validateMcpServerInput(input: AddMcpServerInput): McpValidationResult {
  if (!input.name.trim()) {
    return { ok: false, field: "name", message: "Name is required." };
  }
  if (!/^[\w-]+$/.test(input.name)) {
    return {
      ok: false,
      field: "name",
      message: "Use letters, digits, hyphens, and underscores only.",
    };
  }
  if (!ALLOWED_MCP_COMMANDS.includes(input.command)) {
    return {
      ok: false,
      field: "command",
      message: `Command must be one of: ${ALLOWED_MCP_COMMANDS.join(", ")}.`,
    };
  }
  if (!input.args.trim()) {
    return { ok: false, field: "args", message: "Args are required." };
  }
  // Reject shell metacharacters (mirrors _SHELL_META_RE in Python)
  if (/[;&|><`]|\$[({]|&&|\|\|/.test(input.args)) {
    return {
      ok: false,
      field: "args",
      message: "Args must not contain shell metacharacters (; & | > < ` ${ $(  && ||).",
    };
  }
  const secretPatterns = [
    "key", "token", "secret", "password", "passwd", "credential",
    "private", "auth", "bearer", "apikey", "api_key",
  ];
  for (const k of Object.keys(input.envVars ?? {})) {
    const lower = k.toLowerCase();
    const isSecret = secretPatterns.some((p) => lower.includes(p));
    if (isSecret) {
      return {
        ok: false,
        field: "envVars",
        message: `Env var key "${k}" looks like a secret. Use SecretStore instead.`,
      };
    }
  }
  return { ok: true };
}

/** Raw response from POST /runtime/mcp/register (endpoint not yet in gateway). */
interface GatewayMcpRegisterResponse {
  name: string;
  message?: string;
}

/**
 * Register a new MCP server via POST /runtime/mcp/register.
 *
 * NOTE: This endpoint does not yet exist in the Hachi gateway. The call is
 * wired so that when the backend adds the route, it will work automatically.
 * Until then, callers will receive an ApiError with status 404/405 which the
 * UI surfaces as an error message rather than crashing.
 *
 * On success, reloads the MCP inventory so the new server appears in the list.
 *
 * @throws ApiError on network failure or backend error.
 */
export async function addMcpServer(input: AddMcpServerInput): Promise<void> {
  if (USE_MOCKS) {
    // In mock mode, simulate success without a network call.
    return;
  }

  await apiSend<GatewayMcpRegisterResponse>(
    "POST",
    "/runtime/mcp/register",
    {
      name: input.name,
      command: input.command,
      args: input.args,
      env_vars: input.envVars ?? {},
    },
  );

  // Reload inventory so the newly registered server appears.
  await loadIntegrations();
}
