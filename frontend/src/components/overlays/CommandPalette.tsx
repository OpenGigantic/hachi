// ============================================================
// CommandPalette — global ⌘K / Ctrl+K overlay
// Prototype markup: Hachi Web App.dc.html lines 1311–1335
// Logic: lines 2133–2136, 2410–2432 (palette renderVals)
// ============================================================

import React, { useEffect, useRef, CSSProperties } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useStore } from '../../lib/store/store';
import { Icon } from '../icons/Icon';
import { useFocusTrap } from '../../lib/hooks/useFocusTrap';

// ---- nav route map (mirrors _navMeta in prototype) ----
const navRoutes: Record<string, string> = {
  home: '/',
  chat: '/conversation',
  work: '/work',
  approvals: '/approvals',
  memory: '/memory',
  skills: '/skills',
  og: '/og',
  authoring: '/authoring',
  integrations: '/integrations',
  settings: '/settings',
};

const navMeta = [
  { key: 'home',         label: 'Today',         icon: 'home' },
  { key: 'chat',         label: 'Conversation',   icon: 'chat' },
  { key: 'work',         label: 'Work',           icon: 'work' },
  { key: 'approvals',    label: 'The leash',      icon: 'leash' },
  { key: 'memory',       label: 'Memory',         icon: 'memory' },
  { key: 'skills',       label: 'The library',    icon: 'skills' },
  { key: 'og',           label: 'OG explorer',    icon: 'network' },
  { key: 'authoring',    label: 'Authoring',      icon: 'file-code' },
  { key: 'integrations', label: 'Integrations',   icon: 'plug' },
  { key: 'settings',     label: 'Settings',       icon: 'settings' },
];

