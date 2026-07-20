import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { tanstackRouter } from '@tanstack/router-plugin/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Load HACHI_* (non-VITE) vars too so the dev proxy can read the token.
  const env = loadEnv(mode, process.cwd(), ['VITE_', 'HACHI_'])
  const gatewayTarget = env.HACHI_GATEWAY_PROXY_TARGET || 'http://127.0.0.1:8787'
  const gatewayToken = env.HACHI_GATEWAY_TOKEN || ''
  // T-22 / BB-123: mirrors client.ts's USE_MOCKS, but as a build-time literal instead of
  // a runtime `String(x).toLowerCase() === "true"` expression, which Rollup can never
  // constant-fold. Lets store.ts's seedMockFixtures() gate its dynamic
  // `import("../data/fixtures")` on something the bundler CAN prove false in a mocks-off
  // build, so the ~400-line demo-fixture corpus is dropped from the build entirely
  // instead of merely shipping as an unreferenced chunk. See vite-env.d.ts.
  const useMocks = String(env.VITE_HACHI_USE_MOCKS ?? '').toLowerCase() === 'true'

  return {
    define: {
      __HACHI_USE_MOCKS__: JSON.stringify(useMocks),
    },
    plugins: [
      // tanstackRouter MUST come before react
      tanstackRouter({
        target: 'react',
        autoCodeSplitting: true,
      }),
      react(),
    ],
    server: {
      port: 5173,
      proxy: {
        // Proxy REST + SSE to the gateway, stripping the /api prefix and
        // injecting the bearer token so the browser never holds it. SSE works
        // because we never buffer the streamed body.
        '/api': {
          target: gatewayTarget,
          changeOrigin: true,
          ws: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (gatewayToken) {
                proxyReq.setHeader('Authorization', `Bearer ${gatewayToken}`)
              }
            })
          },
        },
      },
    },
    test: {
      environment: 'jsdom',
    },
  }
})
