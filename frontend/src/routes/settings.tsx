// Settings screen — /settings
// Ported from prototype lines 1134–1236 (markup) + 2122–2347 (logic/renderVals).
// All state mutations go through useStore; screen-local hover/expanded in useState.

import { useState, useEffect, useRef } from 'react';
import { createFileRoute, useLocation } from '@tanstack/react-router';
import { useStore } from '../lib/store/store';
import { Icon } from '../components/icons/Icon';
import { Switch } from '../components/ui/Switch';
import type { SafetyLevel } from '../lib/data/types';
import { loadConfig, setSafetyLive, setAskRuleLive, setProviderLive, addFolderLive, addCommandLive, setLimitsLive } from '../lib/data/domains/config';
import { applyProviderKey } from '../lib/data/domains/setup';

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

// LOW-012: settings that require a restart
const RESTART_REQUIRED_SETTINGS = ['safety level', 'provider', 'model'];

// ---- Inline input dialog for adding a folder/command ---------------

interface InlineInputProps {
  placeholder: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

function InlineInput({ placeholder, onConfirm, onCancel }: InlineInputProps) {
  const [value, setValue] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (value.trim()) onConfirm(value.trim());
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      onCancel();
    }
  }

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        style={{
          height: 30,
          padding: '0 10px',
          borderRadius: 8,
          border: '0.5px solid var(--hachi-trust)',
          background: 'var(--hachi-canvas)',
          fontFamily: 'var(--font-mono)',
          fontSize: 12,
          color: 'var(--hachi-ink)',
          outline: 'none',
          width: 160,
        }}
      />
      <button
        type="button"
        onClick={() => { if (value.trim()) onConfirm(value.trim()); }}
        disabled={!value.trim()}
        style={{
          height: 30,
          padding: '0 10px',
          borderRadius: 8,
          border: 'none',
          background: 'var(--hachi-trust)',
          color: '#fff',
          font: '500 12px var(--font-body)',
          cursor: value.trim() ? 'pointer' : 'default',
          opacity: value.trim() ? 1 : 0.5,
        }}
      >
        Add
      </button>
      <button
        type="button"
        onClick={onCancel}
        style={{
          height: 30,
          padding: '0 8px',
          borderRadius: 8,
          border: '0.5px solid var(--hachi-line)',
          background: 'transparent',
          color: 'var(--hachi-muted)',
          font: '500 12px var(--font-body)',
          cursor: 'pointer',
        }}
      >
        Cancel
      </button>
    </span>
  );
}

// ---- Editable spend cap input ----------------------------------------

interface SpendCapInputProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
  onSave: (v: number) => void;
}

function SpendCapInput({ label, value, onChange, onSave }: SpendCapInputProps) {
  const [editing, setEditing] = useState(false);
  const [localVal, setLocalVal] = useState(String(value.toFixed(2)));
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      setLocalVal(String(value.toFixed(2)));
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing, value]);

  function commit() {
    const parsed = parseFloat(localVal);
    if (!isNaN(parsed) && parsed > 0) {
      onChange(parsed);
      onSave(parsed);
    }
    setEditing(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { e.preventDefault(); setEditing(false); }
  }

  return (
    <div style={{ flex: 1 }}>
      <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginBottom: '6px' }}>
        {label}
      </div>
      {editing ? (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 15, color: 'var(--hachi-muted)' }}>$</span>
          <input
            ref={inputRef}
            type="number"
            min="0.01"
            step="0.01"
            value={localVal}
            onChange={(e) => setLocalVal(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={commit}
            style={{
              flex: 1,
              height: '42px',
              padding: '0 14px',
              borderRadius: '10px',
              border: '0.5px solid var(--hachi-trust)',
              background: 'var(--hachi-canvas)',
              fontFamily: 'var(--font-mono)',
              fontSize: '15px',
              color: 'var(--hachi-ink)',
              outline: 'none',
            }}
          />
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          title="Click to edit"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            width: '100%',
            height: '42px',
            padding: '0 14px',
            borderRadius: '10px',
            border: '0.5px solid var(--hachi-line)',
            background: 'var(--hachi-canvas)',
            fontFamily: 'var(--font-mono)',
            fontSize: '15px',
            color: 'var(--hachi-ink)',
            cursor: 'pointer',
            textAlign: 'left',
          }}
          onMouseEnter={(e) => {
            ;(e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--hachi-trust)';
          }}
          onMouseLeave={(e) => {
            ;(e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--hachi-line)';
          }}
        >
          ${value.toFixed(2)}
          <span style={{ color: 'var(--hachi-muted)', fontSize: 12 }}>
            <Icon name="pencil" size={13} />
          </span>
        </button>
      )}
    </div>
  );
}

