// T-63 Phase 1 — route render smoke test.
// Job: fail loudly if the store slice split (later in this task) breaks the Work
// screen's ability to render at all. Not a behaviour test — just mounts the real
// route component through a minimal router (so useNavigate() etc. resolve) with the
// same client/sse mocking idiom as src/lib/store/store.test.ts, and asserts a stable
// landmark that is present in both the empty and populated states.
// This project does not set `test.globals` in vite.config.ts, so jest-dom's
// bare "." entry (which calls the ambient global `expect`) throws
// ReferenceError. The "/vitest" entry imports vitest's own `expect` and
// extends that instance instead -- see @testing-library/jest-dom/dist/vitest.mjs.
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";

// Mirror the existing mocks in src/lib/store/store.test.ts:17-31
vi.mock("../lib/data/client", () => ({
  USE_MOCKS: false,
  apiGet: vi.fn().mockResolvedValue(undefined),
  apiSend: vi.fn().mockResolvedValue(undefined),
  registerReconnectCallback: vi.fn(),
  isNetworkError: () => false,
  API_BASE: "/api",
  authHeaders: () => ({}),
  ApiError: class ApiError extends Error {},
}));
vi.mock("../lib/data/sse", () => ({ streamSSE: vi.fn() }));

// Imported after the mocks above so the module graph the route pulls in
// (loadWork/loadSpend/loadOperator -> lib/data/client) picks them up.
import { Route as WorkRoute } from "./work";

describe("work route", () => {
  afterEach(cleanup);

  it("renders without crashing", async () => {
    // Mount the route's own component as the router's root so hooks that need a
    // router context (useNavigate) resolve, without pulling in __root.tsx's shell
    // (Sidebar/GovernanceBar/GlobalOverlays) which is out of scope for this smoke test.
    const routeTree = createRootRoute({ component: WorkRoute.options.component! });
    const router = createRouter({
      routeTree,
      history: createMemoryHistory({ initialEntries: ["/"] }),
    });

    render(<RouterProvider router={router} />);

    // Generous timeout: this is the first mount in the file, so it pays React's +
    // TanStack Router's + this route's own useEffect-driven loader cold-start cost
    // (loadWork/loadSpend/loadOperator against the mocked client) in one go. The
    // default 1000ms was observed to be too tight for that (verified empirically).
    expect(
      await screen.findByRole("heading", { name: /^work$/i }, { timeout: 5000 })
    ).toBeInTheDocument();
    // Outer per-test timeout, bumped from vitest's 5000ms default: it exactly matched
    // the findByRole timeout above, so its clock (which starts before render(), not at
    // the findByRole call) could fire first and kill the test with a blunt "Test timed
    // out in 5000ms" before the generous inner wait got a chance to resolve. Reproduced
    // empirically: all four Phase 1 route smoke tests failed this way when the vitest
    // step ran alongside `pytest -n auto` (green in isolation). Hang-detector, not a
    // performance assertion -- raising it doesn't relax anything being checked.
  }, 15000);
});
