// ============================================================
// Hachi Web App — live-data bootstrap entry
//
// Imports `live.ts` FIRST so its loader registry (`const loaders`) is fully
// initialized, THEN eagerly imports every wired domain module so each one's
// top-level registerLoader() runs against the ready registry. This ordering is
// the fix for the circular-import temporal-dead-zone that would otherwise throw
// "Cannot access 'loaders' before initialization" and blank the app.
//
// `__root.tsx` imports bootstrapLiveData from HERE (not from ./live).
// ============================================================

import { bootstrapLiveData, registeredLoaderNames } from "./live";

// Order matters only in that `./live` (above) is imported before these.
import "./domains/approvals";
import "./domains/work";
import "./domains/operator";
import "./domains/chat";
import "./domains/config";
import "./domains/setup";
import "./domains/integrations";
// Formerly-gap domains, now wired to real gateway endpoints
// (see docs/web/backend-api-gaps.md §1–5).
import "./domains/skills";
import "./domains/memory";
import "./domains/og";
import "./domains/authoring";
import "./domains/spend";

export { bootstrapLiveData, registeredLoaderNames };
