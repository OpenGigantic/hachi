// ProviderSettings — multi-provider & routing settings card.
//
// Fetches from GET /config/providers, /config/routing, /config/models/all.
// Mutations via POST/PUT/DELETE /config/providers/* and PUT /config/routing.
// Matches the inline-style conventions of settings.tsx / VoiceSettings.tsx.

import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  getProviders,
  addProvider,
  updateProvider,
  removeProvider,
  testConnection,
  getRouting,
  setRouting,
  listAllModels,
  type ProviderEntry,
  type ProviderSpec,
  type CapabilityRow,
} from '../../lib/data/domains/providers';

// ---- Shared style tokens ----

const cardStyle: React.CSSProperties = {
  background: 'var(--hachi-surface)',
  border: '0.5px solid var(--hachi-line)',
  borderRadius: 16,
  padding: 22,
};

const sectionHeadingStyle: React.CSSProperties = {
  margin: 0,
  fontFamily: 'var(--font-display)',
  fontWeight: 500,
  fontSize: 17,
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  height: 40,
  padding: '0 12px',
  borderRadius: 10,
  border: '0.5px solid var(--hachi-line)',
  background: 'var(--hachi-canvas)',
  font: '14px var(--font-mono)',
  color: 'var(--hachi-ink)',
  outline: 'none',
  boxSizing: 'border-box',
};

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  color: 'var(--hachi-muted)',
  marginBottom: 5,
  display: 'block',
};

const badgeStyle = (kind: string): React.CSSProperties => ({
  display: 'inline-block',
  padding: '2px 7px',
  borderRadius: 6,
  fontSize: 11,
  fontFamily: 'var(--font-mono)',
  background: kind === 'anthropic' ? 'var(--hachi-brand-tint)' : 'var(--hachi-surface)',
  color: kind === 'anthropic' ? 'var(--hachi-brand)' : 'var(--hachi-muted)',
  border: '0.5px solid var(--hachi-line)',
});

// ---- Provider kinds available in the form ----

const PROVIDER_KINDS = [
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai-compatible', label: 'OpenAI / compatible' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'groq', label: 'Groq' },
  { value: 'together', label: 'Together AI' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'ollama', label: 'Ollama (local)' },
];

// Kinds that require a base_url
const NEEDS_BASE_URL = new Set(['openai-compatible', 'ollama']);
// Kinds that do NOT require an API key
const NO_KEY_KINDS = new Set(['ollama']);

// ---- Small reusable sub-components ----

function ActionButton({
  onClick,
  children,
  variant = 'default',
  disabled,
}: {
  onClick: () => void;
  children: React.ReactNode;
  variant?: 'default' | 'danger' | 'primary';
  disabled?: boolean;
}) {
  const [hovered, setHovered] = useState(false);
  const bg =
    variant === 'primary'
      ? hovered
        ? 'var(--hachi-brand-active)'
        : 'var(--hachi-brand-hover)'
      : variant === 'danger'
        ? hovered
          ? 'var(--hachi-danger-tint)'
          : 'transparent'
        : hovered
          ? 'var(--hachi-trust-tint)'
          : 'transparent';
  const color =
    variant === 'primary'
      ? 'var(--hachi-canvas)'
      : variant === 'danger'
        ? 'var(--hachi-danger)'
        : 'var(--hachi-trust)';
  const border =
    variant === 'primary'
      ? 'none'
      : variant === 'danger'
        ? '1px solid var(--hachi-danger)'
        : '1px solid var(--hachi-trust)';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        height: 34,
        padding: '0 12px',
        borderRadius: 10,
        border,
        background: bg,
        color,
        font: '500 13px var(--font-body)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'background 0.1s ease',
      }}
    >
      {children}
    </button>
  );
}

function StatusDot({
  ok,
  testing,
  detail,
}: {
  ok: boolean | null;
  testing: boolean;
  detail?: string;
}) {
  if (testing) {
    return (
      <span
        style={{
          display: 'inline-block',
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: 'var(--hachi-working)',
          flexShrink: 0,
        }}
        title="Testing…"
      />
    );
  }
  if (ok === null) return null;
  return (
    <span
      style={{
        display: 'inline-block',
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: ok ? 'var(--hachi-trust)' : 'var(--hachi-danger)',
        flexShrink: 0,
      }}
      title={ok ? 'Connected' : detail ? `Connection failed: ${detail}` : 'Connection failed'}
    />
  );
}