function Settings() {
  const operator = useStore((s) => s.operator);
  const theme = useStore((s) => s.theme);
  const safety = useStore((s) => s.safety);
  const askRules = useStore((s) => s.askRules);
  const allowedFolders = useStore((s) => s.allowedFolders);
  const allowedCommands = useStore((s) => s.allowedCommands);
  const opTools = useStore((s) => s.opTools);
  const provider = useStore((s) => s.provider);
  const setup = useStore((s) => s.setup);
  const spent = useStore((s) => s.spent);
  const budget = useStore((s) => s.budget);
  const taskCap = useStore((s) => s.taskCap);
  const dailyCap = useStore((s) => s.dailyCap);
  const configLoaded = useStore((s) => s.configLoaded);
  const removeFolder = useStore((s) => s.removeFolder);
  const removeCommand = useStore((s) => s.removeCommand);
  const toggleOpTool = useStore((s) => s.toggleOpTool);
  const toggleTheme = useStore((s) => s.toggleTheme);
  const toggleOperator = useStore((s) => s.toggleOperator);
  const openSetup = useStore((s) => s.openSetup);
  const showToast = useStore((s) => s.showToast);
  const setTaskCap = useStore((s) => s.setTaskCap);
  const setDailyCap = useStore((s) => s.setDailyCap);

  // ---- Inline provider editor state ----
  // When the user clicks "Change provider", open an inline editor instead of the wizard.
  const [editingProvider, setEditingProvider] = useState(false);
  const [editProviderName, setEditProviderName] = useState(provider.name);
  const [editProviderModel, setEditProviderModel] = useState(provider.model);
  const [editProviderKey, setEditProviderKey] = useState('');
  const [providerSaving, setProviderSaving] = useState(false);

  // Keep the inline form in sync if the store's provider changes externally.
  useEffect(() => {
    if (!editingProvider) {
      setEditProviderName(provider.name);
      setEditProviderModel(provider.model);
    }
  }, [provider.name, provider.model, editingProvider]);

  // Load config from gateway on mount (idempotent — also runs on app boot via live.ts)
  useEffect(() => { void loadConfig(); }, []);

  // Local hover state for folder/command chips
  const [hoveredFolder, setHoveredFolder] = useState<string | null>(null);
  const [hoveredCommand, setHoveredCommand] = useState<string | null>(null);
  // Track whether any settings change has been made (unsaved indicator)
  const [unsaved, setUnsaved] = useState(false);
  // MED-016: inline add-folder / add-command dialogs
  const [addingFolder, setAddingFolder] = useState(false);
  const [addingCommand, setAddingCommand] = useState(false);
  // LOW-012: track which settings were changed to name them in the restart notice
  const [restartSettings, setRestartSettings] = useState<string[]>([]);

  // MED-017: clear unsaved indicator when navigating away
  const location = useLocation();
  const locationRef = useRef(location.pathname);
  useEffect(() => {
    if (location.pathname !== locationRef.current) {
      locationRef.current = location.pathname;
      setUnsaved(false);
      setRestartSettings([]);
    }
  }, [location.pathname]);

  function markUnsaved(requiresRestart?: string) {
    setUnsaved(true);
    if (requiresRestart) {
      setRestartSettings((prev) =>
        prev.includes(requiresRestart) ? prev : [...prev, requiresRestart]
      );
    }
  }

  async function handleSave() {
    // MED-017: persist caps to gateway then clear unsaved indicator on success
    try {
      await setLimitsLive(taskCap, dailyCap);
      setUnsaved(false);
      setRestartSettings([]);
      showToast('Settings saved', 'success');
    } catch {
      // Keep unsaved=true on failure so user knows save failed
      showToast('Save failed — please try again', 'blocked');
    }
  }

  async function handleSaveProvider() {
    setProviderSaving(true);
    try {
      const patch: { name?: string; model?: string } = {};
      if (editProviderName.trim()) patch.name = editProviderName.trim();
      if (editProviderModel.trim()) patch.model = editProviderModel.trim();
      await setProviderLive(patch);
      // If a new API key was typed, send it via setup/apply (provider step only).
      // setProviderLive handles name+model; key storage goes through the wizard path.
      if (editProviderKey.trim()) {
        // Persist just the provider key via the setup domain helper (provider step only).
        await applyProviderKey({
          kind: patch.name ?? provider.name,
          model: patch.model ?? provider.model,
          apiKey: editProviderKey.trim(),
        });
      }
      setEditingProvider(false);
      setEditProviderKey('');
      markUnsaved('provider');
      showToast('Provider updated — applies on next restart', 'success');
    } catch {
      showToast('Could not save provider — please try again', 'blocked');
    } finally {
      setProviderSaving(false);
    }
  }

  // Wrap actions to set unsaved flag
  function handleSetSafety(v: SafetyLevel) {
    void setSafetyLive(v);
    markUnsaved('safety level');
  }
  function handleToggleAskRule(id: string) {
    // Persist via the gateway (optimistic). The current value is flipped.
    const current = askRules.find((r) => r.id === id);
    void setAskRuleLive(id, !(current?.on ?? true));
    markUnsaved();
  }
  function handleRemoveFolder(f: string) {
    removeFolder(f);
    markUnsaved();
  }
  function handleAddFolder(path: string) {
    setAddingFolder(false);
    markUnsaved();
    void addFolderLive(path);
  }
  function handleRemoveCommand(c: string) {
    removeCommand(c);
    markUnsaved();
  }
  function handleAddCommand(cmd: string) {
    setAddingCommand(false);
    markUnsaved();
    void addCommandLive(cmd);
  }
  function handleToggleOpTool(id: string) {
    toggleOpTool(id);
    markUnsaved();
  }
  function handleSetTaskCap(v: number) {
    setTaskCap(v);
    markUnsaved();
  }
  function handleSetDailyCap(v: number) {
    setDailyCap(v);
    markUnsaved();
  }

  // Budget display (prototype renderVals lines 2185)
  const spentStr = '$' + Number(spent).toFixed(2);
  const pct = Math.min(100, Math.round((spent / budget) * 100));

  // Theme icon (sun = light → switching to dark; moon = dark → switching to light)
  const systemIsDark = typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const isDark = theme === 'dark' || (theme === 'auto' && systemIsDark);
  const themeIcon = isDark ? 'moon' : 'sun';

  // Provider display — name/model come from live gateway config. The API key is
  // never surfaced to the browser; we derive its display from the env boolean
  // returned by GET /setup (keyAlreadySet) — never from the key value itself.
  const providerLabel = [provider.name, provider.model].filter(Boolean).join(' · ');
  const keyDisplay = setup.keyAlreadySet ? '•••••••• (configured)' : 'not set';

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
        className="hachi-settings-header"
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
        <div style={{ flex: 1 }} />
        {/* MED-017: save button clears unsaved indicator on success */}
        {unsaved && (
          <button
            type="button"
            onClick={() => void handleSave()}
            style={{
              height: 36,
              padding: '0 16px',
              borderRadius: 10,
              border: 'none',
              background: 'var(--hachi-brand-hover)',
              color: 'var(--hachi-canvas)',
              font: '500 13px var(--font-body)',
              cursor: 'pointer',
            }}
            onMouseEnter={(e) => {
              ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-brand-active)';
            }}
            onMouseLeave={(e) => {
              ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-brand-hover)';
            }}
          >
            Save
          </button>
        )}
      </div>

      {/* Scrollable body */}
      <div
        className="hachi-scroll hachi-settings-body"
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

          {/* MED-015: operator mode toggle — visible in Settings */}
          <div style={cardStyle}>
            <h2 style={{ ...sectionHeadingStyle, margin: '0 0 16px' }}>
              Mode
            </h2>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
                padding: '11px 0',
              }}
            >
              <span style={{ color: 'var(--hachi-trust)', display: 'inline-flex' }}>
                <Icon name="cpu" size={17} />
              </span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: '14px', fontWeight: 500 }}>Operator mode</div>
                <div style={{ fontSize: '13px', color: 'var(--hachi-muted)', marginTop: 2 }}>
                  Shows tool signatures, expanded cards, and advanced controls. For power users and integrators.
                </div>
              </div>
              <Switch checked={operator} onChange={toggleOperator} />
            </div>
          </div>

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

            {!configLoaded ? (
              /* Gate render until config is loaded — prevents "Standard" flash before real value arrives */
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '13px',
                color: 'var(--hachi-muted)',
                padding: '14px',
                borderRadius: '12px',
                border: '0.5px solid var(--hachi-line)',
                background: 'var(--hachi-canvas)',
              }}>
                <Icon name="clock" size={14} />
                Loading…
              </div>
            ) : (
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
            )}

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

            {/* LOW-012: specific restart notice */}
            {restartSettings.length > 0 ? (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  fontSize: '12px',
                  color: 'var(--hachi-working)',
                  marginTop: '14px',
                }}
              >
                <Icon name="clock" size={13} />
                {restartSettings.length === 1
                  ? `${restartSettings[0]} changes apply next time Hachi starts.`
                  : `${restartSettings.slice(0, -1).join(', ')} and ${restartSettings[restartSettings.length - 1]} changes apply next time Hachi starts.`}
              </div>
            ) : (
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
            )}
          </div>

          {/* ---- Ask before… ---- */}
          {/* Persisted to settings.toml via PUT /config/permissions (ask_before).
              Toggles survive a reload — loaded by loadConfig(), saved by
              setAskRuleLive() in domains/config.ts. */}
          <div style={cardStyle}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', marginBottom: '16px' }}>
              <h2 style={sectionHeadingStyle}>
                Ask before…
              </h2>
            </div>
            {askRules.length === 0 ? (
              <div style={{ fontSize: '13px', color: 'var(--hachi-muted)' }}>
                No rules loaded.
              </div>
            ) : (
              askRules.map((rule, idx) => (
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
              ))
            )}
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
                {/* MED-016: inline folder input dialog */}
                {addingFolder ? (
                  <InlineInput
                    placeholder="~/path/to/folder"
                    onConfirm={handleAddFolder}
                    onCancel={() => setAddingFolder(false)}
                  />
                ) : (
                  <button
                    onClick={() => setAddingFolder(true)}
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
                )}
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
                {/* MED-016: inline command input dialog */}
                {addingCommand ? (
                  <InlineInput
                    placeholder="command name"
                    onConfirm={handleAddCommand}
                    onCancel={() => setAddingCommand(false)}
                  />
                ) : (
                  <button
                    onClick={() => setAddingCommand(true)}
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
                )}
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

            {/* MED-018: editable spend cap inputs */}
            <div className="hachi-spend-cap-row" style={{ display: 'flex', gap: '14px', marginBottom: '18px' }}>
              <SpendCapInput
                label="Per-task cap"
                value={taskCap}
                onChange={handleSetTaskCap}
                onSave={handleSetTaskCap}
              />
              <SpendCapInput
                label="Daily cap"
                value={dailyCap}
                onChange={handleSetDailyCap}
                onSave={handleSetDailyCap}
              />
            </div>

            {/* Spend bar */}
            <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginBottom: '8px' }}>
              Today, against ${dailyCap.toFixed(2)}
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

            {editingProvider ? (
              /* Inline provider editor — no wizard; calls PUT /config/provider directly */
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div>
                  <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginBottom: '6px' }}>Provider</div>
                  <input
                    type="text"
                    value={editProviderName}
                    onChange={(e) => setEditProviderName(e.target.value)}
                    placeholder="anthropic"
                    style={{
                      width: '100%',
                      height: '40px',
                      padding: '0 12px',
                      borderRadius: '10px',
                      border: '0.5px solid var(--hachi-line)',
                      background: 'var(--hachi-canvas)',
                      font: '14px var(--font-mono)',
                      color: 'var(--hachi-ink)',
                      outline: 'none',
                      boxSizing: 'border-box',
                    }}
                  />
                </div>
                <div>
                  <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginBottom: '6px' }}>Model</div>
                  <input
                    type="text"
                    value={editProviderModel}
                    onChange={(e) => setEditProviderModel(e.target.value)}
                    placeholder="claude-haiku-4-5"
                    style={{
                      width: '100%',
                      height: '40px',
                      padding: '0 12px',
                      borderRadius: '10px',
                      border: '0.5px solid var(--hachi-line)',
                      background: 'var(--hachi-canvas)',
                      font: '14px var(--font-mono)',
                      color: 'var(--hachi-ink)',
                      outline: 'none',
                      boxSizing: 'border-box',
                    }}
                  />
                </div>
                <div>
                  <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginBottom: '6px' }}>API key</div>
                  <input
                    type="password"
                    value={editProviderKey}
                    onChange={(e) => setEditProviderKey(e.target.value)}
                    placeholder="Leave blank to keep existing key"
                    style={{
                      width: '100%',
                      height: '40px',
                      padding: '0 12px',
                      borderRadius: '10px',
                      border: '0.5px solid var(--hachi-line)',
                      background: 'var(--hachi-canvas)',
                      font: '14px var(--font-mono)',
                      color: 'var(--hachi-ink)',
                      outline: 'none',
                      boxSizing: 'border-box',
                    }}
                  />
                  <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginTop: '5px' }}>
                    Your key is stored securely and never shown. Leave blank to keep the current key.
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                  <ProviderButton onClick={() => void handleSaveProvider()}>
                    {providerSaving ? 'Saving…' : 'Save'}
                  </ProviderButton>
                  <button
                    type="button"
                    onClick={() => { setEditingProvider(false); setEditProviderKey(''); }}
                    style={{
                      height: '36px',
                      padding: '0 14px',
                      borderRadius: '10px',
                      border: '0.5px solid var(--hachi-line)',
                      background: 'transparent',
                      color: 'var(--hachi-muted)',
                      font: `500 13px var(--font-body)`,
                      cursor: 'pointer',
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              /* Read-only view */
              <div>
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
                <ProviderButton onClick={() => setEditingProvider(true)}>Change provider</ProviderButton>
              </div>
            )}
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

// ---- Small local button components ----

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
