// ============================================================
// Guard: pins the *fact* that chat.ts's header comment describes (T-64 / CF-16).
//
// A prose comment can drift from the code silently. This test reads the real
// import statements off disk (not a hand-maintained list of module names) so
// that if the module graph ever changes, this test — not just a comment —
// notices. See src/lib/data/domains/chat.ts:6-14 for the comment this pins.
//
// Why `import.meta.glob` and not `node:fs`: this project has no `@types/node`
// (CLAUDE.md Rule 3 — no new dependency without asking), so `node:fs` /
// `node:path` / `__dirname` do not typecheck here (`tsc --noEmit` fails with
// TS2307 / TS2304). `import.meta.glob` is already typed via `vite/client`
// (see src/vite-env.d.ts), so reading source as text needs nothing new.
// `import.meta.glob` requires a literal glob pattern at its call site (Vite
// statically rewrites the call), so every file this test inspects is listed
// once below; `staticImportsOf` just looks the text up by path afterwards.
// ============================================================

import { describe, expect, it } from "vitest";

const MODULE_SOURCE = import.meta.glob<string>(
  [
    "./store/store.ts",
    "./store/slices/chat.ts",
    "./data/domains/chat.ts",
    "./data/domains/approvals.ts",
  ],
  { eager: true, query: "?raw", import: "default" },
);

function staticImportsOf(relativePath: string): string[] {
  const src = MODULE_SOURCE[`./${relativePath}`];
  if (src === undefined) {
    throw new Error(
      `module-graph test: "${relativePath}" is not in MODULE_SOURCE's glob list above — add it there.`
    );
  }
  // Static `import … from "…"` only — dynamic import() is deliberately excluded,
  // because a dynamic import does NOT create an initialisation-order cycle.
  return [...src.matchAll(/^import\s[\s\S]*?from\s+["']([^"']+)["'];/gm)].map((m) => m[1]);
}

describe("module graph", () => {
  it("store.ts and chat.ts import each other statically — keep the comment honest", () => {
    // If this ever stops being true, UPDATE THE COMMENT at
    // src/lib/data/domains/chat.ts:6-14 in the same commit.
    //
    // T-64 / CF-16: chat.ts's header used to claim the opposite — "no cycle ...
    // because store.ts never statically imports chat.ts". Asserted as the old
    // comment claimed (`.not.toContain`), this failed with:
    //   AssertionError: expected [ 'zustand', …(5) ] to not include
    //   '../data/domains/chat'
    // proving the comment wrong before it was corrected.
    //
    // T-63 split store.ts into typed slices under store/slices/*, so store.ts
    // itself no longer imports "../data/domains/chat" directly — the edge
    // moved one hop further, into store/slices/chat.ts (one of three slices
    // that import the chat domain for live-mode delegation; slices/liveRun.ts
    // and slices/sessions.ts also do, for the same reason). Pin the real
    // chain: store.ts -> slices/chat.ts -> data/domains/chat.ts -> store.ts.
    expect(staticImportsOf("store/store.ts")).toContain("./slices/chat");
    expect(staticImportsOf("store/slices/chat.ts")).toContain("../../data/domains/chat");
    expect(staticImportsOf("data/domains/chat.ts")).toContain("../../store/store");
  });

  it("the store -> chat -> approvals -> store cycle is the documented one", () => {
    expect(staticImportsOf("data/domains/chat.ts")).toContain("./approvals");
    expect(staticImportsOf("data/domains/approvals.ts")).toContain("../../store/store");
  });
});
