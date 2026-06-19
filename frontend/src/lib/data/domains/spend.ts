// ============================================================
// Hachi Web App — Spend analytics domain loader
// NO dedicated route — just hydrates the store. Existing components
// (computeSpend / spend panel in work.tsx) auto-render from the slices.
//
// Endpoint:
//   GET /spend?session_id=<id>  (session_id optional — omit for current session)
//   → { turns:[{n,inT,out,cost}], tidies:[{ts,before,after}], models:[{name,tokens,cost,color}] }
// ============================================================

import { apiGet, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
import { useStore } from "../../store/store";
import type { SpendTurn, SpendTidy, SpendModel } from "../types";

// ---- Gateway shapes ----
// Field names intentionally mirror the UI types (gateway contract matches).

interface GatewaySpendTurn {
  n?: number;
  inT?: number;
  out?: number;
  cost?: number;
}

interface GatewaySpendTidy {
  ts?: string;
  before?: number;
  after?: number;
}

interface GatewaySpendModel {
  name?: string;
  tokens?: number;
  cost?: number;
  color?: string;
}

interface GatewaySpend {
  turns?: GatewaySpendTurn[];
  tidies?: GatewaySpendTidy[];
  models?: GatewaySpendModel[];
}

// ---- Color palette (assigned client-side when backend omits color) ----

const MODEL_COLORS = [
  "#C2703D",
  "#3D7AC2",
  "#5BA86A",
  "#A85B9E",
  "#C2A23D",
] as const;

function pickColor(index: number): string {
  return MODEL_COLORS[index % MODEL_COLORS.length];
}

// ---- Pure adapters ----

function adaptTurn(raw: GatewaySpendTurn): SpendTurn {
  return {
    n: raw.n ?? 0,
    inT: raw.inT ?? 0,
    out: raw.out ?? 0,
    cost: raw.cost ?? 0,
  };
}

function adaptTidy(raw: GatewaySpendTidy): SpendTidy {
  return {
    ts: raw.ts ?? "",
    before: raw.before ?? 0,
    after: raw.after ?? 0,
  };
}

function adaptModel(raw: GatewaySpendModel, index: number): SpendModel {
  return {
    name: raw.name ?? "unknown",
    tokens: raw.tokens ?? 0,
    cost: raw.cost ?? 0,
    // Assign a palette color when the backend sends an empty or missing color.
    color: raw.color && raw.color.trim() !== "" ? raw.color : pickColor(index),
  };
}

// ---- Loader ----

// Tracks whether this domain has ever received real data from the gateway.
// In live mode: until the first successful fetch, seeded fixtures must NOT be
// shown, so on failure we clear the slices instead of leaving them in place.
let _hasEverSucceeded = false;

export async function loadSpend(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures
  try {
    const raw = await apiGet<GatewaySpend>("/spend");
    const spendTurns: SpendTurn[] = (raw.turns ?? []).map(adaptTurn);
    const spendTidies: SpendTidy[] = (raw.tidies ?? []).map(adaptTidy);
    const spendModels: SpendModel[] = (raw.models ?? []).map(adaptModel);
    useStore.getState().hydrate({ spendTurns, spendTidies, spendModels });
    useStore.getState().setReconnecting(false);
    _hasEverSucceeded = true;
  } catch {
    if (!_hasEverSucceeded) {
      // First-load failure: clear seeded fixtures so we never present
      // fabricated spend data as if it were real gateway data.
      useStore.getState().hydrate({ spendTurns: [], spendTidies: [], spendModels: [] });
    }
    // Whether or not this is the first load, flag reconnecting so the UI
    // shows the reconnecting indicator. Last-known-good real data is
    // preserved after the first success.
    useStore.getState().setReconnecting(true);
  }
}

registerLoader("spend", loadSpend);
