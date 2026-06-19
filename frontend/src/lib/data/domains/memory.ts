// ============================================================
// Hachi Web App — Memory domain loader
//
// Endpoints:
//   GET /memory            → GatewayMemory → memGroups, blocked, proposed
//   GET /memory/recall     → GatewayRecallHit[] → recall
//
// NOTE: the gateway exposes READ-ONLY endpoints. There are no write endpoints
// for facts (add/edit/delete) or for approving/rejecting proposed changes.
// The store's optimistic write actions (addFact/saveFact/delFact/approveProposed/
// rejectProposed) remain local-only until the gateway adds write support.
// ============================================================

import { apiGet, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
import { useStore } from "../../store/store";
import type { MemGroup, BlockedItem, ProposedChange, RecallHit } from "../types";

// ---- Gateway shapes ----

interface GatewayFact {
  id?: string;
  text?: string;
  source?: string;
}

interface GatewayMemGroup {
  id?: string;
  title?: string;
  facts?: GatewayFact[];
}

interface GatewayBlockedItem {
  id?: string;
  text?: string;
  reason?: string;
}

interface GatewayProposedChange {
  from?: string;
  to?: string;
}

interface GatewayMemory {
  groups?: GatewayMemGroup[];
  blocked?: GatewayBlockedItem[];
  proposed?: GatewayProposedChange | null;
}

interface GatewayRecallHit {
  fact?: string;
  score?: string | number;
  src?: string;
}

// ---- Pure adapters ----

function adaptFact(raw: GatewayFact): MemGroup["facts"][number] {
  return {
    id: raw.id ?? "",
    text: raw.text ?? "",
    source: raw.source ?? "",
  };
}

function adaptMemGroup(raw: GatewayMemGroup): MemGroup {
  return {
    id: raw.id ?? "",
    title: raw.title ?? "",
    facts: (raw.facts ?? []).map(adaptFact),
  };
}

function adaptBlockedItem(raw: GatewayBlockedItem): BlockedItem {
  return {
    id: raw.id ?? "",
    text: raw.text ?? "",
    reason: raw.reason ?? "",
  };
}

function adaptProposed(raw: GatewayProposedChange | null | undefined): ProposedChange | null {
  if (!raw) return null;
  return {
    from: raw.from ?? "",
    to: raw.to ?? "",
  };
}

function adaptRecallHit(raw: GatewayRecallHit): RecallHit {
  // score may be a number from the gateway but the UI type expects string
  const score =
    raw.score === undefined || raw.score === null
      ? ""
      : String(raw.score);
  return {
    fact: raw.fact ?? "",
    score,
    src: raw.src ?? "",
  };
}

// ---- Loaders ----

/** Load memory groups, blocked items, and proposed change from the gateway. */
export async function loadMemory(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures
  try {
    const raw = await apiGet<GatewayMemory>("/memory");
    useStore.getState().hydrate({
      memGroups: (raw.groups ?? []).map(adaptMemGroup),
      blocked: (raw.blocked ?? []).map(adaptBlockedItem),
      proposed: adaptProposed(raw.proposed),
    });
    useStore.getState().setReconnecting(false);
  } catch {
    useStore.getState().setReconnecting(true); // leave fixtures in place
  }
}

/**
 * Run a semantic recall search against the gateway and hydrate the store's
 * `recall` slice with the results.
 */
export async function recallMemory(query: string): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures
  try {
    const raw = await apiGet<GatewayRecallHit[]>("/memory/recall", {
      query,
      top_k: 8,
    });
    useStore.getState().hydrate({
      recall: (raw ?? []).map(adaptRecallHit),
    });
  } catch {
    useStore.getState().setReconnecting(true); // leave recall fixtures in place
  }
}

registerLoader("memory", loadMemory);