export function CommandPalette() {
  const open        = useStore((s) => s.palette.open);
  const query       = useStore((s) => s.palette.query);
  const closePalette     = useStore((s) => s.closePalette);
  const setPaletteQuery  = useStore((s) => s.setPaletteQuery);
  const openPalette      = useStore((s) => s.openPalette);
  const openSetup        = useStore((s) => s.openSetup);
  const simulateReconnect = useStore((s) => s.simulateReconnect);
  const openRun          = useStore((s) => s.openRun);
  const startTask        = useStore((s) => s.startTask);

  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  // Focus trap keeps keyboard focus inside the palette while open.
  const trapRef = useFocusTrap<HTMLDivElement>(open);

  // Global ⌘K / Ctrl+K toggle + Esc to close
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        if (open) {
          closePalette();
        } else {
          openPalette();
        }
      }
      if (e.key === 'Escape') {
        closePalette();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, closePalette, openPalette]);

  // Focus input when opened
  useEffect(() => {
    if (open && inputRef.current) {
      inputRef.current.focus();
    }
  }, [open]);

  if (!open) return null;

  const pq = query.trim().toLowerCase();

  const go = (route: string) => {
    closePalette();
    navigate({ to: route });
  };

  const placesAll = navMeta.map((n) => ({
    label: 'Go to ' + n.label,
    icon: n.icon,
    shortcut: null as string | null,
    go: () => go(navRoutes[n.key] || '/'),
  }));

  const actionsAll = [
    { label: 'Start a new task',             icon: 'plus',     shortcut: 'N',  go: () => go('/conversation') },
    { label: 'Watch a live run',             icon: 'eye',      shortcut: null, go: () => { closePalette(); openRun(undefined, 'home'); } },
    { label: 'Create a routine',             icon: 'repeat',   shortcut: null, go: () => { closePalette(); startTask('Every weekday at 8, summarise my inbox and message me the urgent ones'); navigate({ to: '/conversation' }); } },
    { label: 'Tighten a spend limit',        icon: 'dollar',   shortcut: null, go: () => go('/settings') },
    { label: 'Connect an account',           icon: 'plug',     shortcut: null, go: () => go('/integrations') },
    { label: 'Re-run setup',                 icon: 'sparkles', shortcut: null, go: () => { closePalette(); openSetup(); } },
    { label: 'Preview the reconnecting state', icon: 'refresh', shortcut: null, go: () => { closePalette(); simulateReconnect(); } },
  ];

  const recentsAll = [
    { label: 'Reply to Priya about Q3 deck', icon: 'chat',   shortcut: null, go: () => go('/conversation') },
    { label: 'Morning briefing routine',     icon: 'repeat', shortcut: null, go: () => go('/work') },
    { label: 'Memory: about you',            icon: 'memory', shortcut: null, go: () => go('/memory') },
  ];

  const filt = <T extends { label: string }>(arr: T[]) =>
    arr.filter((x) => !pq || x.label.toLowerCase().includes(pq));

  const paletteGroups = [
    { title: 'Go to',  items: filt(placesAll) },
    { title: 'Do',     items: filt(actionsAll) },
    { title: 'Recent', items: filt(recentsAll) },
  ].filter((g) => g.items.length > 0);

  const isEmpty = paletteGroups.length === 0;

  const backdropStyle: CSSProperties = {
    position: 'fixed',
    inset: 0,
    background: 'rgba(28,26,32,.32)',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'flex-start',
    paddingTop: '14vh',
    zIndex: 50,
  };

  const panelStyle: CSSProperties = {
    width: 560,
    maxWidth: '92vw',
    background: 'var(--hachi-surface)',
    border: '0.5px solid var(--hachi-line)',
    borderRadius: 16,
    overflow: 'hidden',
    boxShadow: '0 24px 60px rgba(28,26,32,.22)',
  };

  const inputRowStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 11,
    padding: '16px 18px',
    borderBottom: '0.5px solid var(--hachi-line)',
  };

  const inputStyle: CSSProperties = {
    flex: 1,
    border: 'none',
    background: 'transparent',
    font: 'inherit',
    fontSize: 16,
    color: 'var(--hachi-ink)',
    outline: 'none',
  };

  const escPillStyle: CSSProperties = {
    fontFamily: 'var(--font-mono)',
    fontSize: 11,
    border: '0.5px solid var(--hachi-line)',
    borderRadius: 5,
    padding: '2px 6px',
    color: 'var(--hachi-muted)',
  };

  const listStyle: CSSProperties = {
    maxHeight: '50vh',
    overflow: 'auto',
    padding: 8,
  };

  const groupLabelStyle: CSSProperties = {
    padding: '8px 10px 4px',
    fontSize: 12,
    color: 'var(--hachi-muted)',
  };

  return (
    <div
      style={backdropStyle}
      onClick={closePalette}
    >
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        style={panelStyle}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search row */}
        <div style={inputRowStyle}>
          <Icon name="search" size={18} style={{ color: 'var(--hachi-muted)' }} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setPaletteQuery(e.target.value)}
            placeholder="Jump to a place, or do something…"
            style={inputStyle}
          />
          <span style={escPillStyle}>esc</span>
        </div>

        {/* Results */}
        <div className="hachi-scroll" style={listStyle}>
          {paletteGroups.map((grp) => (
            <div key={grp.title}>
              <div style={groupLabelStyle}>{grp.title}</div>
              {grp.items.map((item) => (
                <PaletteItem key={item.label} item={item} />
              ))}
            </div>
          ))}
          {isEmpty && (
            <div style={{ padding: 20, textAlign: 'center', color: 'var(--hachi-muted)', fontSize: 14 }}>
              Nothing matches "{query}".
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

interface PaletteItemData {
  label: string;
  icon: string;
  shortcut: string | null;
  go: () => void;
}

function PaletteItem({ item }: { item: PaletteItemData }) {
  const [hovered, setHovered] = React.useState(false);

  const btnStyle: CSSProperties = {
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '10px 12px',
    border: 'none',
    background: hovered ? 'var(--hachi-cream)' : 'transparent',
    borderRadius: 10,
    cursor: 'pointer',
    textAlign: 'left',
    font: 'inherit',
    transition: 'background var(--dur-fast) var(--ease)',
  };

  const shortcutStyle: CSSProperties = {
    fontFamily: 'var(--font-mono)',
    fontSize: 11,
    color: 'var(--hachi-muted)',
    border: '0.5px solid var(--hachi-line)',
    borderRadius: 5,
    padding: '1px 6px',
  };

  return (
    <button
      style={btnStyle}
      onClick={item.go}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <span style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}>
        <Icon name={item.icon} size={17} />
      </span>
      <span style={{ flex: 1, fontSize: 14, color: 'var(--hachi-ink)' }}>
        {item.label}
      </span>
      {item.shortcut && (
        <span style={shortcutStyle}>{item.shortcut}</span>
      )}
    </button>
  );
}
