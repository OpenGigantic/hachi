// ============================================================
// Hachi Web App — MemoryGardener domain (T-08 A3)
//
// Endpoints:
//   GET  /memory/diffs            → { pending_review: GatewayGardenerProposal[] }
//   POST /memory/diffs/{id}       → body: { action: "approve" | "reject" }
//
// No seeded fixtures: this is a brand-new admin surface with no demo data
// (mirrors tests/eval-loop's `interactions` domain, which also has none) — it
// simply renders empty until a real gardener run produces proposals.
//
// Mutation pattern mirrors approvals.ts (BB-106): the decision POST is
// awaited FIRST and the store is only mutated once the gateway confirms it,
// so a failed or network-dropped decision never removes the card from the
// list while pretending the .og files were actually changed.
// ============================================================

import { apiGet, apiSend, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
import { invalidate } from "../cache";
import { useStore } from "../../store/store";
import type { GardenerProposal } from "../types";

// ---- Gateway shapes (mirror the Python dataclass/pydantic fields exactly) ----

interface GatewayGardenerProposal {
  id?: string;
  kind?: string;
  name_a?: string;
  name_b?: string;
  reason?: string;
  confidence?: number;
  file_path_a?: string;
  file_path_b?: string;
  auto_applied?: boolean;
}

interface GatewayGardenerDiffsResponse {
  pending_review?: GatewayGardenerProposal[];
}

interface GatewayGardenerDecisionResponse {
  ok?: boolean;
  id?: string;
  action?: string;
}

// ---- Adapter ----

function adaptProposal(raw: GatewayGardenerProposal): GardenerProposal {
  return {
    id: raw.id ?? "",
    kind: raw.kind ?? "",
    nameA: raw.name_a ?? "",
    nameB: raw.name_b ?? "",
    reason: raw.reason ?? "",
    confidence: raw.confidence ?? 0,
    filePathA: raw.file_path_a ?? "",
    filePathB: raw.file_path_b ?? "",
    autoApplied: raw.auto_applied ?? false,
  };
}

// ---- Loader ----

// Tracks whether this domain has ever received real data from the gateway.
// In live mode: until the first successful fetch, nothing (there are no
// seeded fixtures for this domain) is shown rather than a stale local guess.
let _hasEverSucceeded = false;
// In-flight guard — concurrent calls coalesce into the running promise.
let _inFlight: Promise<void> | null = null;

/** Load pending MemoryGardener review proposals from the gateway. */
export async function loadGardenerDiffs(): Promise<void> {
  if (USE_MOCKS) return; // no fixtures for this domain — stays empty
  if (_inFlight) return _inFlight;
  _inFlight = (async () => {
    try {
      const raw = await apiGet<GatewayGardenerDiffsResponse>("/memory/diffs");
      useStore.getState().hydrate({
        gardenerDiffs: (raw.pending_review ?? []).map(adaptProposal),
      });
      _hasEverSucceeded = true;
    } catch {
      if (!_hasEverSucceeded) {
        useStore.getState().hydrate({ gardenerDiffs: [] });
      }
      // Reconnecting flag is managed centrally by client.ts.
    }
  })().finally(() => {
    _inFlight = null;
  });
  return _inFlight;
}

registerLoader("memoryGardenerDiffs", loadGardenerDiffs);

// ---- Decision (approve/reject write-back) ----

const DECISION_FAILED_MESSAGE = "Couldn't send that decision — try again.";

/**
 * Approve or reject a pending gardener proposal.
 *
 * Non-optimistic (BB-106): awaits the gateway POST first and removes the
 * proposal from the store only once the gateway confirms it applied (or
 * dropped) the decision — a failed POST leaves the card in place instead of
 * pretending the .og files were changed.
 */
export async function decideGardenerDiff(
  id: string,
  action: "approve" | "reject"
): Promise<void> {
  const { showToast, gardenerDiffs } = useStore.getState();

  if (USE_MOCKS) {
    useStore.getState().hydrate({ gardenerDiffs: gardenerDiffs.filter((p) => p.id !== id) });
    return;
  }

  try {
    await apiSend<GatewayGardenerDecisionResponse>("POST", `/memory/diffs/${id}`, { action });
    useStore.getState().hydrate({
      gardenerDiffs: useStore.getState().gardenerDiffs.filter((p) => p.id !== id),
    });
    showToast(
      action === "approve" ? "Applied — memory updated" : "Dismissed",
      "success"
    );
  } catch {
    showToast(DECISION_FAILED_MESSAGE, "blocked");
  } finally {
    // Mark the domain stale so a revisit refetches the authoritative list
    // (e.g. a newer gardener tick may have already changed it).
    invalidate("memoryGardenerDiffs");
  }
}
