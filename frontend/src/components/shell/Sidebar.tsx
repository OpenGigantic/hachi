import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate, useLocation } from '@tanstack/react-router'
import { Icon } from '../icons/Icon'
import { HachiMascot } from '../brand/HachiMascot'
import { Switch } from '../ui/Switch'
import { NavItem } from './NavItem'
import { BudgetWidget } from './BudgetWidget'
import { useStore } from '../../lib/store/store'
import { useIsMobile } from '../../lib/hooks/useMediaQuery'
import type { Run } from '../../lib/data/types'

interface NavEntry {
  key: string
  route: string
  label: string
  icon: string
}

const NAV_ITEMS: NavEntry[] = [
  { key: 'home',         route: '/',            label: 'Today',        icon: 'home'      },
  { key: 'conversation', route: '/conversation', label: 'Conversation', icon: 'chat'      },
  { key: 'work',         route: '/work',         label: 'Work',         icon: 'work'      },
  { key: 'approvals',    route: '/approvals',    label: 'The leash',    icon: 'leash'     },
  { key: 'memory',       route: '/memory',       label: 'Memory',       icon: 'memory'    },
  { key: 'skills',       route: '/skills',       label: 'The library',  icon: 'skills'    },
  { key: 'og',           route: '/og',           label: 'OG explorer',  icon: 'network'   },
  { key: 'authoring',    route: '/authoring',    label: 'Authoring',    icon: 'file-code' },
  { key: 'integrations', route: '/integrations', label: 'Integrations', icon: 'plug'      },
  { key: 'settings',     route: '/settings',     label: 'Settings',     icon: 'settings'  },
]

/** Inner sidebar contents — shared between desktop fixed sidebar and mobile drawer. */
function SidebarContents({ onNavClick }: { onNavClick?: () => void }) {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchHovered, setSearchHovered] = useState(false)
  const [operatorHovered, setOperatorHovered] = useState(false)

  const operator = useStore((s) => s.operator)
  const setOperator = useStore((s) => s.setOperator)
  const openPalette = useStore((s) => s.openPalette)
  const spent = useStore((s) => s.spent)
  const budget = useStore((s) => s.budget)
  const approvals = useStore((s) => s.approvals)

  const runs = useStore((s) => s.runs)
  const pendingApprovals = (approvals as unknown[]).length
  const activeRun = (runs as Run[]).find((r) => r.status === 'working')
  const blockedRun = (runs as Run[]).find((r) => r.status === 'blocked')
  const hachiState = blockedRun
    ? 'blocked'
    : pendingApprovals > 0
    ? 'blocked'
    : activeRun
    ? 'working'
    : 'on-watch'

  const currentPath = location.pathname

  const handleBudgetClick = () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    navigate({ to: '/work' as any })
    onNavClick?.()
  }

  return (
    <>
      {/* Mascot */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '8px 8px 18px',
        }}
      >
        <HachiMascot state={hachiState} scale={3} decor={false} />
      </div>

      {/* Search / palette button */}
      <button
        type="button"
        aria-label="Open command palette (Ctrl+K)"
        onClick={() => { openPalette(); onNavClick?.() }}
        onMouseEnter={() => setSearchHovered(true)}
        onMouseLeave={() => setSearchHovered(false)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '9px',
          width: '100%',
          height: '44px',
          padding: '0 11px',
          marginBottom: '14px',
          borderRadius: '10px',
          border: '0.5px solid var(--hachi-line)',
          background: searchHovered ? 'var(--hachi-cream)' : 'var(--hachi-canvas)',
          color: 'var(--hachi-muted)',
          cursor: 'pointer',
          font: 'inherit',
          fontSize: '13px',
          transition: 'background var(--dur) var(--ease)',
        }}
      >
        <Icon name="search" size={15} />
        <span style={{ flex: 1, textAlign: 'left' }}>Search or jump…</span>
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            border: '0.5px solid var(--hachi-line)',
            borderRadius: '5px',
            padding: '1px 5px',
          }}
        >
          ⌘K
        </span>
      </button>

      {/* Nav list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
        {NAV_ITEMS.map((item) => {
          const isActive = currentPath === item.route
          const badge = item.key === 'approvals' ? pendingApprovals : undefined
          return (
            <NavItem
              key={item.key}
              label={item.label}
              icon={item.icon}
              active={isActive}
              badge={badge}
              onClick={() => {
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                navigate({ to: item.route as any })
                onNavClick?.()
              }}
            />
          )
        })}
      </div>

      <div style={{ flex: 1 }} />

      {/* Operator view toggle */}
      <label
        onMouseEnter={() => setOperatorHovered(true)}
        onMouseLeave={() => setOperatorHovered(false)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '10px 12px',
          marginBottom: '10px',
          borderRadius: '10px',
          cursor: 'pointer',
          background: operatorHovered ? 'var(--hachi-cream)' : 'transparent',
          transition: 'background var(--dur) var(--ease)',
          minHeight: '44px',
        }}
      >
        <span style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}>
          <Icon name="cpu" size={17} />
        </span>
        <span style={{ flex: 1, fontSize: '13px', color: 'var(--hachi-ink)' }}>
          operator view
        </span>
        <Switch
          checked={operator}
          onChange={(v) => setOperator(v)}
        />
      </label>

      {/* Budget widget */}
      <BudgetWidget spent={spent} budget={budget} onClick={handleBudgetClick} />
    </>
  )
}

