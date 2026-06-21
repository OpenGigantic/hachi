// ============================================================
// Hachi Web App — route-triggered lazy domain loader
//
// Watches the current pathname and, on first visit to each route,
// fires the matching domain loader(s) without editing individual
// route components.
//
// Only non-critical domains are listed here. The three eager domains
// (config, chat/sessions, spend) are already loaded by bootstrap.ts.
//
// Double-calls are harmless: loadConfig/loadSpend use dedupe(), and all
// other loaders are idempotent (they overwrite the store slice).
// ============================================================

import { useEffect, useRef } from "react";
import { useLocation } from "@tanstack/react-router";

// Track which pathnames have already triggered their loader (per session).
const _loaded = new Set<string>();

export function useRouteLoaders(): void {
  const location = useLocation();
  const pathname = location.pathname;
  // Stable ref so the effect callback always sees the latest pathname
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;

  useEffect(() => {
    const p = pathnameRef.current;

    // Already triggered for this path in this session — skip.
    if (_loaded.has(p)) return;
    _loaded.add(p);

    // ---- Route → loader(s) map ----
    if (p === "/memory") {
      void import("./domains/memory").then((m) => void m.loadMemory());
      return;
    }
    if (p === "/og") {
      void import("./domains/og").then((m) => void m.loadOg());
      return;
    }
    if (p === "/authoring") {
      void import("./domains/authoring").then((m) => void m.loadAuthoring());
      return;
    }
    if (p === "/work") {
      void import("./domains/work").then((m) => void m.loadWork());
      void import("./domains/spend").then((m) => void m.loadSpend());
      return;
    }
    if (p === "/integrations") {
      void import("./domains/integrations").then((m) => void m.loadIntegrations());
      return;
    }
    if (p === "/skills") {
      void import("./domains/skills").then((m) => {
        void m.loadSkills();
        void m.loadKennel();
      });
      return;
    }
    if (p === "/approvals") {
      void import("./domains/approvals").then((m) => void m.loadApprovals());
      return;
    }
    if (p === "/run") {
      void import("./domains/operator").then((m) => void m.loadOperator());
      return;
    }
    if (p === "/settings") {
      void import("./domains/setup").then((m) => void m.loadSetup());
      // config is already eager, but dedupe makes the extra call free
      void import("./domains/config").then((m) => void m.loadConfig());
      return;
    }
    // "/" (home / conversation) — no additional non-critical domain needed
  }, [pathname]);
}
