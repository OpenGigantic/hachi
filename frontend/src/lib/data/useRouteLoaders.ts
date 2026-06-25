// ============================================================
// Hachi Web App — route-triggered lazy domain loader
//
// Watches the current pathname and fires the matching domain loader(s)
// using the stale-while-revalidate cache (cache.ts).  Revisiting a route
// within the TTL is a no-op (store data is still fresh).  Revisiting after
// the TTL triggers a background refetch while the store keeps showing the
// previous data.
//
// Only non-critical domains are listed here. The three eager domains
// (config, chat/sessions, spend) are already loaded by bootstrap.ts.
//
// Double-calls are harmless: loadConfig/loadSpend use dedupe(), and all
// other loaders are idempotent (they overwrite the store slice).  The cache
// layer adds an additional short-circuit so loaders are not even invoked
// when data is still fresh.
// ============================================================

import { useEffect, useRef } from "react";
import { useLocation } from "@tanstack/react-router";
import { loadCached, DEFAULT_TTL_MS } from "./cache";

// TTL overrides for domains that change more/less frequently than the default.
// Approvals are polled more aggressively because they are time-sensitive.
const APPROVALS_TTL_MS = 10_000; // 10 s — pending approvals expire fast
const SPEND_TTL_MS = 60_000;     // 60 s — spend rarely changes mid-session

export function useRouteLoaders(): void {
  const location = useLocation();
  const pathname = location.pathname;
  // Stable ref so the effect callback always sees the latest pathname
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;

  useEffect(() => {
    const p = pathnameRef.current;

    // ---- Route → loader(s) map ----
    if (p === "/memory") {
      void import("./domains/memory").then((m) =>
        loadCached("memory", () => m.loadMemory())
      );
      return;
    }
    if (p === "/og") {
      void import("./domains/og").then((m) =>
        loadCached("og", () => m.loadOg())
      );
      return;
    }
    if (p === "/authoring") {
      void import("./domains/authoring").then((m) =>
        loadCached("authoring", () => m.loadAuthoring())
      );
      return;
    }
    if (p === "/work") {
      void import("./domains/work").then((m) =>
        loadCached("work", () => m.loadWork())
      );
      void import("./domains/spend").then((m) =>
        loadCached("spend", () => m.loadSpend(), { ttlMs: SPEND_TTL_MS })
      );
      return;
    }
    if (p === "/integrations") {
      void import("./domains/integrations").then((m) =>
        loadCached("integrations", () => m.loadIntegrations())
      );
      return;
    }
    if (p === "/skills") {
      void import("./domains/skills").then((m) => {
        void loadCached("skills", () => m.loadSkills());
        void loadCached("kennel", () => m.loadKennel());
      });
      return;
    }
    if (p === "/approvals") {
      void import("./domains/approvals").then((m) =>
        loadCached("approvals", () => m.loadApprovals(), { ttlMs: APPROVALS_TTL_MS })
      );
      return;
    }
    if (p === "/run") {
      void import("./domains/operator").then((m) =>
        loadCached("operator", () => m.loadOperator())
      );
      return;
    }
    if (p === "/settings") {
      void import("./domains/setup").then((m) =>
        loadCached("setup", () => m.loadSetup())
      );
      // config is already eager, but dedupe makes the extra call free
      void import("./domains/config").then((m) =>
        loadCached("config", () => m.loadConfig())
      );
      return;
    }
    if (p === "/review") {
      void import("./domains/interactions").then((m) =>
        loadCached("interactions", () => m.loadInteractions())
      );
      return;
    }
    // "/" (home / conversation) — no additional non-critical domain needed
  }, [pathname]);
}

// Re-export DEFAULT_TTL_MS so consumers can reference the baseline without
// importing cache.ts directly (convenience only).
export { DEFAULT_TTL_MS };