export function Sidebar() {
  const isMobile = useIsMobile()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const location = useLocation()

  // Close drawer on route change
  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname])

  // Close drawer on Escape
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape' && drawerOpen) setDrawerOpen(false)
  }, [drawerOpen])

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  // Prevent body scroll when drawer is open
  useEffect(() => {
    if (isMobile && drawerOpen) {
      document.body.style.overflow = 'hidden'
      return () => { document.body.style.overflow = '' }
    }
  }, [isMobile, drawerOpen])

  if (isMobile) {
    return (
      <>
        {/* Top bar — always visible on mobile */}
        <div className="hachi-topbar" role="banner">
          <button
            type="button"
            className="hachi-hamburger"
            aria-label={drawerOpen ? 'Close navigation' : 'Open navigation'}
            aria-expanded={drawerOpen}
            aria-controls="hachi-drawer"
            onClick={() => setDrawerOpen((v) => !v)}
          >
            <Icon name={drawerOpen ? 'x' : 'menu'} size={22} />
          </button>
          <span className="hachi-topbar-title">hachi</span>
        </div>

        {/* Backdrop */}
        {drawerOpen && (
          <div
            className="hachi-drawer-backdrop"
            aria-hidden="true"
            onClick={() => setDrawerOpen(false)}
          />
        )}

        {/* Drawer */}
        <aside
          id="hachi-drawer"
          className="hachi-sidebar-drawer"
          aria-label="Navigation"
          role="navigation"
          style={{
            background: 'var(--hachi-surface)',
            borderRight: '0.5px solid var(--hachi-line)',
            display: drawerOpen ? 'flex' : 'none',
            flexDirection: 'column',
            padding: '16px',
          }}
        >
          {/* Close button inside drawer */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '8px' }}>
            <button
              type="button"
              aria-label="Close navigation"
              onClick={() => setDrawerOpen(false)}
              style={{
                width: '44px',
                height: '44px',
                borderRadius: '10px',
                border: 'none',
                background: 'transparent',
                color: 'var(--hachi-muted)',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Icon name="x" size={18} />
            </button>
          </div>
          <SidebarContents onNavClick={() => setDrawerOpen(false)} />
        </aside>
      </>
    )
  }

  // Desktop: original fixed sidebar — unchanged
  return (
    <aside
      style={{
        width: '248px',
        flexShrink: 0,
        background: 'var(--hachi-surface)',
        borderRight: '0.5px solid var(--hachi-line)',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        padding: '16px',
      }}
    >
      <SidebarContents />
    </aside>
  )
}
