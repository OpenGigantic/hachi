// VoiceSettings — settings card for STT/TTS provider configuration.
//
// Reads from GET /voice/config, saves via PUT /voice/config.
// Matches the card style of settings.tsx (cardStyle / sectionHeadingStyle).

import React, { useEffect, useState } from 'react'
import {
  getVoiceConfig,
  setVoiceConfig,
  type VoiceConfig,
  type VoiceConfigPatch,
} from '../../lib/data/domains/voice'

// ---- Inline field component ----

function Field({
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
  hint,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
  hint?: string
}) {
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
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <label
        style={{ fontSize: 12, color: 'var(--hachi-muted)', fontWeight: 500 }}
      >
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={inputStyle}
        onFocus={(e) =>
          ((e.currentTarget as HTMLInputElement).style.borderColor = 'var(--hachi-trust)')
        }
        onBlur={(e) =>
          ((e.currentTarget as HTMLInputElement).style.borderColor = 'var(--hachi-line)')
        }
      />
      {hint && (
        <span style={{ fontSize: 11, color: 'var(--hachi-muted)', marginTop: 2 }}>
          {hint}
        </span>
      )}
    </div>
  )
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  options: string[]
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <label
        style={{ fontSize: 12, color: 'var(--hachi-muted)', fontWeight: 500 }}
      >
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          height: 40,
          padding: '0 12px',
          borderRadius: 10,
          border: '0.5px solid var(--hachi-line)',
          background: 'var(--hachi-canvas)',
          color: 'var(--hachi-ink)',
          font: '14px var(--font-body)',
          outline: 'none',
          cursor: 'pointer',
        }}
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  )
}

// ---- Main component ----

