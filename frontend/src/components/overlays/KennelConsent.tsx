// ============================================================
// KennelConsent — skill-add permission overlay
// Prototype markup: Hachi Web App.dc.html lines 1428–1451
// Logic: lines 2014–2020 (cancelKennel/confirmKennel), 2323–2325 (kennelConsent renderVals)
// Shows exactly what the skill will be allowed to do.
// ============================================================

import React, { CSSProperties, useEffect, useState } from 'react';
import { useStore } from '../../lib/store/store';
import { Icon } from '../icons/Icon';
import { useFocusTrap } from '../../lib/hooks/useFocusTrap';

// Permission sets per skill (mirrored from prototype renderVals)
const KENNEL_PERMS: Record<string, string[]> = {
  GitHub:  ['Read your repositories and issues', 'Open pull requests for you', 'Comment on issues'],
  Notion:  ['Read your workspace pages', 'Create and edit pages'],
  Slack:   ['Read channels you choose', 'Post messages as you'],
};

export function KennelConsent() {
  const kc           = useStore((s) => s.kennelConsent);
  const cancelKennel = useStore((s) => s.cancelKennel);
  const confirmKennel = useStore((s) => s.confirmKennel);

  const isOpen = kc !== null;
  const trapRef = useFocusTrap<HTMLDivElement>(isOpen);

  // Close on Escape.
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cancelKennel();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [isOpen, cancelKennel]);

  if (!kc) return null;

  const perms = KENNEL_PERMS[kc.name] ?? ['Use this capability within the leash'];

  const backdropStyle: CSSProperties = {
    position: 'fixed',
    inset: 0,
    background: 'rgba(28,26,32,.4)',
    display: 'grid',
    placeItems: 'center',
    padding: 24,
    zIndex: 60,
  };

  const panelStyle: CSSProperties = {
    width: 460,
    maxWidth: '96vw',
    background: 'var(--hachi-surface)',
    border: '0.5px solid var(--hachi-line)',
    borderRadius: 18,
    overflow: 'hidden',
    boxShadow: '0 24px 60px rgba(28,26,32,.24)',
  };

  return (
    <div style={backdropStyle} onClick={cancelKennel}>
      <div
        ref={trapRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="kennel-consent-title"
        style={panelStyle}
        onClick={(e) => e.stopPropagation()}
      >

        {/* Header */}
        <div style={{ padding: '22px 22px 0', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{
            width: 44,
            height: 44,
            borderRadius: 11,
            background: 'var(--hachi-cream)',
            display: 'grid',
            placeItems: 'center',
          }}>
            <Icon name={kc.icon} size={22} />
          </div>
          <div>
            <div id="kennel-consent-title" style={{ fontFamily: 'var(--font-display)', fontWeight: 500, fontSize: 18 }}>
              Add {kc.name}
            </div>
            <div style={{ fontSize: 13, color: 'var(--hachi-muted)', marginTop: 1 }}>
              {kc.desc}
            </div>
          </div>
        </div>

        {/* Permission list */}
        <div style={{ padding: '18px 22px' }}>
          <div style={{ fontSize: 13, color: 'var(--hachi-muted)', marginBottom: 10 }}>
            What it will be allowed to do
          </div>
          <div style={{
            background: 'var(--hachi-canvas)',
            border: '0.5px solid var(--hachi-line)',
            borderRadius: 12,
            overflow: 'hidden',
          }}>
            {perms.map((p, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '11px 14px',
                  borderBottom: i < perms.length - 1 ? '0.5px solid var(--hachi-line)' : 'none',
                  fontSize: 14,
                }}
              >
                <span style={{ color: 'var(--hachi-brand)', display: 'inline-flex' }}>
                  <Icon name="leash" size={15} />
                </span>
                {p}
              </div>
            ))}
          </div>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 7,
            fontSize: 12,
            color: 'var(--hachi-muted)',
            marginTop: 12,
          }}>
            <Icon name="shield" size={13} />
            You can change or remove this any time. Hachi still asks before risky actions.
          </div>
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', gap: 10, padding: '0 22px 22px' }}>
          <CancelBtn onClick={cancelKennel} />
          <ConfirmBtn onClick={confirmKennel} />
        </div>
      </div>
    </div>
  );
}

function CancelBtn({ onClick }: { onClick: () => void }) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        flex: 1,
        height: 42,
        borderRadius: 10,
        border: '1px solid var(--hachi-trust)',
        background: hovered ? 'var(--hachi-trust-tint)' : 'transparent',
        color: 'var(--hachi-trust)',
        font: '500 14px var(--font-body)',
        cursor: 'pointer',
        transition: 'background var(--dur-fast) var(--ease)',
      }}
    >
      Cancel
    </button>
  );
}

function ConfirmBtn({ onClick }: { onClick: () => void }) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        flex: 1,
        height: 42,
        borderRadius: 10,
        border: '0.5px solid transparent',
        background: hovered ? 'var(--hachi-brand-hover)' : 'var(--hachi-brand)',
        color: 'var(--hachi-canvas)',
        font: '500 14px var(--font-body)',
        cursor: 'pointer',
        transition: 'background var(--dur-fast) var(--ease)',
      }}
    >
      Add and allow
    </button>
  );
}
