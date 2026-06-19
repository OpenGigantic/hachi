import React, { useEffect } from 'react'
import { createRootRoute, Outlet } from '@tanstack/react-router'
import { Sidebar } from '../components/shell/Sidebar'
import { GlobalOverlays } from '../components/overlays'
import { useStore } from '../lib/store/store'
import { bootstrapLiveData } from '../lib/data/bootstrap'

function RootLayout() {
  const theme = useStore((s) => s.theme)
  const dataMode = useStore((s) => s.dataMode)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  // Hydrate every wired domain from the gateway once on mount. Each loader
  // degrades to seeded fixtures on failure, so this never blanks the UI.
  useEffect(() => {
    if (dataMode === 'live') void bootstrapLiveData()
  }, [dataMode])

  return (
    <div
      data-theme={theme}
      style={{
        display: 'flex',
        height: '100vh',
        width: '100%',
        overflow: 'hidden',
        background: 'var(--hachi-canvas)',
        color: 'var(--hachi-ink)',
        fontFamily: 'var(--font-body)',
        fontSize: '14px',
      }}
    >
      <Sidebar />
      <main
        style={{
          flex: 1,
          minWidth: 0,
          position: 'relative',
          overflow: 'auto',
        }}
      >
        <Outlet />
      </main>
      <GlobalOverlays />
    </div>
  )
}

export const Route = createRootRoute({
  component: RootLayout,
})
