// ============================================================
// SetupWizard — 5-step onboarding modal
// Prototype markup: Hachi Web App.dc.html lines 1337–1426
// Logic: lines 2139–2158 (setup actions), 2434–2449 (setup renderVals)
// ============================================================

import React, { CSSProperties, useEffect, useState } from 'react';
import { useStore } from '../../lib/store/store';
import { HachiMark } from '../brand/HachiMark';
import { Icon } from '../icons/Icon';
import { Switch } from '../ui/Switch';
import { loadSetup, applySetup } from '../../lib/data/domains/setup';

const SAFETY_LEVELS = [
  {
    k: 'guarded' as const,
    name: 'Guarded',
    desc: 'Hachi asks before almost everything. Best while you\'re getting to know it.',
  },
  {
    k: 'standard' as const,
    name: 'Standard',
    desc: 'Hachi handles routine, safe work on its own and asks before anything that leaves your machine or deletes things.',
  },
  {
    k: 'open' as const,
    name: 'Open',
    desc: 'Hachi acts without asking. For trusted, sandboxed work only.',
  },
];

export function SetupWizard() {
  const setup            = useStore((s) => s.setup);
  const closeSetup       = useStore((s) => s.closeSetup);
  const setupBack        = useStore((s) => s.setupBack);
  const setupNext        = useStore((s) => s.setupNext);
  const setSetupKey      = useStore((s) => s.setSetupKey);
  const toggleSetupRoutine  = useStore((s) => s.toggleSetupRoutine);
  const toggleSetupGoogle   = useStore((s) => s.toggleSetupGoogle);
  const toggleSetupChannel  = useStore((s) => s.toggleSetupChannel);
  const setSetupField    = useStore((s) => s.setSetupField);

  // Load real onboarding state on mount (idempotent; mock-safe via USE_MOCKS check inside).
  useEffect(() => {
    void loadSetup();
  }, []);

  if (!setup.open) return null;

  const step = setup.step;
  const keyValid   = setup.key.trim().length >= 12;
  const keyInvalid = setup.key.trim().length > 0 && !keyValid;
  const keyBorder  = keyValid
    ? 'var(--hachi-success)'
    : keyInvalid
    ? 'var(--hachi-danger)'
    : 'var(--hachi-line)';

  const setupDots = [0, 1, 2, 3, 4].map((i) =>
    i === step
      ? 'var(--hachi-brand)'
      : i < step
      ? 'var(--hachi-brand-tint)'
      : 'var(--hachi-line)'
  );

  const nextLabel = step >= 4 ? 'Finish' : step === 0 ? 'Get started' : 'Next';
  const skipLabel = step >= 4 ? '' : 'Skip';
  const canBack   = step > 0;

  // On the final step, also fire the live gateway apply call (additive to the
  // existing optimistic store flow in setupNext which closes the wizard).
  const handleNext = () => {
    if (step >= 4) {
      // Fire-and-forget: errors are handled inside applySetup (setReconnecting).
      void applySetup({
        key:     setup.key,
        safety:  setup.safety,
        routine: setup.routine,
        google:  setup.google,
        channel: setup.channel,
      });
    }
    setupNext();
  };

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
    width: 540,
    maxWidth: '96vw',
    background: 'var(--hachi-surface)',
    border: '0.5px solid var(--hachi-line)',
    borderRadius: 18,
    overflow: 'hidden',
    boxShadow: '0 24px 60px rgba(28,26,32,.24)',
  };

  const headerStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '18px 22px',
    borderBottom: '0.5px solid var(--hachi-line)',
  };

  const bodyStyle: CSSProperties = {
    padding: '26px 22px',
    minHeight: 230,
  };

  const footerStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '16px 22px',
    borderTop: '0.5px solid var(--hachi-line)',
  };

  return (
    <div style={backdropStyle}>
      <div style={panelStyle} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div style={headerStyle}>
          <HachiMark size={26} />
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 500, fontSize: 18, flex: 1 }}>
            Set up Hachi
          </span>
          <div style={{ display: 'flex', gap: 5 }}>
            {setupDots.map((color, i) => (
              <span
                key={i}
                style={{ width: 7, height: 7, borderRadius: '50%', background: color }}
              />
            ))}
          </div>
          <CloseBtn onClick={closeSetup} />
        </div>

        {/* Body — step-specific content */}
        <div style={bodyStyle}>
          {step === 0 && <Step0 />}
          {step === 1 && <Step1 setupKey={setup.key} keyBorder={keyBorder} keyValid={keyValid} keyInvalid={keyInvalid} onKeyChange={setSetupKey} />}
          {step === 2 && <Step2 currentSafety={setup.safety} onPick={(v) => setSetupField('safety', v)} />}
          {step === 3 && <Step3 google={setup.google} channel={setup.channel} onToggleGoogle={toggleSetupGoogle} onToggleChannel={toggleSetupChannel} />}
          {step === 4 && <Step4 routine={setup.routine} onToggle={toggleSetupRoutine} />}
        </div>

        {/* Footer */}
        <div style={footerStyle}>
          {canBack && (
            <GhostBtn onClick={setupBack}>Back</GhostBtn>
          )}
          <div style={{ flex: 1 }} />
          {skipLabel && (
            <GhostBtn onClick={closeSetup}>{skipLabel}</GhostBtn>
          )}
          <PrimaryBtn onClick={handleNext}>{nextLabel}</PrimaryBtn>
        </div>
      </div>
    </div>
  );
}

