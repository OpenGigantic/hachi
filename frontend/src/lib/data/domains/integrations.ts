// ============================================================
// Hachi Web App — integrations + MCP inventory domain
// Wires:
//   GET /capabilities  → Integration[] (native integrations only)
//   GET /runtime/mcp/inventory → McpServer[] + McpTool[]
//   PUT /capabilities/{id}/enabled → connect/disconnect toggle
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

/**
 * Load native integrations (GET /capabilities) and MCP inventory
 * (GET /runtime/mcp/inventory), then hydrate the store.
 *
 * On any error: setReconnecting(true) and leave fixtures in place.
 * On success: setReconnecting(false).
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
  } catch {
    // Leave fixtures intact; signal the UI that we're offline/degraded.
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

/**
 * "Connect" an integration — optimistic store update + gateway call.
 *
 * Applies the optimistic update instantly for snappy UI, then calls
 * PUT /capabilities/{id}/enabled with { enabled: true }. On error,
 * signals setReconnecting(true) and re-runs the loader to restore
 * the last known gateway truth.
 */
export async function connectIntegration(id: string): Promise<void> {
  useStore.getState().connectIntegration(id);
  if (USE_MOCKS) return;

  const { setReconnecting } = useStore.getState();
  try {
    await apiSend<GatewayCapabilityToggleResponse>(
      "PUT",
      `/capabilities/${id}/enabled`,
      { enabled: true },
    );
    // Re-run the loader so derived status reflects gateway truth.
    await loadIntegrations();
  } catch {
    setReconnecting(true);
  }
}

/**
 * "Disconnect" an integration — optimistic store update + gateway call.
 *
 * Applies the optimistic update instantly for snappy UI, then calls
 * PUT /capabilities/{id}/enabled with { enabled: false }. On error,
 * signals setReconnecting(true) and re-runs the loader to restore
 * the last known gateway truth.
 */
export async function disconnectIntegration(id: string): Promise<void> {
  useStore.getState().disconnectIntegration(id);
  if (USE_MOCKS) return;

  const { setReconnecting } = useStore.getState();
  try {
    await apiSend<GatewayCapabilityToggleResponse>(
      "PUT",
      `/capabilities/${id}/enabled`,
      { enabled: false },
    );
    // Re-run the loader so derived status reflects gateway truth.
    await loadIntegrations();
  } catch {
    setReconnecting(true);
  }
}
