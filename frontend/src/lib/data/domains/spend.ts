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

export async function loadSpend(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures
  try {
    const raw = await apiGet<GatewaySpend>("/spend");
    const spendTurns: SpendTurn[] = (raw.turns ?? []).map(adaptTurn);
    const spendTidies: SpendTidy[] = (raw.tidies ?? []).map(adaptTidy);
    const spendModels: SpendModel[] = (raw.models ?? []).map(adaptModel);
    useStore.getState().hydrate({ spendTurns, spendTidies, spendModels });
    useStore.getState().setReconnecting(false);
  } catch {
    useStore.getState().setReconnecting(true); // leave fixtures in place
  }
}

registerLoader("spend", loadSpend);