// ---- step sub-components ----

function Step0() {
  return (
    <div style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16, padding: '14px 0' }}>
      <HachiMark size={64} />
      <div>
        <div style={{ fontFamily: 'var(--font-display)', fontWeight: 500, fontSize: 24, marginBottom: 6 }}>
          A good dog, on your machine
        </div>
        <div style={{ fontSize: 15, color: 'var(--hachi-muted)', lineHeight: '23px', maxWidth: 400 }}>
          Hachi does long, autonomous work locally — and asks before anything risky. You hold the leash. This takes about three minutes.
        </div>
      </div>
    </div>
  );
}

function Step1({
  setupKey,
  keyBorder,
  keyValid,
  keyInvalid,
  onKeyChange,
}: {
  setupKey: string;
  keyBorder: string;
  keyValid: boolean;
  keyInvalid: boolean;
  onKeyChange: (v: string) => void;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ fontFamily: 'var(--font-display)', fontWeight: 500, fontSize: 18 }}>Connect an AI provider</div>
      <div style={{ fontSize: 13, color: 'var(--hachi-muted)' }}>Hachi uses your own key. It's stored masked and never shown back.</div>
      <div>
        <div style={{ fontSize: 13, color: 'var(--hachi-muted)', marginBottom: 6 }}>Provider</div>
        <div style={{ display: 'flex', alignItems: 'center', height: 44, padding: '0 14px', borderRadius: 10, border: '0.5px solid var(--hachi-line)', background: 'var(--hachi-canvas)', fontSize: 14 }}>
          Anthropic · Claude
        </div>
      </div>
      <div>
        <div style={{ fontSize: 13, color: 'var(--hachi-muted)', marginBottom: 6 }}>API key</div>
        <input
          value={setupKey}
          onChange={(e) => onKeyChange(e.target.value)}
          type="password"
          placeholder="sk-ant-…"
          style={{
            width: '100%',
            height: 44,
            padding: '0 14px',
            borderRadius: 10,
            border: `0.5px solid ${keyBorder}`,
            background: 'var(--hachi-canvas)',
            font: 'inherit',
            fontFamily: 'var(--font-mono)',
            fontSize: 14,
            color: 'var(--hachi-ink)',
            outline: 'none',
            boxSizing: 'border-box',
          }}
        />
        {keyValid && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, color: '#2C7A50', marginTop: 8 }}>
            <Icon name="check" size={15} />
            Key looks good — validated.
          </div>
        )}
        {keyInvalid && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, color: 'var(--hachi-danger)', marginTop: 8 }}>
            <Icon name="alert" size={15} />
            That doesn't look like a valid key yet.
          </div>
        )}
      </div>
    </div>
  );
}

