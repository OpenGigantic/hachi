// ============================================================
// T-63 — store slice: dataMode (concern #2 of 22).
// No dedicated actions -- dataMode is set only via hydrate() (domain
// loaders / seedMockFixtures never call a setter for it). Moved verbatim
// from frontend/src/lib/store/store.ts.
// ============================================================
import type { StateCreator } from "zustand";
import { USE_MOCKS } from "../../data/client";
import type { Store } from "../store";

export interface DataModeState {
  // data source: "live" calls the gateway; "mock" keeps seeded fixtures.
  dataMode: "mock" | "live";
}

export const createDataModeSlice: StateCreator<Store, [], [], DataModeState> = () => ({
  dataMode: USE_MOCKS ? "mock" : "live",
});
