// AddProviderDialog — guided add/edit flow for an AI provider.
//
// Add mode: pick a service from a tile grid, then only the fields it needs are
// shown (API key + model for known providers; base URL only for Ollama/compatible).
// Allowed-models and custom headers live under a collapsed "Advanced" section.
// Edit mode: kind + name are locked; the same fields are prefilled.
//
// Submit delegates to the parent via onSave(name, spec) — same contract the
// previous inline form used — so list refresh / toast stay in ProviderSettings.

import React, { useState, useRef } from 'react';
import { Icon } from '../icons/Icon';
import { useFocusTrap } from '../../lib/hooks/useFocusTrap';
import type { ProviderSpec } from '../../lib/data/domains/providers';
import { inputStyle, labelStyle } from './shared';

interface ProviderKind {
  value: string;
  label: string;
  monogram: string;
  needsBase: boolean;
  needsKey: boolean;
  headers: boolean;
  modelPlaceholder: string;
  baseUrlPlaceholder: string;
}

export const PROVIDER_KINDS: ProviderKind[] = [
  { value: 'anthropic', label: 'Anthropic', monogram: 'A', needsBase: false, needsKey: true, headers: false, modelPlaceholder: 'claude-haiku-4-5', baseUrlPlaceholder: '' },
  { value: 'openai-compatible', label: 'OpenAI / compatible', monogram: 'AI', needsBase: true, needsKey: true, headers: true, modelPlaceholder: 'gpt-4o-mini', baseUrlPlaceholder: 'https://api.openai.com/v1' },
  { value: 'openrouter', label: 'OpenRouter', monogram: 'OR', needsBase: false, needsKey: true, headers: true, modelPlaceholder: 'anthropic/claude-3.5-sonnet', baseUrlPlaceholder: '' },
  { value: 'groq', label: 'Groq', monogram: 'G', needsBase: false, needsKey: true, headers: true, modelPlaceholder: 'llama-3.1-70b-versatile', baseUrlPlaceholder: '' },
  { value: 'together', label: 'Together AI', monogram: 'T', needsBase: false, needsKey: true, headers: true, modelPlaceholder: 'meta-llama/Llama-3-70b', baseUrlPlaceholder: '' },
  { value: 'deepseek', label: 'DeepSeek', monogram: 'DS', needsBase: false, needsKey: true, headers: true, modelPlaceholder: 'deepseek-chat', baseUrlPlaceholder: '' },
  { value: 'ollama', label: 'Ollama (local)', monogram: 'OL', needsBase: true, needsKey: false, headers: false, modelPlaceholder: 'llama3.1:8b', baseUrlPlaceholder: 'http://localhost:11434' },
];

function kindOf(value: string): ProviderKind {
  return PROVIDER_KINDS.find((k) => k.value === value) ?? PROVIDER_KINDS[0];
}

interface HeaderRow {
  id: string;
  key: string;
  value: string;
}

export interface ProviderFormInitial {
  name?: string;
  kind?: string;
  model?: string;
  base_url?: string;
  allowed_models_raw?: string;
  headers?: HeaderRow[];
}

