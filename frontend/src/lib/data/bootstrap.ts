// ============================================================
// Hachi Web App — live-data bootstrap entry
//
// Only the three domains that feed always-visible sidebar elements
// (budget/spend, session list, provider/leash) are loaded eagerly at
// boot. All other domains are loaded lazily when their route is first
// visited — see useRouteLoaders.ts.
//
// Imports `live.ts` FIRST so its loader registry (`const loaders`) is
// fully initialized, THEN imports the eager domain modules so each
// one's top-level registerLoader() runs against the ready registry.
// This ordering prevents the circular-import temporal-dead-zone that
// would otherwise throw "Cannot access 'loaders' before initialization".
//
// `__root.tsx` imports bootstrapLiveData from HERE (not from ./live).
// ============================================================

import { bootstrapLiveData, registeredLoaderNames } from "./live";

// ---- Eager domains (sidebar feeds only) ----
// These three run concurrently on every app mount:
//   - config.loadConfig   → provider + safety level (leash bar, settings)
//   - chat.loadSessions   → session list (sidebar)
//   - spend.loadSpend     → budget / spent (governance bar)
import "./domains/config";
import "./domains/chat";
import "./domains/spend";

// ---- Non-critical domains (lazy, via useRouteLoaders) ----
// These are imported at module scope so their registerLoader() side-effects
// run (making them importable without re-loading), but they are NOT added
// to the bootstrap registry — they will be called on first route visit.
// NOTE: Do not add registerLoader() calls for these; they are self-contained.
// They will be dynamically imported by useRouteLoaders.ts as needed.

export { bootstrapLiveData, registeredLoaderNames };
