// Tests for the og domain adapter — T-38 / BB-115.
//
// Locks two things:
// 1. `components["schemas"]["OgNodeModel"]` is a real generated type (not
//    `unknown`) — if the backend's `response_model` on GET /og/graph ever
//    regresses, this file fails to type-check (`npm run typecheck`), which is
//    half of the contract this pilot exists to prove.
// 2. The REAL exported `adaptOgNode` (imported from ./og, not a re-implemented
//    copy) maps a generated-schema node to the display model correctly — the
//    runtime half. A test that only declares a typed literal checks the types
//    but not the adapter (see T-38 §7b).

import { describe, expect, it } from "vitest";
import { adaptOgNode } from "./og";
import type { components } from "../schema";

// If `OgNodeModel` does not exist on `components["schemas"]`, this alias
// fails `tsc` — that IS the type-level contract check.
type GatewayOgNode = components["schemas"]["OgNodeModel"];

describe("og domain — adaptOgNode (T-38 / BB-115)", () => {
  it("maps a generated-schema node to the display model", () => {
    const raw: GatewayOgNode = {
      id: "brain",
      name: "brain",
      kind: "root",
      index: 12,
      dense: 40,
      full: 900,
      deps: [],
    };

    expect(adaptOgNode(raw)).toEqual({
      id: "brain",
      name: "brain",
      kind: "root",
      index: 12,
      dense: 40,
      full: 900,
      deps: [],
    });
  });

  it("falls back to 'skill' for a kind the display model does not recognise", () => {
    const raw: GatewayOgNode = {
      id: "n1",
      name: "n1",
      kind: "not-a-real-kind",
      index: 1,
      dense: 2,
      full: 3,
      deps: ["brain"],
    };

    expect(adaptOgNode(raw).kind).toBe("skill");
  });

  it("preserves deps and node identity fields as-is", () => {
    const raw: GatewayOgNode = {
      id: "n2",
      name: "deep_research",
      kind: "leaf",
      index: 0,
      dense: 0,
      full: 0,
      deps: ["a", "b"],
    };

    const node = adaptOgNode(raw);
    expect(node.id).toBe("n2");
    expect(node.name).toBe("deep_research");
    expect(node.deps).toEqual(["a", "b"]);
  });
});