export function AddProviderDialog({
  isEditing,
  initial,
  onSave,
  onClose,
}: {
  isEditing: boolean;
  initial?: ProviderFormInitial;
  onSave: (name: string, spec: ProviderSpec) => Promise<void>;
  onClose: () => void;
}) {
  const trapRef = useFocusTrap<HTMLDivElement>(true);
  const headerIdRef = useRef(0);

  const [name, setName] = useState(initial?.name ?? 'anthropic');
  const [kind, setKind] = useState(initial?.kind ?? 'anthropic');
  const [model, setModel] = useState(initial?.model ?? '');
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? '');
  const [apiKey, setApiKey] = useState('');
  const [allowedRaw, setAllowedRaw] = useState(initial?.allowed_models_raw ?? '');
  const [headers, setHeaders] = useState<HeaderRow[]>(initial?.headers ?? []);
  const [nameAuto, setNameAuto] = useState(!isEditing); // name tracks kind until user edits it
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const meta = kindOf(kind);

  function selectKind(value: string) {
    setKind(value);
    if (nameAuto) setName(value);
    setError('');
  }

  function addHeader() {
    headerIdRef.current += 1;
    setHeaders((h) => [...h, { id: String(headerIdRef.current), key: '', value: '' }]);
  }
  function setHeader(id: string, field: 'key' | 'value', val: string) {
    setHeaders((h) => h.map((r) => (r.id === id ? { ...r, [field]: val } : r)));
  }
  function removeHeader(id: string) {
    setHeaders((h) => h.filter((r) => r.id !== id));
  }

  async function handleSubmit() {
    if (!name.trim()) return setError('Give this connection a name.');
    if (!model.trim()) return setError('Enter the default model to use.');
    if (meta.needsBase && !baseUrl.trim()) return setError('This provider needs a base URL.');

    const headerMap: Record<string, string> = {};
    for (const row of headers) if (row.key.trim()) headerMap[row.key.trim()] = row.value;

    const spec: ProviderSpec = {
      kind,
      model: model.trim(),
      base_url: baseUrl.trim() || null,
      allowed_models: allowedRaw.split(',').map((s) => s.trim()).filter(Boolean),
      headers: headerMap,
      api_key: apiKey || undefined,
    };

    setSaving(true);
    setError('');
    try {
      await onSave(name.trim(), spec);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not save this provider.');
    } finally {
      setSaving(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onClose();
    }
  }

  const backdropStyle: React.CSSProperties = {
    position: 'fixed',
    inset: 0,
    background: 'rgba(28,26,32,.4)',
    display: 'grid',
    placeItems: 'center',
    padding: 24,
    zIndex: 60,
  };
  const panelStyle: React.CSSProperties = {
    width: 500,
    maxWidth: '96vw',
    maxHeight: '88vh',
    background: 'var(--hachi-surface)',
    border: '0.5px solid var(--hachi-line)',
    borderRadius: 18,
    overflow: 'hidden',
    boxShadow: '0 24px 60px rgba(28,26,32,.24)',
    display: 'flex',
    flexDirection: 'column',
  };

  return (
    <div style={backdropStyle} onClick={onClose}>
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-provider-title"
        style={panelStyle}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '20px 22px 16px',
            borderBottom: '0.5px solid var(--hachi-line)',
            flexShrink: 0,
          }}
        >
          <div>
            <h2
              id="add-provider-title"
              style={{ margin: 0, fontFamily: 'var(--font-display)', fontWeight: 500, fontSize: 18 }}
            >
              {isEditing ? `Edit ${name}` : 'Add a provider'}
            </h2>
            {!isEditing && (
              <div style={{ fontSize: 12, color: 'var(--hachi-muted)', marginTop: 2 }}>
                Pick a service — Hachi only asks for what it needs.
              </div>
            )}
          </div>
          <div style={{ flex: 1 }} />
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            style={{
              border: 'none',
              background: 'transparent',
              color: 'var(--hachi-muted)',
              cursor: 'pointer',
              display: 'inline-flex',
              padding: 6,
              borderRadius: 8,
            }}
          >
            <Icon name="x" size={16} />
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '18px 22px' }}>
          {!isEditing && (
            <div style={{ marginBottom: 18 }}>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(4, 1fr)',
                  gap: 8,
                }}
              >
                {PROVIDER_KINDS.map((pk) => {
                  const sel = pk.value === kind;
                  return (
                    <button
                      key={pk.value}
                      type="button"
                      onClick={() => selectKind(pk.value)}
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        gap: 6,
                        padding: '10px 4px',
                        borderRadius: 10,
                        border: `1px solid ${sel ? 'var(--hachi-brand)' : 'var(--hachi-line)'}`,
                        background: sel ? 'var(--hachi-brand-tint)' : 'var(--hachi-canvas)',
                        cursor: 'pointer',
                      }}
                    >
                      <span
                        style={{
                          width: 24,
                          height: 24,
                          borderRadius: 6,
                          background: sel ? 'var(--hachi-brand)' : 'var(--hachi-cream)',
                          color: sel ? '#fff' : 'var(--hachi-muted)',
                          display: 'grid',
                          placeItems: 'center',
                          fontSize: pk.monogram.length > 1 ? 10 : 12,
                          fontWeight: 500,
                          fontFamily: 'var(--font-mono)',
                        }}
                      >
                        {pk.monogram}
                      </span>
                      <span
                        style={{
                          fontSize: 11,
                          color: sel ? 'var(--hachi-brand)' : 'var(--hachi-ink)',
                          textAlign: 'center',
                          lineHeight: 1.2,
                        }}
                      >
                        {pk.label}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* Name */}
            <div>
              <label style={labelStyle}>Name</label>
              <input
                style={{ ...inputStyle, opacity: isEditing ? 0.6 : 1 }}
                value={name}
                disabled={isEditing}
                onChange={(e) => {
                  setName(e.target.value);
                  setNameAuto(false);
                }}
                placeholder="e.g. my-openrouter"
              />
            </div>

            {/* Base URL */}
            {meta.needsBase && (
              <div>
                <label style={labelStyle}>Base URL</label>
                <input
                  style={inputStyle}
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder={meta.baseUrlPlaceholder}
                />
              </div>
            )}

            {/* API key */}
            {meta.needsKey && (
              <div>
                <label style={labelStyle}>
                  API key{isEditing ? ' (leave blank to keep existing)' : ''}
                </label>
                <input
                  type="password"
                  style={inputStyle}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={isEditing ? '••••••••' : 'sk-…'}
                  autoComplete="off"
                />
                <div style={{ fontSize: 11, color: 'var(--hachi-muted)', marginTop: 4 }}>
                  Stored securely, never shown again.
                </div>
              </div>
            )}

            {/* Model */}
            <div>
              <label style={labelStyle}>Default model</label>
              <input
                style={inputStyle}
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={meta.modelPlaceholder}
              />
            </div>

            {/* Advanced */}
            <button
              type="button"
              onClick={() => setAdvancedOpen((v) => !v)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                alignSelf: 'flex-start',
                background: 'transparent',
                border: 'none',
                color: 'var(--hachi-trust)',
                cursor: 'pointer',
                font: '500 12px var(--font-body)',
                padding: 0,
              }}
            >
              <Icon name={advancedOpen ? 'chevron-down' : 'chevron-right'} size={14} />
              Advanced
            </button>

            {advancedOpen && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div>
                  <label style={labelStyle}>Allowed models (comma-separated, blank = all)</label>
                  <input
                    style={inputStyle}
                    value={allowedRaw}
                    onChange={(e) => setAllowedRaw(e.target.value)}
                    placeholder="e.g. claude-haiku-4-5, claude-sonnet-4-6"
                  />
                </div>

                {meta.headers && (
                  <div>
                    <label style={labelStyle}>Custom HTTP headers (optional)</label>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {headers.map((row) => (
                        <div key={row.id} style={{ display: 'flex', gap: 8 }}>
                          <input
                            style={{ ...inputStyle, flex: 1 }}
                            value={row.key}
                            onChange={(e) => setHeader(row.id, 'key', e.target.value)}
                            placeholder="Header name"
                          />
                          <input
                            style={{ ...inputStyle, flex: 2 }}
                            value={row.value}
                            onChange={(e) => setHeader(row.id, 'value', e.target.value)}
                            placeholder="Value"
                          />
                          <button
                            type="button"
                            onClick={() => removeHeader(row.id)}
                            aria-label="Remove header"
                            style={{
                              height: 40,
                              width: 40,
                              borderRadius: 10,
                              border: '0.5px solid var(--hachi-line)',
                              background: 'transparent',
                              color: 'var(--hachi-muted)',
                              cursor: 'pointer',
                              fontSize: 16,
                              flexShrink: 0,
                            }}
                          >
                            ×
                          </button>
                        </div>
                      ))}
                      <button
                        type="button"
                        onClick={addHeader}
                        style={{
                          alignSelf: 'flex-start',
                          height: 32,
                          padding: '0 12px',
                          borderRadius: 8,
                          border: '0.5px solid var(--hachi-line)',
                          background: 'transparent',
                          color: 'var(--hachi-muted)',
                          fontSize: 13,
                          cursor: 'pointer',
                        }}
                      >
                        + Add header
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {error && (
              <div
                style={{
                  padding: '10px 14px',
                  borderRadius: 10,
                  background: 'var(--hachi-danger-tint)',
                  color: 'var(--hachi-danger)',
                  fontSize: 13,
                }}
              >
                {error}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div
          style={{
            display: 'flex',
            gap: 10,
            padding: '14px 22px',
            borderTop: '0.5px solid var(--hachi-line)',
            flexShrink: 0,
          }}
        >
          <button
            type="button"
            onClick={onClose}
            style={{
              flex: 1,
              height: 42,
              borderRadius: 10,
              border: '1px solid var(--hachi-trust)',
              background: 'transparent',
              color: 'var(--hachi-trust)',
              font: '500 14px var(--font-body)',
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={saving}
            style={{
              flex: 1,
              height: 42,
              borderRadius: 10,
              border: 'none',
              background: 'var(--hachi-brand-hover)',
              color: 'var(--hachi-canvas)',
              font: '500 14px var(--font-body)',
              cursor: saving ? 'default' : 'pointer',
              opacity: saving ? 0.6 : 1,
            }}
          >
            {saving ? 'Saving…' : isEditing ? 'Save changes' : 'Add provider'}
          </button>
        </div>
      </div>
    </div>
  );
}
