// ============================================================
// Hachi Web App — OG explorer domain loader
// Endpoint: GET /og/graph → { nodes: GatewayOgNode[] } → OgNode[]
// ============================================================

import { apiGet, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
import { useStore } from "../../store/store";
import type { OgNode } from "../types";

// ---- Gateway shape ----

interface GatewayOgNode {
  id: string;
  name: string;
  kind?: string;
  index?: number;
  dense?: number;
  full?: number;
  deps?: string[];
}

interface GatewayOgGraph {
  nodes: GatewayOgNode[];
}

// ---- Pure adapter ----

function adaptOgNode(raw: GatewayOgNode): OgNode {
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

export async function loadOg(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures
  try {
    const raw = await apiGet<GatewayOgGraph>("/og/graph");
    const ogNodes: OgNode[] = (raw.nodes ?? []).map(adaptOgNode);
    useStore.getState().hydrate({ ogNodes });
    useStore.getState().setReconnecting(false);
  } catch {
    useStore.getState().setReconnecting(true); // leave fixtures in place
  }
}

registerLoader("og", loadOg);