function ToastBanner({ message, type }: { message: string; type: 'success' | 'error' }) {
  return (
    <div
      style={{
        padding: '10px 14px',
        borderRadius: 10,
        background:
          type === 'success' ? 'var(--hachi-trust-tint)' : 'var(--hachi-danger-tint)',
        color: type === 'success' ? 'var(--hachi-trust)' : 'var(--hachi-danger)',
        fontSize: 13,
        marginTop: 10,
      }}
    >
      {message}
    </div>
  );
}

// ---- Add/Edit Provider Form ----

interface HeaderRow {
  id: string;
  key: string;
  value: string;
}

interface ProviderFormState {
  name: string;
  kind: string;
  model: string;
  base_url: string;
  api_key: string;
  allowed_models_raw: string; // comma-separated
  headers: HeaderRow[];
}

const DEFAULT_FORM: ProviderFormState = {
  name: '',
  kind: 'anthropic',
  model: '',
  base_url: '',
  api_key: '',
  allowed_models_raw: '',
  headers: [],
};

function ProviderForm({
  initial,
  isEditing,
  onSave,
  onCancel,
}: {
  initial?: Partial<ProviderFormState>;
  isEditing: boolean;
  onSave: (name: string, spec: ProviderSpec) => Promise<void>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<ProviderFormState>({ ...DEFAULT_FORM, ...initial });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  function setField<K extends keyof ProviderFormState>(k: K, v: ProviderFormState[K]) {
    setForm((prev) => ({ ...prev, [k]: v }));
  }

  // Stable id counter for header rows — avoids index-key mis-mapping on removal.
  const headerIdRef = useRef(0);

  function addHeader() {
    headerIdRef.current += 1;
    setField('headers', [
      ...form.headers,
      { id: String(headerIdRef.current), key: '', value: '' },
    ]);
  }

  function setHeader(id: string, field: 'key' | 'value', val: string) {
    const h = form.headers.map((r) => (r.id === id ? { ...r, [field]: val } : r));
    setField('headers', h);
  }

  function removeHeader(id: string) {
    setField('headers', form.headers.filter((r) => r.id !== id));
  }

  async function handleSubmit() {
    if (!form.name.trim()) {
      setError('Provider name is required');
      return;
    }
    if (!form.model.trim()) {
      setError('Model is required');
      return;
    }
    if (NEEDS_BASE_URL.has(form.kind) && !form.base_url.trim()) {
      setError('Base URL is required for this provider kind');
      return;
    }
    setSaving(true);
    setError('');
    const headers: Record<string, string> = {};
    for (const row of form.headers) {
      if (row.key.trim()) headers[row.key.trim()] = row.value;
    }
    const spec: ProviderSpec = {
      kind: form.kind,
      model: form.model.trim(),
      base_url: form.base_url.trim() || null,
      allowed_models: form.allowed_models_raw
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
      headers,
      api_key: form.api_key || undefined,
    };
    try {
      await onSave(form.name.trim(), spec);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  const needsKey = !NO_KEY_KINDS.has(form.kind);
  const needsBase = NEEDS_BASE_URL.has(form.kind);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
        padding: '16px 0 0',
        borderTop: '0.5px solid var(--hachi-line)',
        marginTop: 14,
      }}
    >
      <div style={{ fontWeight: 500, fontSize: 15 }}>
        {isEditing ? 'Edit provider' : 'Add provider'}
      </div>

      {/* Name */}
      {!isEditing && (
        <div>
          <label style={labelStyle}>Provider name (unique identifier)</label>
          <input
            style={inputStyle}
            value={form.name}
            onChange={(e) => setField('name', e.target.value)}
            placeholder="e.g. my-openrouter"
          />
        </div>
      )}

      {/* Kind */}
      <div>
        <label style={labelStyle}>Kind</label>
        <select
          value={form.kind}
          onChange={(e) => setField('kind', e.target.value)}
          style={{
            ...inputStyle,
            cursor: 'pointer',
          }}
        >
          {PROVIDER_KINDS.map((pk) => (
            <option key={pk.value} value={pk.value}>
              {pk.label}
            </option>
          ))}
        </select>
      </div>

      {/* Base URL — required for openai-compatible / ollama */}
      {needsBase && (
        <div>
          <label style={labelStyle}>
            Base URL{form.kind === 'ollama' ? ' (e.g. http://localhost:11434)' : ' (required)'}
          </label>
          <input
            style={inputStyle}
            value={form.base_url}
            onChange={(e) => setField('base_url', e.target.value)}
            placeholder={
              form.kind === 'ollama'
                ? 'http://localhost:11434'
                : 'https://openrouter.ai/api/v1'
            }
          />
        </div>
      )}

      {/* Model */}
      <div>
        <label style={labelStyle}>Default model</label>
        <input
          style={inputStyle}
          value={form.model}
          onChange={(e) => setField('model', e.target.value)}
          placeholder={
            form.kind === 'anthropic'
              ? 'claude-haiku-4-5'
              : form.kind === 'ollama'
                ? 'llama3.1:8b'
                : 'gpt-4o-mini'
          }
        />
      </div>

      {/* Allowed models */}
      <div>
        <label style={labelStyle}>Allowed models (comma-separated, blank = all)</label>
        <input
          style={inputStyle}
          value={form.allowed_models_raw}
          onChange={(e) => setField('allowed_models_raw', e.target.value)}
          placeholder="e.g. claude-haiku-4-5, claude-sonnet-4-6"
        />
      </div>

      {/* API key */}
      {needsKey && (
        <div>
          <label style={labelStyle}>
            API key{isEditing ? ' (leave blank to keep existing)' : ''}
          </label>
          <input
            type="password"
            style={inputStyle}
            value={form.api_key}
            onChange={(e) => setField('api_key', e.target.value)}
            placeholder={isEditing ? '••••••••' : 'sk-…'}
            autoComplete="off"
          />
          <div style={{ fontSize: 12, color: 'var(--hachi-muted)', marginTop: 4 }}>
            Stored securely, never shown
          </div>
        </div>
      )}

      {/* Custom headers */}
      {(form.kind === 'openai-compatible' ||
        form.kind === 'openrouter' ||
        form.kind === 'groq' ||
        form.kind === 'together' ||
        form.kind === 'deepseek') && (
        <div>
          <label style={labelStyle}>Custom HTTP headers (optional)</label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {form.headers.map((row) => (
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
                  style={{
                    height: 40,
                    width: 40,
                    borderRadius: 10,
                    border: '0.5px solid var(--hachi-line)',
                    background: 'transparent',
                    color: 'var(--hachi-muted)',
                    cursor: 'pointer',
                    fontSize: 16,
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

      {error && <ToastBanner message={error} type="error" />}

      <div style={{ display: 'flex', gap: 8 }}>
        <ActionButton onClick={() => void handleSubmit()} variant="primary" disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </ActionButton>
        <ActionButton onClick={onCancel}>Cancel</ActionButton>
      </div>
    </div>
  );
}

// ---- Provider row ----

function ProviderRow({
  provider,
  onRemove,
  onEdit,
}: {
  provider: ProviderEntry;
  onRemove: (name: string) => void;
  onEdit: (p: ProviderEntry) => void;
}) {
  const [status, setStatus] = useState<boolean | null>(null);
  const [testDetail, setTestDetail] = useState<string | undefined>(undefined);
  const [testing, setTesting] = useState(false);

  async function handleTest() {
    setTesting(true);
    setTestDetail(undefined);
    const result = await testConnection(provider.name);
    setStatus(result.ok);
    if (!result.ok) setTestDetail(result.detail);
    setTesting(false);
  }

  const displayName =
    provider.name === '__primary__' ? 'Primary provider' : provider.name;

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '12px 0',
        borderBottom: '0.5px solid var(--hachi-line)',
      }}
    >
      <StatusDot ok={status} testing={testing} detail={testDetail} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontWeight: 500, fontSize: 14 }}>{displayName}</span>
          <span style={badgeStyle(provider.kind)}>{provider.kind}</span>
          {!provider.configured && !NO_KEY_KINDS.has(provider.kind) && (
            <span
              style={{
                fontSize: 11,
                color: 'var(--hachi-working)',
                fontFamily: 'var(--font-mono)',
              }}
            >
              no key
            </span>
          )}
        </div>
        <div
          style={{
            fontSize: 13,
            color: 'var(--hachi-muted)',
            marginTop: 2,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {provider.model}
          {provider.base_url && ` · ${provider.base_url}`}
        </div>
        {/* Inline failure detail so the user can see WHY the test failed. */}
        {status === false && testDetail && (
          <div
            style={{
              fontSize: 12,
              color: 'var(--hachi-danger)',
              marginTop: 3,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {testDetail}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
        <ActionButton onClick={() => void handleTest()} disabled={testing}>
          Test
        </ActionButton>
        <ActionButton onClick={() => onEdit(provider)}>Edit</ActionButton>
        {provider.name !== '__primary__' && (
          <ActionButton onClick={() => onRemove(provider.name)} variant="danger">
            Remove
          </ActionButton>
        )}
      </div>
    </div>
  );
}

// ---- Routing matrix ----

function RoutingMatrix({
  routing,
  allModels,
  onSave,
}: {
  routing: { default_model: string; capabilities: CapabilityRow[] };
  allModels: Record<string, string[]>;
  onSave: (defaultModel: string, caps: Record<string, string[]>) => Promise<void>;
}) {
  const [defaultModel, setDefaultModel] = useState(routing.default_model);
  const [caps, setCaps] = useState<Record<string, string[]>>(
    Object.fromEntries(routing.capabilities.map((r) => [r.name, r.models]))
  );
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  // Build flat model list across all providers for dropdowns
  const allModelList = Array.from(
    new Set(Object.values(allModels).flat())
  ).sort();

  function setCapModel(capName: string, model: string) {
    setCaps((prev) => ({ ...prev, [capName]: model ? [model] : [] }));
  }

  async function handleSave() {
    setSaving(true);
    setToast(null);
    try {
      await onSave(defaultModel, caps);
      setToast({ msg: 'Routing saved', type: 'success' });
    } catch (e: unknown) {
      setToast({
        msg: e instanceof Error ? e.message : 'Save failed',
        type: 'error',
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ marginTop: 24 }}>
      <h3
        style={{
          ...sectionHeadingStyle,
          fontSize: 15,
          margin: '0 0 12px',
        }}
      >
        Routing matrix
      </h3>

      {/* Default model */}
      <div style={{ marginBottom: 14 }}>
        <label style={labelStyle}>Default model (fallback for all capabilities)</label>
        <select
          value={defaultModel}
          onChange={(e) => setDefaultModel(e.target.value)}
          style={{ ...inputStyle, cursor: 'pointer' }}
        >
          <option value="">— keep current —</option>
          {allModelList.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>

      {/* Per-capability rows */}
      <div
        style={{
          border: '0.5px solid var(--hachi-line)',
          borderRadius: 10,
          overflow: 'hidden',
        }}
      >
        {routing.capabilities.map((row, idx) => (
          <div
            key={row.name}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '10px 12px',
              background: idx % 2 === 0 ? 'var(--hachi-canvas)' : 'var(--hachi-surface)',
              borderBottom:
                idx < routing.capabilities.length - 1
                  ? '0.5px solid var(--hachi-line)'
                  : undefined,
            }}
          >
            <div style={{ flex: 1, fontSize: 13, fontWeight: 500 }}>{row.label}</div>
            <div style={{ flex: 1 }}>
              <select
                value={caps[row.name]?.[0] ?? ''}
                onChange={(e) => setCapModel(row.name, e.target.value)}
                style={{
                  width: '100%',
                  height: 34,
                  padding: '0 8px',
                  borderRadius: 8,
                  border: '0.5px solid var(--hachi-line)',
                  background: 'var(--hachi-canvas)',
                  fontSize: 12,
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--hachi-ink)',
                  cursor: 'pointer',
                }}
              >
                <option value="">— use default —</option>
                {allModelList.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
          </div>
        ))}
      </div>

      {toast && <ToastBanner message={toast.msg} type={toast.type} />}

      <div style={{ marginTop: 14 }}>
        <ActionButton onClick={() => void handleSave()} variant="primary" disabled={saving}>
          {saving ? 'Saving…' : 'Save routing'}
        </ActionButton>
      </div>
    </div>
  );
}

// ---- Main component ----

type EditState =
  | { mode: 'none' }
  | { mode: 'add' }
  | { mode: 'edit'; provider: ProviderEntry };

/**
 * Self-contained provider settings panel.
 * Renders the provider list, add/edit forms, and routing matrix.
 * Matches the card/sectionHeading style of settings.tsx.
 */
export default function ProviderSettings() {
  const [providers, setProviders] = useState<ProviderEntry[]>([]);
  const [routing, setRoutingState] = useState<{
    default_model: string;
    capabilities: CapabilityRow[];
  } | null>(null);
  const [allModels, setAllModels] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [editState, setEditState] = useState<EditState>({ mode: 'none' });
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clear the toast timer on unmount to prevent setState-after-unmount.
  useEffect(() => {
    return () => {
      if (toastTimerRef.current !== null) {
        clearTimeout(toastTimerRef.current);
      }
    };
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const [pResp, rResp, mResp] = await Promise.all([
        getProviders(),
        getRouting(),
        listAllModels(),
      ]);
      // Guard against undefined/non-2xx bodies — any .map must run on a real array.
      setProviders(pResp?.providers ?? []);
      setRoutingState(
        rResp
          ? { ...rResp, capabilities: rResp.capabilities ?? [] }
          : null
      );
      setAllModels(mResp ?? {});
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load provider config');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function showToast(msg: string, type: 'success' | 'error') {
    // Cancel any in-flight timer before scheduling a new one.
    if (toastTimerRef.current !== null) {
      clearTimeout(toastTimerRef.current);
    }
    setToast({ msg, type });
    toastTimerRef.current = setTimeout(() => {
      setToast(null);
      toastTimerRef.current = null;
    }, 3500);
  }

  async function handleSave(name: string, spec: ProviderSpec) {
    if (editState.mode === 'add') {
      const entry = await addProvider(name, spec);
      setProviders((prev) => [...prev, entry]);
    } else if (editState.mode === 'edit') {
      const entry = await updateProvider(name, spec);
      setProviders((prev) => prev.map((p) => (p.name === name ? entry : p)));
    }
    setEditState({ mode: 'none' });
    showToast('Provider saved', 'success');
    // Refresh model list
    try {
      const mResp = await listAllModels();
      setAllModels(mResp);
    } catch {
      /* non-fatal */
    }
  }

  async function handleRemove(name: string) {
    try {
      await removeProvider(name);
      setProviders((prev) => prev.filter((p) => p.name !== name));
      showToast('Provider removed', 'success');
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : 'Remove failed', 'error');
    }
  }

  async function handleSaveRouting(
    defaultModel: string,
    caps: Record<string, string[]>
  ) {
    const resp = await setRouting(defaultModel, caps);
    setRoutingState(resp);
  }

  // Counter for stable header row ids when populating the edit form.
  const editHeaderIdRef = useRef(0);

  // Build initial form state from a provider entry
  function providerToForm(p: ProviderEntry): Partial<ProviderFormState> {
    return {
      name: p.name,
      kind: p.kind,
      model: p.model,
      base_url: p.base_url ?? '',
      api_key: '',
      allowed_models_raw: (p.allowed_models ?? []).join(', '),
      headers: Object.entries(p.headers ?? {}).map(([key, value]) => {
        editHeaderIdRef.current += 1;
        return { id: String(editHeaderIdRef.current), key, value };
      }),
    };
  }

  if (loading) {
    return (
      <div style={cardStyle}>
        <h2 style={{ ...sectionHeadingStyle, margin: '0 0 16px' }}>AI providers &amp; routing</h2>
        <div style={{ color: 'var(--hachi-muted)', fontSize: 14 }}>Loading…</div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div style={cardStyle}>
        <h2 style={{ ...sectionHeadingStyle, margin: '0 0 16px' }}>AI providers &amp; routing</h2>
        <ToastBanner message={loadError} type="error" />
        <div style={{ marginTop: 12 }}>
          <ActionButton onClick={() => void load()}>Retry</ActionButton>
        </div>
      </div>
    );
  }

  return (
    <div style={cardStyle}>
      <h2 style={{ ...sectionHeadingStyle, margin: '0 0 16px' }}>AI providers &amp; routing</h2>

      {/* Provider list */}
      {providers.length === 0 ? (
        <div
          style={{
            padding: '24px 0',
            textAlign: 'center',
            color: 'var(--hachi-muted)',
            fontSize: 14,
          }}
        >
          No providers configured. Add one below.
        </div>
      ) : (
        <div>
          {providers.map((p) => (
            <ProviderRow
              key={p.name}
              provider={p}
              onRemove={(name) => void handleRemove(name)}
              onEdit={(provider) => setEditState({ mode: 'edit', provider })}
            />
          ))}
        </div>
      )}

      {/* Add/Edit form */}
      {editState.mode === 'none' && (
        <div style={{ marginTop: 14 }}>
          <ActionButton onClick={() => setEditState({ mode: 'add' })}>
            + Add provider
          </ActionButton>
        </div>
      )}

      {editState.mode === 'add' && (
        <ProviderForm
          isEditing={false}
          onSave={handleSave}
          onCancel={() => setEditState({ mode: 'none' })}
        />
      )}

      {editState.mode === 'edit' && (
        <ProviderForm
          initial={providerToForm(editState.provider)}
          isEditing={true}
          onSave={handleSave}
          onCancel={() => setEditState({ mode: 'none' })}
        />
      )}

      {toast && <ToastBanner message={toast.msg} type={toast.type} />}

      {/* Routing matrix */}
      {routing && (
        <RoutingMatrix
          routing={routing}
          allModels={allModels}
          onSave={handleSaveRouting}
        />
      )}
    </div>
  );
}

