// ============================================================
// Hachi Web App — Skills domain loader
// Endpoint: GET /skills → GatewaySkill[] → Skill[]
// ============================================================

import { apiGet, USE_MOCKS } from "../client";
import { registerLoader } from "../live";
import { useStore } from "../../store/store";
import type { Skill } from "../types";

// ---- Gateway shape ----

interface GatewaySkill {
  name: string;
  role: string;
  keyword_count: number;
  use_count: number;
  last_used: string | null;
}

// ---- Pure adapter ----

/** Returns a human-readable relative date string or "never". */
function humanizeLastUsed(isoDate: string | null | undefined): string {
  if (!isoDate) return "never";
  try {
    const d = new Date(isoDate);
    if (isNaN(d.getTime())) return "never";
    const now = Date.now();
    const diffMs = now - d.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return "today";
    if (diffDays === 1) return "yesterday";
    if (diffDays < 7) return `${diffDays}d ago`;
    if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`;
    if (diffDays < 365) return `${Math.floor(diffDays / 30)}mo ago`;
    return `${Math.floor(diffDays / 365)}y ago`;
  } catch {
    return "never";
  }
}

/** True when last_used is null OR more than 30 days ago. */
function isStale(isoDate: string | null | undefined): boolean {
  if (!isoDate) return true;
  try {
    const d = new Date(isoDate);
    if (isNaN(d.getTime())) return true;
    const diffMs = Date.now() - d.getTime();
    return diffMs > 30 * 24 * 60 * 60 * 1000;
  } catch {
    return true;
  }
}

function adapt(raw: GatewaySkill): Skill {
  const useCount = raw.use_count ?? 0;
  return {
    id: raw.name ?? "unknown",
    icon: "sparkles",
    name: raw.name ?? "unknown",
    desc: raw.role ? `role: ${raw.role}` : "skill",
    uses: `${useCount} times`,
    lastUsed: humanizeLastUsed(raw.last_used),
    stale: isStale(raw.last_used),
  };
}

// ---- Loader ----

export async function loadSkills(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures
  try {
    const raw = await apiGet<GatewaySkill[]>("/skills");
    useStore.getState().hydrate({ skills: (raw ?? []).map(adapt) });
    useStore.getState().setReconnecting(false);
  } catch {
    useStore.getState().setReconnecting(true); // leave fixtures in place
  }
}

registerLoader("skills", loadSkills);