function Step2({
  currentSafety,
  onPick,
}: {
  currentSafety: string;
  onPick: (v: string) => void;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ fontFamily: 'var(--font-display)', fontWeight: 500, fontSize: 18 }}>Choose a safety level</div>
      <div style={{ fontSize: 13, color: 'var(--hachi-muted)' }}>You can change this any time. Hachi always shows what it's doing.</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
        {SAFETY_LEVELS.map((lvl) => {
          const sel = currentSafety === lvl.k;
          return (
            <SafetyOption
              key={lvl.k}
              name={lvl.name}
              desc={lvl.desc}
              selected={sel}
              onClick={() => onPick(lvl.k)}
            />
          );
        })}
      </div>
    </div>
  );
}

function SafetyOption({ name, desc, selected, onClick }: {
  name: string; desc: string; selected: boolean; onClick: () => void;
}) {
  const [hovered, setHovered] = React.useState(false);

  const border = selected ? 'var(--hachi-brand)' : hovered ? '#d3c8b4' : 'var(--hachi-line)';
  const bg     = selected ? 'var(--hachi-brand-tint)' : 'var(--hachi-canvas)';
  const dotBorder = selected ? 'var(--hachi-brand)' : 'var(--hachi-line)';
  const dotBg     = selected ? 'var(--hachi-brand)' : 'transparent';

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        gap: 12,
        alignItems: 'flex-start',
        textAlign: 'left',
        padding: 14,
        borderRadius: 12,
        border: `1px solid ${border}`,
        background: bg,
        cursor: 'pointer',
        font: 'inherit',
        transition: 'border-color var(--dur-fast) var(--ease)',
      }}
    >
      <span style={{
        flexShrink: 0,
        width: 18,
        height: 18,
        borderRadius: '50%',
        border: `2px solid ${dotBorder}`,
        background: dotBg,
        marginTop: 1,
        transition: 'border-color var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease)',
      }} />
      <div>
        <div style={{ fontWeight: 500, fontSize: 14 }}>{name}</div>
        <div style={{ fontSize: 13, color: 'var(--hachi-muted)', marginTop: 2 }}>{desc}</div>
      </div>
    </button>
  );
}

function Step3({
  google,
  channel,
  onToggleGoogle,
  onToggleChannel,
}: {
  google: boolean;
  channel: boolean;
  onToggleGoogle: () => void;
  onToggleChannel: () => void;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ fontFamily: 'var(--font-display)', fontWeight: 500, fontSize: 18 }}>Connect your accounts</div>
      <div style={{ fontSize: 13, color: 'var(--hachi-muted)' }}>
        Optional — you can do this any time in Integrations. Hachi only ever uses what you connect.
      </div>
      <ConnectRow
        icon="mail"
        title="Google"
        sub="Gmail · Calendar · Drive"
        connected={google}
        onToggle={onToggleGoogle}
      />
      <ConnectRow
        icon="hash"
        title="A chat channel"
        sub="Slack or WhatsApp — where Hachi reaches you"
        connected={channel}
        onToggle={onToggleChannel}
      />
    </div>
  );
}

