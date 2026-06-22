// GeneralSection — Mode (operator) + Appearance & account (theme, re-run setup).
// Carried over from the previous single-scroll Settings page; behaviour is
// unchanged, just grouped under the "General" panel.

import { useState } from 'react';
import { useStore } from '../../../lib/store/store';
import { Icon } from '../../icons/Icon';
import { Switch } from '../../ui/Switch';
import { cardStyle, sectionHeadingStyle, PanelHeader } from '../shared';

function SetupButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        height: 34,
        padding: '0 14px',
        borderRadius: 10,
        border: '0.5px solid var(--hachi-line)',
        background: hovered ? 'var(--hachi-cream)' : 'var(--hachi-canvas)',
        color: 'var(--hachi-ink)',
        font: '500 13px var(--font-body)',
        cursor: 'pointer',
        transition: 'background var(--dur-fast) var(--ease)',
      }}
    >
      {children}
    </button>
  );
}

export default function GeneralSection() {
  const operator = useStore((s) => s.operator);
  const toggleOperator = useStore((s) => s.toggleOperator);
  const theme = useStore((s) => s.theme);
  const toggleTheme = useStore((s) => s.toggleTheme);
  const openSetup = useStore((s) => s.openSetup);

  const systemIsDark =
    typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const isDark = theme === 'dark' || (theme === 'auto' && systemIsDark);
  const themeIcon = isDark ? 'moon' : 'sun';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PanelHeader title="General" subtitle="How Hachi looks and behaves day to day." />

      {/* Mode */}
      <div style={cardStyle}>
        <h3 style={{ ...sectionHeadingStyle, marginBottom: 16 }}>Mode</h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '11px 0' }}>
          <span style={{ color: 'var(--hachi-trust)', display: 'inline-flex' }}>
            <Icon name="cpu" size={17} />
          </span>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 500 }}>Operator mode</div>
            <div style={{ fontSize: 13, color: 'var(--hachi-muted)', marginTop: 2 }}>
              Shows tool signatures, expanded cards, and advanced controls. For power users and
              integrators.
            </div>
          </div>
          <Switch checked={operator} onChange={toggleOperator} />
        </div>
      </div>

      {/* Appearance & account */}
      <div style={cardStyle}>
        <h3 style={{ ...sectionHeadingStyle, marginBottom: 16 }}>Appearance &amp; account</h3>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            padding: '11px 0',
            borderBottom: '0.5px solid var(--hachi-line)',
          }}
        >
          <span style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}>
            <Icon name={themeIcon} size={17} />
          </span>
          <div style={{ flex: 1, fontSize: 14 }}>Dark mode</div>
          <Switch checked={isDark} onChange={() => toggleTheme()} />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '11px 0' }}>
          <span style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}>
            <Icon name="sparkles" size={17} />
          </span>
          <div style={{ flex: 1, fontSize: 14 }}>Re-run the setup wizard</div>
          <SetupButton onClick={openSetup}>Run setup</SetupButton>
        </div>
      </div>
    </div>
  );
}