export default function VoiceSettings() {
  const [config, setConfig] = useState<VoiceConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveOk, setSaveOk] = useState(false)

  // Local editable state
  const [ttsProvider, setTtsProvider] = useState('openai')
  const [ttsVoice, setTtsVoice] = useState('alloy')
  const [sttModel, setSttModel] = useState('')
  const [ttsModel, setTtsModel] = useState('')
  const [sttBaseUrl, setSttBaseUrl] = useState('')
  const [ttsBaseUrl, setTtsBaseUrl] = useState('')
  const [sttApiKeyEnv, setSttApiKeyEnv] = useState('')
  const [ttsApiKeyEnv, setTtsApiKeyEnv] = useState('')
  const [elApiKeyEnv, setElApiKeyEnv] = useState('')
  const [elVoiceId, setElVoiceId] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getVoiceConfig()
      .then((cfg) => {
        if (cancelled) return
        setConfig(cfg)
        setTtsProvider(cfg.tts_provider)
        setTtsVoice(cfg.tts_voice)
        setSttModel(cfg.stt_model ?? '')
        setTtsModel(cfg.tts_model ?? '')
        setElVoiceId(cfg.elevenlabs_voice_id ?? '')
      })
      .catch(() => { /* gateway may be offline; show empty form */ })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    setSaveOk(false)
    const patch: VoiceConfigPatch = {}
    if (ttsProvider) patch.tts_provider = ttsProvider
    if (ttsVoice) patch.tts_voice = ttsVoice
    if (sttModel.trim()) patch.stt_model = sttModel.trim()
    if (ttsModel.trim()) patch.tts_model = ttsModel.trim()
    if (sttBaseUrl.trim()) patch.stt_base_url = sttBaseUrl.trim()
    if (ttsBaseUrl.trim()) patch.tts_base_url = ttsBaseUrl.trim()
    if (sttApiKeyEnv.trim()) patch.stt_api_key_env = sttApiKeyEnv.trim()
    if (ttsApiKeyEnv.trim()) patch.tts_api_key_env = ttsApiKeyEnv.trim()
    if (elApiKeyEnv.trim()) patch.elevenlabs_api_key_env = elApiKeyEnv.trim()
    if (elVoiceId.trim()) patch.elevenlabs_voice_id = elVoiceId.trim()
    try {
      const updated = await setVoiceConfig(patch)
      setConfig(updated)
      setSaveOk(true)
      setTimeout(() => setSaveOk(false), 2500)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Could not save voice settings'
      setSaveError(msg)
    } finally {
      setSaving(false)
    }
  }

  // Card styles matching settings.tsx
  const cardStyle: React.CSSProperties = {
    background: 'var(--hachi-surface)',
    border: '0.5px solid var(--hachi-line)',
    borderRadius: 16,
    padding: 22,
  }

  const sectionHeadingStyle: React.CSSProperties = {
    margin: '0 0 16px',
    fontFamily: 'var(--font-display)',
    fontWeight: 500,
    fontSize: 17,
  }

  return (
    <div style={cardStyle}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <span style={{ fontSize: 20 }}>🎙</span>
        <h2 style={{ ...sectionHeadingStyle, margin: 0 }}>Voice</h2>
        {config?.configured && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 500,
              color: 'var(--hachi-trust)',
              background: 'var(--hachi-trust-tint)',
              padding: '3px 9px',
              borderRadius: 999,
            }}
          >
            configured
          </span>
        )}
      </div>
      <div
        style={{ fontSize: 13, color: 'var(--hachi-muted)', marginBottom: 20 }}
      >
        Gateway-powered speech-to-text (OpenAI Whisper) and text-to-speech
        (OpenAI or ElevenLabs). Set the env-var names that hold your API keys
        — keys are never stored in settings directly.
      </div>

      {loading ? (
        <div style={{ fontSize: 13, color: 'var(--hachi-muted)', padding: '12px 0' }}>
          Loading voice config…
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* STT section */}
          <div
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: 'var(--hachi-muted)',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              paddingBottom: 6,
              borderBottom: '0.5px solid var(--hachi-line)',
            }}
          >
            Speech-to-text (STT)
          </div>
          <Field
            label="STT provider"
            value="openai (Whisper)"
            onChange={() => { /* read-only — openai is the only STT option */ }}
            placeholder="openai"
            hint="Only OpenAI-compatible Whisper is supported for STT."
          />
          <Field
            label="STT API key env var"
            value={sttApiKeyEnv}
            onChange={setSttApiKeyEnv}
            placeholder="OPENAI_API_KEY"
            hint="Name of the environment variable that holds the STT key. Defaults to OPENAI_API_KEY."
          />
          <Field
            label="STT model"
            value={sttModel}
            onChange={setSttModel}
            placeholder="whisper-1"
            hint="Leave blank for the default (whisper-1)."
          />
          <Field
            label="STT base URL"
            value={sttBaseUrl}
            onChange={setSttBaseUrl}
            placeholder="https://api.openai.com/v1"
            hint="Override for self-hosted or compatible endpoints."
          />

          {/* TTS section */}
          <div
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: 'var(--hachi-muted)',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              paddingBottom: 6,
              borderBottom: '0.5px solid var(--hachi-line)',
              marginTop: 8,
            }}
          >
            Text-to-speech (TTS)
          </div>
          <SelectField
            label="TTS provider"
            value={ttsProvider}
            onChange={setTtsProvider}
            options={config?.available?.tts_providers ?? ['openai', 'elevenlabs']}
          />

          {ttsProvider === 'openai' && (
            <>
              <SelectField
                label="Voice"
                value={ttsVoice}
                onChange={setTtsVoice}
                options={config?.available?.voices ?? ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer']}
              />
              <Field
                label="TTS API key env var"
                value={ttsApiKeyEnv}
                onChange={setTtsApiKeyEnv}
                placeholder="OPENAI_API_KEY"
                hint="Name of the environment variable that holds the TTS key. Defaults to OPENAI_API_KEY."
              />
              <Field
                label="TTS model"
                value={ttsModel}
                onChange={setTtsModel}
                placeholder="tts-1"
                hint="Leave blank for the default (tts-1)."
              />
              <Field
                label="TTS base URL"
                value={ttsBaseUrl}
                onChange={setTtsBaseUrl}
                placeholder="https://api.openai.com/v1"
                hint="Override for self-hosted or compatible endpoints."
              />
            </>
          )}

          {ttsProvider === 'elevenlabs' && (
            <>
              <Field
                label="ElevenLabs API key env var"
                value={elApiKeyEnv}
                onChange={setElApiKeyEnv}
                placeholder="ELEVENLABS_API_KEY"
                hint="Name of the env var holding your ElevenLabs API key."
              />
              <Field
                label="ElevenLabs voice ID"
                value={elVoiceId}
                onChange={setElVoiceId}
                placeholder="21m00Tcm4TlvDq8ikWAM"
                hint='Voice ID from the ElevenLabs dashboard. Default: "Rachel".'
              />
            </>
          )}

          {/* Save controls */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              marginTop: 8,
            }}
          >
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving}
              style={{
                height: 36,
                padding: '0 18px',
                borderRadius: 10,
                border: 'none',
                background: 'var(--hachi-brand-hover)',
                color: 'var(--hachi-canvas)',
                font: '500 13px var(--font-body)',
                cursor: saving ? 'default' : 'pointer',
                opacity: saving ? 0.6 : 1,
              }}
              onMouseEnter={(e) => {
                if (!saving)
                  (e.currentTarget as HTMLButtonElement).style.background =
                    'var(--hachi-brand-active)'
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.background =
                  'var(--hachi-brand-hover)'
              }}
            >
              {saving ? 'Saving…' : 'Save voice settings'}
            </button>
            {saveOk && (
              <span style={{ fontSize: 13, color: 'var(--hachi-trust)' }}>
                Saved
              </span>
            )}
            {saveError && (
              <span style={{ fontSize: 13, color: 'var(--hachi-danger)' }}>
                {saveError}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