function ConnectRow({
  icon, title, sub, connected, onToggle,
}: {
  icon: string; title: string; sub: string; connected: boolean; onToggle: () => void;
}) {
  const [hovered, setHovered] = React.useState(false);

  const border = connected ? 'var(--hachi-success)' : hovered ? '#d3c8b4' : 'var(--hachi-line)';
  const bg     = connected ? 'var(--hachi-success-tint)' : 'var(--hachi-canvas)';

  return (
    <button
      onClick={onToggle}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 13,
        textAlign: 'left',
        padding: 14,
        borderRadius: 12,
        border: `1px solid ${border}`,
        background: bg,
        cursor: 'pointer',
        font: 'inherit',
        transition: 'border-color var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease)',
      }}
    >
      <div style={{
        width: 40,
        height: 40,
        borderRadius: 10,
        background: 'var(--hachi-cream)',
        display: 'grid',
        placeItems: 'center',
      }}>
        <Icon name={icon} size={20} />
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 500, fontSize: 14 }}>{title}</div>
        <div style={{ fontSize: 13, color: 'var(--hachi-muted)', marginTop: 1 }}>{sub}</div>
      </div>
      {connected && (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#2C7A50', fontWeight: 500 }}>
          <Icon name="check" size={15} />
          connected
        </span>
      )}
    </button>
  );
}

function Step4({
  routine,
  onToggle,
}: {
  routine: boolean;
  onToggle: () => void;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ fontFamily: 'var(--font-display)', fontWeight: 500, fontSize: 18 }}>Your first routine</div>
      <div style={{ fontSize: 13, color: 'var(--hachi-muted)' }}>A suggestion to get value on day one. You can edit or skip it.</div>
      <div style={{
        background: 'var(--hachi-cream)',
        border: '0.5px solid var(--hachi-line)',
        borderRadius: 12,
        padding: 16,
        display: 'flex',
        gap: 13,
        alignItems: 'flex-start',
      }}>
        <div style={{
          width: 40,
          height: 40,
          borderRadius: 10,
          background: 'var(--hachi-surface)',
          display: 'grid',
          placeItems: 'center',
          color: 'var(--hachi-trust)',
        }}>
          <Icon name="calendar" size={20} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 500, fontSize: 15 }}>Morning briefing</div>
          <div style={{ fontSize: 13, color: 'var(--hachi-muted)', marginTop: 3, lineHeight: '20px' }}>
            Every weekday at 8:00, Hachi summarises your inbox and calendar and flags anything urgent. ≈ $0.04 per run.
          </div>
        </div>
      </div>
      <label style={{ display: 'flex', alignItems: 'center', gap: 11, cursor: 'pointer' }}>
        <Switch checked={routine} onChange={onToggle} />
        <span style={{ fontSize: 14 }}>Set up this routine and keep it on watch</span>
      </label>
    </div>
  );
}

// ---- shared small components ----

function CloseBtn({ onClick }: { onClick: () => void }) {
  const [hovered, setHovered] = React.useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        border: 'none',
        background: hovered ? 'var(--hachi-cream)' : 'transparent',
        color: 'var(--hachi-muted)',
        cursor: 'pointer',
        display: 'inline-flex',
        padding: 4,
        borderRadius: 6,
        transition: 'background var(--dur-fast) var(--ease)',
      }}
    >
      <Icon name="x" size={18} />
    </button>
  );
}

function GhostBtn({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  const [hovered, setHovered] = React.useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        height: 40,
        padding: '0 16px',
        borderRadius: 10,
        border: 'none',
        background: hovered ? 'var(--hachi-cream)' : 'transparent',
        color: 'var(--hachi-muted)',
        font: '500 14px var(--font-body)',
        cursor: 'pointer',
        transition: 'background var(--dur-fast) var(--ease)',
      }}
    >
      {children}
    </button>
  );
}

function PrimaryBtn({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  const [hovered, setHovered] = React.useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        height: 40,
        padding: '0 22px',
        borderRadius: 10,
        border: '0.5px solid transparent',
        background: hovered ? 'var(--hachi-brand-hover)' : 'var(--hachi-brand)',
        color: 'var(--hachi-canvas)',
        font: '500 14px var(--font-body)',
        cursor: 'pointer',
        transition: 'background var(--dur-fast) var(--ease)',
      }}
    >
      {children}
    </button>
  );
}
