/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_HACHI_GATEWAY_URL?: string
  readonly VITE_HACHI_USE_MOCKS?: string
  readonly VITE_HACHI_TOKEN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

/**
 * Build-time literal injected by vite.config.ts's `define`, mirroring client.ts's
 * `USE_MOCKS` (computed from VITE_HACHI_USE_MOCKS) at build time rather than at
 * runtime. `USE_MOCKS` itself can never be constant-folded by Rollup -- it is derived
 * via `String(x).toLowerCase() === "true"`, and minifiers don't evaluate arbitrary
 * String.prototype calls -- so a runtime-only check leaves store.ts's dynamic
 * `import("../data/fixtures")` looking reachable and Rollup still emits fixtures.ts as
 * an (unreferenced but present) chunk in every build. Checking this literal FIRST lets
 * dead-code elimination prove the import unreachable and drop it entirely when mocks are
 * off. See vite.config.ts and store.ts's seedMockFixtures() (T-22 / BB-123).
 */
declare const __HACHI_USE_MOCKS__: boolean
