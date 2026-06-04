import fs from "node:fs";
import process from "node:process";

// Server-only config. The .server.ts suffix prevents Vite from bundling
// this file into the client — values here never reach the browser.
//
// On Cloudflare Workers, env binds at REQUEST time. Module-scope reads
// (e.g. `const x = process.env.X`) resolve to undefined — always read
// process.env INSIDE a function or handler.
//
// When to use which env-access pattern:
//   - .server.ts module (this file): server-only helpers reused across
//     handlers. Wrap reads in a function so they run per-request.
//   - inline process.env inside a createServerFn handler: one-off reads
//     not reused elsewhere.
//   - import.meta.env.VITE_FOO: PUBLIC config readable from both client
//     and server (analytics IDs, public URLs). Define in .env with the
//     VITE_ prefix. Never put secrets here — they ship to the browser.

function normalizeAuthHeader(rawValue: string | undefined): string | undefined {
  if (!rawValue) {
    return undefined;
  }

  const value = rawValue.trim();
  if (!value) {
    return undefined;
  }

  if (value.startsWith("Bearer ")) {
    const token = value.slice("Bearer ".length).trim();
    return token ? `Bearer ${token}` : undefined;
  }

  // Bare token values are normalized to Bearer to reduce config foot-guns.
  if (!value.includes(" ")) {
    return `Bearer ${value}`;
  }

  return undefined;
}

export function getServerConfig() {
  const gatewayBaseUrl =
    process.env.TOKOBOROS_GATEWAY_BASE_URL ||
    process.env.VITE_TOKOBOROS_GATEWAY_BASE_URL ||
    "http://127.0.0.1:8787";
  const gatewayPollMs = Number.parseInt(process.env.TOKOBOROS_GATEWAY_POLL_MS || "1500", 10);
  let gatewayAuthHeader: string | undefined;

  const authHeaderFile = process.env.TOKOBOROS_GATEWAY_AUTH_HEADER_FILE;
  if (authHeaderFile) {
    try {
      const token = fs.readFileSync(authHeaderFile, "utf-8");
      gatewayAuthHeader = normalizeAuthHeader(token);
    } catch {
      gatewayAuthHeader = undefined;
    }
  }

  if (!gatewayAuthHeader) {
    gatewayAuthHeader = normalizeAuthHeader(process.env.TOKOBOROS_GATEWAY_AUTH_HEADER);
  }

  if (!gatewayBaseUrl.startsWith("http://") && !gatewayBaseUrl.startsWith("https://")) {
    throw new Error("TOKOBOROS_GATEWAY_BASE_URL must start with http:// or https://");
  }

  return {
    nodeEnv: process.env.NODE_ENV,
    gatewayBaseUrl,
    gatewayPollMs: Number.isNaN(gatewayPollMs) ? 1500 : gatewayPollMs,
    gatewayAuthHeader,
  };
}
