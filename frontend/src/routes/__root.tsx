import React, { useEffect } from 'react'
import { createRootRoute, Outlet } from '@tanstack/react-router'
import { Sidebar } from '../components/shell/Sidebar'
import { GovernanceBar } from '../components/shell/GovernanceBar'
import { GlobalOverlays } from '../components/overlays'
import { useStore } from '../lib/store/store'
import { bootstrapLiveData } from '../lib/data/bootstrap'
import { useIsMobile } from '../lib/hooks/useMediaQuery'

function RootLayout() {
  const theme = useStore((s) => s.theme)
  const dataMode = useStore((s) => s.dataMode)
  const isMobile = useIsMobile()

  useEffect(() => {
    if (theme === 'auto') {
      document.documentElement.removeAttribute('data-theme')
    } else {
      document.documentElement.setAttribute('data-theme', theme)
    }
  }, [theme])

  // Hydrate eager domains (config, sessions, spend) from the gateway on mount.
  // Each loader degrades to seeded fixtures on failure, so this never blanks the UI.
  useEffect(() => {
    if (dataMode === 'live') void bootstrapLiveData()
  }, [dataMode])

  return (
    <div
      data-theme={theme !== 'auto' ? theme : undefined}
      style={{
        display: 'flex',
        // On mobile the sidebar becomes an off-canvas drawer; the root
        // switches to column layout so the top-bar sits above <main>.
        flexDirection: isMobile ? 'column' : 'row',
        height: '100vh',
        width: '100%',
        overflow: 'hidden',
        background: 'var(--hachi-canvas)',
        color: 'var(--hachi-ink)',
        fontFamily: 'var(--font-body)',
        fontSize: '14px',
      }}
    >
      {/* On mobile: Sidebar renders only the top-bar + drawer overlay.
          On desktop: Sidebar renders the fixed left panel. */}
      <Sidebar />
      <main
        style={{
          flex: 1,
          minWidth: 0,
          // On mobile full height minus the 52px top-bar rendered inside Sidebar
          minHeight: 0,
          position: 'relative',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
          <Outlet />
        </div>
        <GovernanceBar />
      </main>
      <GlobalOverlays />
    </div>
  )
}

export const Route = createRootRoute({
  component: RootLayout,
})
