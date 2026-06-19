// Settings screen — /settings
// Ported from prototype lines 1134–1236 (markup) + 2122–2347 (logic/renderVals).
// All state mutations go through useStore; screen-local hover/expanded in useState.

import { useState, useEffect } from 'react';
import { createFileRoute } from '@tanstack/react-router';
import { useStore } from '../lib/store/store';
import { Icon } from '../components/icons/Icon';
import { Switch } from '../components/ui/Switch';
import type { SafetyLevel } from '../lib/data/types';
import { loadConfig, setSafetyLive, setProviderLive } from '../lib/data/domains/config';

// ---- Safety level definitions (mirror prototype renderVals lines 2337–2342) ----
const SAFETY_LEVELS: { k: SafetyLevel; name: string; desc: string }[] = [
  {
    k: 'guarded',
    name: 'Guarded',
    desc: 'Hachi asks before almost everything. Best while you\'re getting to know it.',
  },
  {
    k: 'standard',
    name: 'Standard',
    desc: 'Hachi handles routine, safe work on its own and asks before anything that leaves your machine or deletes things.',
  },
  {
    k: 'open',
    name: 'Open',
    desc: 'Hachi acts without asking. For trusted, sandboxed work only.',
  },
];

function Settings() {
  const {
    // state
    operator,
    theme,
    safety,
    askRules,
    allowedFolders,
    allowedCommands,
    opTools,
    provider,
    spent,
    budget,
    // actions (setSafety is called indirectly via setSafetyLive)
    toggleAskRule,
    removeFolder,
    removeCommand,
    addFolder,
    addCommand,
    toggleOpTool,
    toggleTheme,
    openSetup,
    showToast,
  } = useStore();

  // Load config from gateway on mount (idempotent — also runs on app boot via live.ts)
  useEffect(() => { void loadConfig(); }, []);

  // Local hover state for folder/command chips
  const [hoveredFolder, setHoveredFolder] = useState<string | null>(null);
  const [hoveredCommand, setHoveredCommand] = useState<string | null>(null);
  // Track whether any settings change has been made (unsaved indicator)
  const [unsaved, setUnsaved] = useState(false);

  // Wrap actions to set unsaved flag
  function handleSetSafety(v: SafetyLevel) {
    // setSafetyLive optimistically calls setSafety then persists to the gateway
    void setSafetyLive(v);
    setUnsaved(true);
  }
  function handleToggleAskRule(id: string) {
    toggleAskRule(id);
    setUnsaved(true);
  }
  function handleRemoveFolder(f: string) {
    removeFolder(f);
    setUnsaved(true);
  }
  function handleAddFolder() {
    addFolder();
    setUnsaved(true);
  }
  function handleRemoveCommand(c: string) {
    removeCommand(c);
    setUnsaved(true);
  }
  function handleAddCommand() {
    addCommand();
    setUnsaved(true);
  }
  function handleToggleOpTool(id: string) {
    toggleOpTool(id);
    setUnsaved(true);
  }

  // Budget display (prototype renderVals lines 2185)
  const spentStr = '$' + Number(spent).toFixed(2);
  const budgetStr = '$' + Number(budget).toFixed(2);
  const pct = Math.min(100, Math.round((spent / budget) * 100));

  // Theme icon (sun = light → switching to dark; moon = dark → switching to light)
  const isDark = theme === 'dark';
  const themeIcon = isDark ? 'moon' : 'sun';

  // Provider display — name/model come from live gateway config. The API key is
  // never surfaced to the browser, so show a neutral hidden state rather than a
  // fabricated last-4 or an unverified "validated" claim. To change the key, the
  // user re-runs the wizard via "Change provider".
  const providerLabel = [provider.name, provider.model].filter(Boolean).join(' · ');
  const keyDisplay = 'stored securely · hidden';

  // Card container style (shared)
  const cardStyle: React.CSSProperties = {
    background: 'var(--hachi-surface)',
    border: '0.5px solid var(--hachi-line)',
    borderRadius: '16px',
    padding: '22px',
  };

  const sectionHeadingStyle: React.CSSProperties = {
    margin: 0,
    fontFamily: 'var(--font-display)',
    fontWeight: 500,
    fontSize: '17px',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '16px',
          padding: '0 32px',
          height: '68px',
          flexShrink: 0,
          borderBottom: '0.5px solid var(--hachi-line)',
        }}
      >
        <h1
          style={{
            margin: 0,
            fontFamily: 'var(--font-display)',
            fontWeight: 500,
            fontSize: '22px',
            lineHeight: '26px',
          }}
        >
          Settings
        </h1>
        {unsaved && (
          <span style={{ fontSize: '13px', color: 'var(--hachi-working)' }}>
            unsaved changes
          </span>
        )}
      </div>

      {/* Scrollable body */}
      <div
        className="hachi-scroll"
        style={{ flex: 1, overflow: 'auto', padding: '28px 32px 64px' }}
      >
        <div
          style={{
            maxWidth: '720px',
            margin: '0 auto',
            display: 'flex',
            flexDirection: 'column',
            gap: '24px',
          }}
        >

          {/* ---- Leash / Safety ---- */}
          <div style={cardStyle}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '9px',
                marginBottom: '6px',
              }}
            >
              <span style={{ color: 'var(--hachi-brand)', display: 'inline-flex' }}>
                <Icon name="leash" size={18} />
              </span>
              <h2 style={sectionHeadingStyle}>
                Permissions &amp; safety — the leash
              </h2>
            </div>
            <div style={{ fontSize: '13px', color: 'var(--hachi-muted)', marginBottom: '16px' }}>
              How much Hachi can do before it asks you.
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '9px' }}>
              {SAFETY_LEVELS.map((lvl) => {
                const sel = safety === lvl.k;
                return (
                  <button
                    key={lvl.k}
                    onClick={() => handleSetSafety(lvl.k)}
                    style={{
                      display: 'flex',
                      gap: '13px',
                      alignItems: 'flex-start',
                      textAlign: 'left',
                      padding: '14px',
                      borderRadius: '12px',
                      border: `1px solid ${sel ? 'var(--hachi-brand)' : 'var(--hachi-line)'}`,
                      background: sel ? 'var(--hachi-brand-tint)' : 'var(--hachi-canvas)',
                      cursor: 'pointer',
                      font: 'inherit',
                    }}
                  >
                    <span
                      style={{
                        flexShrink: 0,
                        width: '18px',
                        height: '18px',
                        borderRadius: '50%',
                        border: `2px solid ${sel ? 'var(--hachi-brand)' : 'var(--hachi-line)'}`,
                        background: sel ? 'var(--hachi-brand)' : 'transparent',
                        marginTop: '2px',
                      }}
                    />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 500, fontSize: '14px' }}>{lvl.name}</div>
                      <div style={{ fontSize: '13px', color: 'var(--hachi-muted)', marginTop: '2px' }}>
                        {lvl.desc}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>

            {/* Open warning */}
            {safety === 'open' && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  fontSize: '13px',
                  color: 'var(--hachi-danger)',
                  background: 'var(--hachi-danger-tint)',
                  padding: '10px 12px',
                  borderRadius: '10px',
                  marginTop: '12px',
                }}
              >
                <Icon name="alert" size={15} />
                Open lets Hachi act without asking. Reserve it for trusted, sandboxed work.
              </div>
            )}

            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '12px',
                color: 'var(--hachi-muted)',
                marginTop: '14px',
              }}
            >
              <Icon name="clock" size={13} />
              Some changes apply next time Hachi starts.
            </div>
          </div>

          {/* ---- Ask before… ---- */}
          <div style={cardStyle}>
            <h2 style={{ ...sectionHeadingStyle, margin: '0 0 16px' }}>
              Ask before…
            </h2>
            {askRules.map((rule, idx) => (
              <div
                key={rule.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '14px',
                  padding: '11px 0',
                  borderBottom: idx < askRules.length - 1 ? '0.5px solid var(--hachi-line)' : 'none',
                }}
              >
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '14px' }}>{rule.label}</div>
                </div>
                <Switch
                  checked={rule.on}
                  onChange={() => handleToggleAskRule(rule.id)}
                />
              </div>
            ))}
          </div>

          {/* ---- Operator: folders, commands & tools ---- */}
          {operator && (
            <div style={cardStyle}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '9px',
                  marginBottom: '6px',
                }}
              >
                <span style={{ color: 'var(--hachi-trust)', display: 'inline-flex' }}>
                  <Icon name="cpu" size={18} />
                </span>
                <h2 style={sectionHeadingStyle}>
                  Folders, commands &amp; tools
                </h2>
                <span
                  style={{
                    fontSize: '11px',
                    fontWeight: 500,
                    color: 'var(--hachi-trust)',
                    background: 'var(--hachi-trust-tint)',
                    padding: '3px 9px',
                    borderRadius: '999px',
                  }}
                >
                  operator
                </span>
              </div>
              <div style={{ fontSize: '13px', color: 'var(--hachi-muted)', marginBottom: '16px' }}>
                The exact reach you've granted Hachi. Narrow it any time.
              </div>

              {/* Folders */}
              <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginBottom: '8px' }}>
                Folders Hachi may use
              </div>
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: '8px',
                  marginBottom: '18px',
                }}
              >
                {allowedFolders.map((folder) => (
                  <span
                    key={folder}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '7px',
                      padding: '6px 10px',
                      borderRadius: '8px',
                      background: 'var(--hachi-cream)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: '12px',
                      color: 'var(--hachi-ink)',
                    }}
                  >
                    {folder}
                    <button
                      onClick={() => handleRemoveFolder(folder)}
                      onMouseEnter={() => setHoveredFolder(folder)}
                      onMouseLeave={() => setHoveredFolder(null)}
                      style={{
                        border: 'none',
                        background: 'transparent',
                        color: hoveredFolder === folder ? 'var(--hachi-danger)' : 'var(--hachi-muted)',
                        cursor: 'pointer',
                        display: 'inline-flex',
                        padding: 0,
                        transition: 'color var(--dur-fast) var(--ease)',
                      }}
                      aria-label={`Remove ${folder}`}
                    >
                      <Icon name="minus" size={13} />
                    </button>
                  </span>
                ))}
                <button
                  onClick={handleAddFolder}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '5px',
                    padding: '6px 10px',
                    borderRadius: '8px',
                    border: '0.5px dashed var(--hachi-line)',
                    background: 'transparent',
                    color: 'var(--hachi-trust)',
                    font: `500 12px var(--font-body)`,
                    cursor: 'pointer',
                  }}
                >
                  <Icon name="plus" size={13} />
                  folder
                </button>
              </div>

              {/* Commands */}
              <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginBottom: '8px' }}>
                Commands allowed
              </div>
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: '8px',
                  marginBottom: '18px',
                }}
              >
                {allowedCommands.map((cmd) => (
                  <span
                    key={cmd}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '7px',
                      padding: '6px 10px',
                      borderRadius: '8px',
                      background: 'var(--hachi-cream)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: '12px',
                      color: 'var(--hachi-ink)',
                    }}
                  >
                    {cmd}
                    <button
                      onClick={() => handleRemoveCommand(cmd)}
                      onMouseEnter={() => setHoveredCommand(cmd)}
                      onMouseLeave={() => setHoveredCommand(null)}
                      style={{
                        border: 'none',
                        background: 'transparent',
                        color: hoveredCommand === cmd ? 'var(--hachi-danger)' : 'var(--hachi-muted)',
                        cursor: 'pointer',
                        display: 'inline-flex',
                        padding: 0,
                        transition: 'color var(--dur-fast) var(--ease)',
                      }}
                      aria-label={`Remove ${cmd}`}
                    >
                      <Icon name="minus" size={13} />
                    </button>
                  </span>
                ))}
                <button
                  onClick={handleAddCommand}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '5px',
                    padding: '6px 10px',
                    borderRadius: '8px',
                    border: '0.5px dashed var(--hachi-line)',
                    background: 'transparent',
                    color: 'var(--hachi-trust)',
                    font: `500 12px var(--font-body)`,
                    cursor: 'pointer',
                  }}
                >
                  <Icon name="plus" size={13} />
                  command
                </button>
              </div>

              {/* Op tools */}
              <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginBottom: '4px' }}>
                Tools
              </div>
              {opTools.map((tool, idx) => (
                <div
                  key={tool.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '14px',
                    padding: '11px 0',
                    borderBottom: idx < opTools.length - 1 ? '0.5px solid var(--hachi-line)' : 'none',
                  }}
                >
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: '14px' }}>{tool.label}</div>
                    <div
                      style={{
                        fontSize: '12px',
                        color: 'var(--hachi-muted)',
                        fontFamily: 'var(--font-mono)',
                        marginTop: '1px',
                      }}
                    >
                      {tool.scope}
                    </div>
                  </div>
                  <Switch
                    checked={tool.on}
                    onChange={() => handleToggleOpTool(tool.id)}
                  />
                </div>
              ))}
            </div>
          )}

          {/* ---- Spend & limits ---- */}
          <div style={cardStyle}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '9px',
                marginBottom: '16px',
              }}
            >
              <span style={{ color: 'var(--hachi-ink)', display: 'inline-flex' }}>
                <Icon name="dollar" size={18} />
              </span>
              <h2 style={sectionHeadingStyle}>Spend &amp; limits</h2>
            </div>

            {/* Cap display fields */}
            <div style={{ display: 'flex', gap: '14px', marginBottom: '18px' }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginBottom: '6px' }}>
                  Per-task cap
                </div>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    height: '42px',
                    padding: '0 14px',
                    borderRadius: '10px',
                    border: '0.5px solid var(--hachi-line)',
                    background: 'var(--hachi-canvas)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: '15px',
                  }}
                >
                  $1.00
                </div>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginBottom: '6px' }}>
                  Daily cap
                </div>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    height: '42px',
                    padding: '0 14px',
                    borderRadius: '10px',
                    border: '0.5px solid var(--hachi-line)',
                    background: 'var(--hachi-canvas)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: '15px',
                  }}
                >
                  {budgetStr}
                </div>
              </div>
            </div>

            {/* Spend bar */}
            <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginBottom: '8px' }}>
              Today, against {budgetStr}
            </div>
            <div
              style={{
                height: '6px',
                borderRadius: '999px',
                background: 'var(--hachi-line)',
                overflow: 'hidden',
                marginBottom: '8px',
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: '100%',
                  background: 'var(--hachi-brand)',
                  borderRadius: '999px',
                  transition: 'width var(--dur) var(--ease)',
                }}
              />
            </div>
            <div style={{ fontSize: '13px', color: 'var(--hachi-muted)' }}>
              What's costing money: model calls {spentStr} · tools $0.00
            </div>
          </div>

          {/* ---- AI provider & model ---- */}
          <div style={cardStyle}>
            <h2 style={{ ...sectionHeadingStyle, margin: '0 0 16px' }}>
              AI provider &amp; model
            </h2>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
                marginBottom: '14px',
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: '14px', fontWeight: 500 }}>
                  {providerLabel}
                </div>
                <div style={{ fontSize: '13px', color: 'var(--hachi-muted)', marginTop: '2px' }}>
                  API key {keyDisplay}
                </div>
              </div>
            </div>
            <ProviderButton onClick={openSetup}>Change provider</ProviderButton>
          </div>

          {/* ---- Appearance & account ---- */}
          <div style={cardStyle}>
            <h2 style={{ ...sectionHeadingStyle, margin: '0 0 16px' }}>
              Appearance &amp; account
            </h2>

            {/* Dark mode toggle */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
                padding: '11px 0',
                borderBottom: '0.5px solid var(--hachi-line)',
              }}
            >
              <span style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}>
                <Icon name={themeIcon} size={17} />
              </span>
              <div style={{ flex: 1, fontSize: '14px' }}>Dark mode</div>
              <Switch checked={isDark} onChange={() => toggleTheme()} />
            </div>

            {/* Re-run setup */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
                padding: '11px 0',
              }}
            >
              <span style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}>
                <Icon name="sparkles" size={17} />
              </span>
              <div style={{ flex: 1, fontSize: '14px' }}>Re-run the setup wizard</div>
              <SetupButton onClick={openSetup}>Run setup</SetupButton>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}

// ---- Small local button components (avoid importing Button which is Torii red primary) ----

function ProviderButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        height: '36px',
        padding: '0 14px',
        borderRadius: '10px',
        border: '1px solid var(--hachi-trust)',
        background: hovered ? 'var(--hachi-trust-tint)' : 'transparent',
        color: 'var(--hachi-trust)',
        font: `500 13px var(--font-body)`,
        cursor: 'pointer',
        transition: 'background var(--dur-fast) var(--ease)',
      }}
    >
      {children}
    </button>
  );
}

function SetupButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        height: '34px',
        padding: '0 14px',
        borderRadius: '10px',
        border: '0.5px solid var(--hachi-line)',
        background: hovered ? 'var(--hachi-cream)' : 'var(--hachi-canvas)',
        color: 'var(--hachi-ink)',
        font: `500 13px var(--font-body)`,
        cursor: 'pointer',
        transition: 'background var(--dur-fast) var(--ease)',
      }}
    >
      {children}
    </button>
  );
}

export const Route = createFileRoute('/settings')({
  component: Settings,
});
