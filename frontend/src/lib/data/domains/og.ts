// ============================================================
// Hachi Web App — OG explorer domain loader
// Endpoint: GET /og/graph → { nodes: GatewayOgNode[] } → OgNode[]
// ============================================================

import { apiGet, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
import { useStore } from "../../store/store";
import type { OgNode } from "../types";
import type { components } from "../schema";

// ---- Gateway shapes: DERIVED from the generated OpenAPI schema, not
// hand-mirrored (T-38 / BB-115). `GET /og/graph` now declares
// `response_model=OgGraphResponse` on the backend, so these are real types,
// not `unknown`. Regenerate with `npm run generate:api` after any gateway
// change (BB-4).
type GatewayOgNode = components["schemas"]["OgNodeModel"];
type GatewayOgGraph = components["schemas"]["OgGraphResponse"];

// ---- Pure adapter ----

export function adaptOgNode(raw: GatewayOgNode): OgNode {
  const validKinds = new Set<OgNode["kind"]>(["root", "skill", "leaf"]);
  const kind: OgNode["kind"] = validKinds.has(raw.kind as OgNode["kind"])
    ? (raw.kind as OgNode["kind"])
    : "skill";

  return {
    id: raw.id ?? "",
    name: raw.name ?? "",
    kind,
    index: raw.index ?? 0,
    dense: raw.dense ?? 0,
    full: raw.full ?? 0,
    deps: raw.deps ?? [],
  };
}

// ---- Loader ----

// Tracks whether this domain has ever received real data from the gateway.
// In live mode: until the first successful fetch, seeded fixtures must NOT be
// shown, so on failure we clear the slice instead of leaving them in place.
let _hasEverSucceeded = false;
// F3: in-flight guard — coalesces concurrent calls so bootstrapLiveData + the
// on-navigate useEffect don't double-fetch on mount. Resets after completion.
let _inFlight: Promise<void> | null = null;

export async function loadOg(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures
  if (_inFlight) return _inFlight;
  _inFlight = (async () => {
    try {
      const raw = await apiGet<GatewayOgGraph>("/og/graph");
      const ogNodes: OgNode[] = (raw.nodes ?? []).map(adaptOgNode);
      useStore.getState().hydrate({ ogNodes });
      _hasEverSucceeded = true;
    } catch {
      if (!_hasEverSucceeded) {
        // First-load failure: clear seeded fixtures so we never present
        // fabricated OG graph as if it were real gateway data.
        useStore.getState().hydrate({ ogNodes: [] });
      }
      // Reconnecting flag is now managed centrally by client.ts via the
      // registered callback — no per-domain setReconnecting calls needed.
    }
  })().finally(() => { _inFlight = null; });
  return _inFlight;
}

registerLoader("og", loadOg);
