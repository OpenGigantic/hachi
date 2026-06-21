// ============================================================
// Integrations screen — /integrations
// Shows: connected accounts/tools w/ health/status, MCP servers,
// tool inventory (operator-only), add-MCP affordance.
// ============================================================
import React, { CSSProperties, useEffect, useReducer, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useStore } from '../lib/store/store'
import { Icon } from '../components/icons/Icon'
import { ConnectorLogo } from '../components/icons/ConnectorLogo'
import { useFocusTrap } from '../lib/hooks/useFocusTrap'
import type { Integration, McpServer, McpTool } from '../lib/data/types'
import type { IntegrationExtended } from '../lib/data/domains/integrations'
import {
  loadIntegrations,
  connectIntegration,
  disconnectIntegration,
  addMcpServer,
  validateMcpServerInput,
  subscribeActionStatus,
  getConnectStatus,
  getDisconnectStatus,
  ALLOWED_MCP_COMMANDS,
} from '../lib/data/domains/integrations'
import type { McpCommand, McpTransport } from '../lib/data/domains/integrations'

// ---- status meta --------------------------------------------------------

interface StatusMeta {
  bg: string
  fg: string
  dot: string
  text: string
}

const intMeta: Record<string, StatusMeta> = {
  connected: {
    bg: 'var(--hachi-success-tint)',
    fg: '#2C7A50',
    dot: 'var(--hachi-success)',
    text: 'connected',
  },
  degraded: {
    bg: 'var(--hachi-working-tint)',
    fg: '#A8651A',
    dot: 'var(--hachi-working)',
    text: 'degraded',
  },
  none: {
    bg: 'var(--hachi-cream)',
    fg: 'var(--hachi-muted)',
    dot: 'var(--hachi-muted)',
    text: 'not connected',
  },
}

const mcpMeta: Record<string, StatusMeta> = {
  connected: {
    bg: 'var(--hachi-success-tint)',
    fg: '#2C7A50',
    dot: 'var(--hachi-success)',
    text: 'connected',
  },
  starting: {
    bg: 'var(--hachi-working-tint)',
    fg: '#A8651A',
    dot: 'var(--hachi-working)',
    text: 'starting',
  },
  blocked: {
    bg: 'var(--hachi-danger-tint)',
    fg: '#9E2D21',
    dot: 'var(--hachi-danger)',
    text: "can't reach",
  },
}

function getIntMeta(status: string): StatusMeta {
  return intMeta[status] ?? intMeta.none
}

function getMcpMeta(status: string): StatusMeta {
  return mcpMeta[status] ?? mcpMeta.starting
}

// ---- sub-components -----------------------------------------------------

interface StatusDotProps {
  bg: string
  size?: number
}
function StatusDot({ bg, size = 7 }: StatusDotProps) {
  return (
    <span
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: bg,
        flexShrink: 0,
        display: 'inline-block',
      }}
    />
  )
}

interface StatusChipProps {
  bg: string
  fg: string
  dot: string
  text: string
  dotSize?: number
  padding?: string
  fontSize?: number
}
function StatusChip({ bg, fg, dot, text, dotSize = 7, padding = '5px 11px', fontSize = 12 }: StatusChipProps) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding,
        borderRadius: 999,
        background: bg,
        color: fg,
        fontSize,
        fontWeight: 500,
        whiteSpace: 'nowrap',
        flexShrink: 0,
      }}
    >
      <StatusDot bg={dot} size={dotSize} />
      {text}
    </span>
  )
}

// ---- Integration card ---------------------------------------------------

interface IntCardProps {
  integration: Integration
  onConnect: () => void
  onDisconnect: () => void
  connectLoading: boolean
  connectError: boolean
  disconnectLoading: boolean
  disconnectError: boolean
}

function IntCard({
  integration: i,
  onConnect,
  onDisconnect,
  connectLoading,
  connectError,
  disconnectLoading,
  disconnectError,
}: IntCardProps) {
  // Cast to extended to access OAuth-specific fields gracefully.
  const ext = i as Partial<IntegrationExtended>
  const authType = ext.auth_type ?? 'none'
  const configured = ext.configured ?? true  // default true for backwards compat
  const isOAuth = authType === 'oauth2'
  const isUnconfigured = isOAuth && !configured

  const sm = getIntMeta(i.status)
  const connected = i.status === 'connected'

  // Determine primary button label and colour.
  let primaryLabel: string
  if (isUnconfigured) {
    primaryLabel = 'Needs setup'
  } else if (connectLoading) {
    primaryLabel = 'Connecting…'
  } else if (connectError) {
    primaryLabel = 'Retry'
  } else if (i.status === 'connected') {
    primaryLabel = isOAuth ? 'Re-authorise' : 'Re-authorise'
  } else if (i.status === 'degraded') {
    primaryLabel = 'Fix access'
  } else {
    primaryLabel = isOAuth ? 'Connect with OAuth' : 'Connect'
  }

  const primaryFg =
    isUnconfigured
      ? 'var(--hachi-muted)'
      : connectError
      ? 'var(--hachi-danger)'
      : i.status === 'none'
      ? 'var(--hachi-trust)'
      : 'var(--hachi-ink)'

  const [primaryHover, setPrimaryHover] = useState(false)
  const [disconnectHover, setDisconnectHover] = useState(false)

  return (
    <div
      style={{
        background: 'var(--hachi-surface)',
        border: '0.5px solid var(--hachi-line)',
        borderRadius: 14,
        padding: 18,
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
      }}
    >
      {/* header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 10,
            background: 'var(--hachi-cream)',
            display: 'grid',
            placeItems: 'center',
            flexShrink: 0,
          }}
        >
          <ConnectorLogo id={i.id} size={22} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 500, fontSize: 15 }}>{i.name}</div>
          <div style={{ fontSize: 12, color: 'var(--hachi-muted)', marginTop: 1 }}>
            {i.can}
          </div>
        </div>
        <StatusChip
          bg={sm.bg}
          fg={sm.fg}
          dot={sm.dot}
          text={sm.text}
          dotSize={7}
          padding="5px 11px"
          fontSize={12}
        />
      </div>

      {/* unconfigured OAuth warning */}
      {isUnconfigured && (
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 7,
            fontSize: 12,
            color: 'var(--hachi-muted)',
            background: 'var(--hachi-cream)',
            padding: '7px 10px',
            borderRadius: 8,
          }}
          title="An admin must configure the OAuth client ID and secret for this connector before users can connect."
        >
          <Icon name="key" size={13} style={{ marginTop: 1, flexShrink: 0 }} />
          Admin needs to set OAuth client credentials before this can be connected.
        </div>
      )}

      {/* risky warning */}
      {i.risky && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 7,
            fontSize: 12,
            color: 'var(--hachi-danger)',
            background: 'var(--hachi-danger-tint)',
            padding: '7px 10px',
            borderRadius: 8,
          }}
        >
          <Icon name="alert" size={14} />
          Broad filesystem + shell access. Review what it can reach.
        </div>
      )}

      {/* connect error banner */}
      {connectError && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 7,
            fontSize: 12,
            color: 'var(--hachi-danger)',
            background: 'var(--hachi-danger-tint)',
            padding: '7px 10px',
            borderRadius: 8,
          }}
          role="alert"
        >
          <Icon name="alert" size={14} />
          {isOAuth
            ? 'OAuth flow failed. Check popup permissions and try again.'
            : 'Connection failed. Check your gateway and try again.'}
        </div>
      )}

      {/* disconnect error banner */}
      {disconnectError && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 7,
            fontSize: 12,
            color: 'var(--hachi-danger)',
            background: 'var(--hachi-danger-tint)',
            padding: '7px 10px',
            borderRadius: 8,
          }}
          role="alert"
        >
          <Icon name="alert" size={14} />
          Disconnect failed. Check your gateway and try again.
        </div>
      )}

      {/* action buttons */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={isUnconfigured ? undefined : onConnect}
          disabled={isUnconfigured || connectLoading || disconnectLoading}
          onMouseEnter={() => setPrimaryHover(true)}
          onMouseLeave={() => setPrimaryHover(false)}
          aria-busy={connectLoading}
          title={isUnconfigured ? 'An admin must configure OAuth client credentials first.' : undefined}
          style={{
            flex: 1,
            height: 34,
            borderRadius: 10,
            border: connectError
              ? '0.5px solid var(--hachi-danger)'
              : '0.5px solid var(--hachi-line)',
            background: primaryHover && !connectLoading && !disconnectLoading && !isUnconfigured
              ? 'var(--hachi-cream)'
              : 'var(--hachi-canvas)',
            color: primaryFg,
            font: '500 13px var(--font-body)',
            cursor: connectLoading || disconnectLoading || isUnconfigured ? 'not-allowed' : 'pointer',
            opacity: connectLoading || disconnectLoading || isUnconfigured ? 0.65 : 1,
            transition: 'background var(--dur-fast) var(--ease)',
          }}
        >
          {primaryLabel}
        </button>
        {connected && (
          <button
            onClick={onDisconnect}
            disabled={connectLoading || disconnectLoading}
            onMouseEnter={() => setDisconnectHover(true)}
            onMouseLeave={() => setDisconnectHover(false)}
            aria-busy={disconnectLoading}
            style={{
              height: 34,
              padding: '0 14px',
              borderRadius: 10,
              border: disconnectError ? '0.5px solid var(--hachi-danger)' : 'none',
              background: disconnectHover && !connectLoading && !disconnectLoading
                ? 'var(--hachi-cream)'
                : 'transparent',
              color: disconnectError ? 'var(--hachi-danger)' : 'var(--hachi-muted)',
              font: '500 13px var(--font-body)',
              cursor: connectLoading || disconnectLoading ? 'not-allowed' : 'pointer',
              opacity: connectLoading || disconnectLoading ? 0.65 : 1,
              transition: 'background var(--dur-fast) var(--ease)',
            }}
          >
            {disconnectLoading ? 'Disconnecting…' : disconnectError ? 'Retry' : 'Disconnect'}
          </button>
        )}
      </div>
    </div>
  )
}

// ---- MCP server row -----------------------------------------------------

interface McpServerRowProps {
  server: McpServer
  isLast: boolean
}

function McpServerRow({ server: s, isLast }: McpServerRowProps) {
  const m = getMcpMeta(s.status)
  const toolCount = s.tools === 0 ? '—' : s.tools
  return (
    <div
      className="hachi-mcp-server-row"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 13,
        padding: '12px 16px',
        borderBottom: isLast ? 'none' : '0.5px solid var(--hachi-line)',
      }}
    >
      {/* name + error */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 13,
            color: 'var(--hachi-ink)',
          }}
        >
          {s.name}
        </div>
        {s.err && (
          <div
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 11.5,
              color: 'var(--hachi-danger)',
              marginTop: 2,
            }}
          >
            {s.err}
          </div>
        )}
      </div>

      {/* status */}
      <span style={{ width: 96, flexShrink: 0 }}>
        <StatusChip
          bg={m.bg}
          fg={m.fg}
          dot={m.dot}
          text={m.text}
          dotSize={6}
          padding="4px 9px"
          fontSize={11.5}
        />
      </span>

      {/* tool count */}
      <span
        style={{
          width: 52,
          flexShrink: 0,
          textAlign: 'right',
          fontFamily: 'var(--font-mono)',
          fontSize: 12.5,
          color: 'var(--hachi-ink)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {toolCount}
      </span>

      {/* transport */}
      <span
        style={{
          width: 64,
          flexShrink: 0,
          fontFamily: 'var(--font-mono)',
          fontSize: 12,
          color: 'var(--hachi-muted)',
        }}
      >
        {s.transport}
      </span>

      {/* uptime */}
      <span
        style={{
          width: 64,
          flexShrink: 0,
          textAlign: 'right',
          fontFamily: 'var(--font-mono)',
          fontSize: 12,
          color: 'var(--hachi-muted)',
        }}
      >
        {s.uptime}
      </span>
    </div>
  )
}

// ---- MCP tool row -------------------------------------------------------

interface McpToolRowProps {
  tool: McpTool
  isLast: boolean
}

function McpToolRow({ tool: t, isLast }: McpToolRowProps) {
  const args = t.args.map(([k, ty]) => ({ k, ty }))
  return (
    <div
      style={{
        padding: '13px 0',
        borderBottom: isLast ? 'none' : '0.5px solid var(--hachi-line)',
      }}
    >
      {/* name + server badge */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 9,
          marginBottom: args.length > 0 ? 8 : 0,
        }}
      >
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 13,
            color: 'var(--hachi-ink)',
          }}
        >
          {t.name}
        </span>
        <span
          style={{
            fontSize: 11,
            color: 'var(--hachi-muted)',
            background: 'var(--hachi-cream)',
            padding: '2px 8px',
            borderRadius: 999,
          }}
        >
          {t.server}
        </span>
      </div>

      {/* arg chips */}
      {args.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          {args.map((arg) => (
            <span
              key={arg.k}
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 11.5,
                color: 'var(--hachi-muted)',
                background: 'var(--hachi-canvas)',
                border: '0.5px solid var(--hachi-line)',
                padding: '3px 8px',
                borderRadius: 6,
              }}
            >
              <span style={{ color: 'var(--hachi-ink)' }}>{arg.k}</span>: {arg.ty}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ---- Empty state --------------------------------------------------------

function NoIntegrations() {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 12,
        padding: '48px 24px',
        textAlign: 'center',
        color: 'var(--hachi-muted)',
      }}
    >
      <Icon name="plug" size={32} style={{ opacity: 0.35 }} />
      <div style={{ fontSize: 15, fontWeight: 500, color: 'var(--hachi-ink)' }}>
        No accounts connected yet
      </div>
      <div style={{ fontSize: 13 }}>
        Connect an account to let Hachi reach your tools.
      </div>
    </div>
  )
}

function NoMcpServers() {
  return (
    <div
      style={{
        padding: '32px 16px',
        textAlign: 'center',
        color: 'var(--hachi-muted)',
        fontSize: 13,
      }}
    >
      No MCP servers configured.
    </div>
  )
}

// ---- Add MCP dialog — extended with transport selector ------------------

interface AddMcpDialogProps {
  onClose: () => void
}

interface FormState {
  name: string
  transport: McpTransport
  // stdio fields
  command: McpCommand
  args: string
  envKey: string
  envVal: string
  envVars: Record<string, string>
  // sse/http fields
  url: string
  headerKey: string
  headerVal: string
  headers: Record<string, string>
  // submit state
  submitting: boolean
  submitError: string | null
  fieldErrors: Partial<Record<'name' | 'args' | 'envVars' | 'url' | 'command', string>>
}

type FormAction =
  | { type: 'set_name'; value: string }
  | { type: 'set_transport'; value: McpTransport }
  | { type: 'set_command'; value: McpCommand }
  | { type: 'set_args'; value: string }
  | { type: 'set_env_key'; value: string }
  | { type: 'set_env_val'; value: string }
  | { type: 'add_env' }
  | { type: 'remove_env'; key: string }
  | { type: 'set_url'; value: string }
  | { type: 'set_header_key'; value: string }
  | { type: 'set_header_val'; value: string }
  | { type: 'add_header' }
  | { type: 'remove_header'; key: string }
  | { type: 'set_submitting'; value: boolean }
  | { type: 'set_submit_error'; value: string | null }
  | { type: 'set_field_error'; field: keyof FormState['fieldErrors']; value: string | null }
  | { type: 'clear_field_error'; field: keyof FormState['fieldErrors'] }

function formReducer(state: FormState, action: FormAction): FormState {
  switch (action.type) {
    case 'set_name':
      return { ...state, name: action.value, fieldErrors: { ...state.fieldErrors, name: undefined } }
    case 'set_transport':
      return { ...state, transport: action.value, fieldErrors: {} }
    case 'set_command':
      return { ...state, command: action.value }
    case 'set_args':
      return { ...state, args: action.value, fieldErrors: { ...state.fieldErrors, args: undefined } }
    case 'set_env_key':
      return { ...state, envKey: action.value }
    case 'set_env_val':
      return { ...state, envVal: action.value }
    case 'add_env': {
      if (!state.envKey.trim()) return state
      return {
        ...state,
        envVars: { ...state.envVars, [state.envKey.trim()]: state.envVal },
        envKey: '',
        envVal: '',
        fieldErrors: { ...state.fieldErrors, envVars: undefined },
      }
    }
    case 'remove_env': {
      const next = { ...state.envVars }
      delete next[action.key]
      return { ...state, envVars: next }
    }
    case 'set_url':
      return { ...state, url: action.value, fieldErrors: { ...state.fieldErrors, url: undefined } }
    case 'set_header_key':
      return { ...state, headerKey: action.value }
    case 'set_header_val':
      return { ...state, headerVal: action.value }
    case 'add_header': {
      if (!state.headerKey.trim()) return state
      return {
        ...state,
        headers: { ...state.headers, [state.headerKey.trim()]: state.headerVal },
        headerKey: '',
        headerVal: '',
      }
    }
    case 'remove_header': {
      const next = { ...state.headers }
      delete next[action.key]
      return { ...state, headers: next }
    }
    case 'set_submitting':
      return { ...state, submitting: action.value }
    case 'set_submit_error':
      return { ...state, submitError: action.value }
    case 'set_field_error':
      return {
        ...state,
        fieldErrors: {
          ...state.fieldErrors,
          [action.field]: action.value ?? undefined,
        },
      }
    case 'clear_field_error':
      return {
        ...state,
        fieldErrors: { ...state.fieldErrors, [action.field]: undefined },
      }
    default:
      return state
  }
}

const initialFormState: FormState = {
  name: '',
  transport: 'stdio',
  command: 'npx',
  args: '',
  envKey: '',
  envVal: '',
  envVars: {},
  url: '',
  headerKey: '',
  headerVal: '',
  headers: {},
  submitting: false,
  submitError: null,
  fieldErrors: {},
}

function AddMcpDialog({ onClose }: AddMcpDialogProps) {
  const [state, dispatch] = useReducer(formReducer, initialFormState)
  const trapRef = useFocusTrap<HTMLDivElement>(true)

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape' && !state.submitting) onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose, state.submitting])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (state.submitting) return

    const input = {
      name: state.name.trim(),
      transport: state.transport,
      // stdio
      command: state.command,
      args: state.args.trim(),
      envVars: state.envVars,
      // sse/http
      url: state.url.trim(),
      headers: state.headers,
    }

    const result = validateMcpServerInput(input)
    if (!result.ok) {
      const field = result.field as keyof FormState['fieldErrors']
      if (field === 'name' || field === 'args' || field === 'envVars' || field === 'url') {
        dispatch({ type: 'set_field_error', field, value: result.message })
      } else {
        dispatch({ type: 'set_submit_error', value: result.message })
      }
      return
    }

    dispatch({ type: 'set_submitting', value: true })
    dispatch({ type: 'set_submit_error', value: null })

    try {
      await addMcpServer(input)
      useStore.getState().showToast(`MCP server "${input.name}" registered`, 'success')
      onClose()
    } catch (err: unknown) {
      const msg =
        err instanceof Error
          ? err.message
          : 'Failed to register MCP server — check the server name and args, then try again.'
      dispatch({ type: 'set_submit_error', value: msg })
      dispatch({ type: 'set_submitting', value: false })
    }
  }

  const backdropStyle: CSSProperties = {
    position: 'fixed',
    inset: 0,
    background: 'rgba(28,26,32,.4)',
    display: 'grid',
    placeItems: 'center',
    padding: 24,
    zIndex: 60,
  }

  const panelStyle: CSSProperties = {
    width: 520,
    maxWidth: '96vw',
    background: 'var(--hachi-surface)',
    border: '0.5px solid var(--hachi-line)',
    borderRadius: 18,
    overflow: 'hidden',
    boxShadow: '0 24px 60px rgba(28,26,32,.24)',
  }

  const fieldWrapStyle: CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  }

  const labelStyle: CSSProperties = {
    fontSize: 12,
    fontWeight: 500,
    color: 'var(--hachi-muted)',
  }

  function fieldInputStyle(hasError: boolean): CSSProperties {
    return {
      height: 36,
      padding: '0 10px',
      borderRadius: 8,
      border: hasError
        ? '0.5px solid var(--hachi-danger)'
        : '0.5px solid var(--hachi-line)',
      background: 'var(--hachi-canvas)',
      color: 'var(--hachi-ink)',
      font: '13px var(--font-body)',
      width: '100%',
      boxSizing: 'border-box' as CSSProperties['boxSizing'],
    }
  }

  const errorTextStyle: CSSProperties = {
    fontSize: 11.5,
    color: 'var(--hachi-danger)',
    marginTop: 2,
  }

  const hasEnvPairs = Object.keys(state.envVars).length > 0
  const hasHeaderPairs = Object.keys(state.headers).length > 0
  const isStdio = state.transport === 'stdio'

  return (
    <div style={backdropStyle} onClick={state.submitting ? undefined : onClose}>
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-mcp-dialog-title"
        style={panelStyle}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: '20px 22px 0',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div>
            <div
              id="add-mcp-dialog-title"
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 500,
                fontSize: 18,
              }}
            >
              Add MCP server
            </div>
            <div style={{ fontSize: 12, color: 'var(--hachi-muted)', marginTop: 2 }}>
              Register a new Model Context Protocol server for Hachi to call.
            </div>
          </div>
          <button
            type="button"
            onClick={state.submitting ? undefined : onClose}
            disabled={state.submitting}
            aria-label="Close dialog"
            style={{
              width: 30,
              height: 30,
              borderRadius: 8,
              border: 'none',
              background: 'transparent',
              color: 'var(--hachi-muted)',
              cursor: state.submitting ? 'not-allowed' : 'pointer',
              display: 'grid',
              placeItems: 'center',
              flexShrink: 0,
            }}
          >
            <Icon name="x" size={16} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} noValidate>
          <div style={{ padding: '16px 22px', display: 'flex', flexDirection: 'column', gap: 14 }}>

            {/* Server name */}
            <div style={fieldWrapStyle}>
              <label htmlFor="mcp-name" style={labelStyle}>
                Server name <span aria-hidden="true" style={{ color: 'var(--hachi-danger)' }}>*</span>
              </label>
              <input
                id="mcp-name"
                type="text"
                value={state.name}
                onChange={(e) => dispatch({ type: 'set_name', value: e.target.value })}
                placeholder="e.g. github"
                disabled={state.submitting}
                aria-invalid={!!state.fieldErrors.name}
                aria-describedby={state.fieldErrors.name ? 'mcp-name-error' : undefined}
                style={fieldInputStyle(!!state.fieldErrors.name)}
                autoComplete="off"
                spellCheck={false}
              />
              {state.fieldErrors.name && (
                <span id="mcp-name-error" style={errorTextStyle} role="alert">
                  {state.fieldErrors.name}
                </span>
              )}
              <span style={{ fontSize: 11, color: 'var(--hachi-muted)' }}>
                Letters, digits, hyphens, underscores only.
              </span>
            </div>

            {/* Transport selector */}
            <div style={fieldWrapStyle}>
              <span style={labelStyle}>
                Transport <span aria-hidden="true" style={{ color: 'var(--hachi-danger)' }}>*</span>
              </span>
              <div style={{ display: 'flex', gap: 8 }}>
                {(['stdio', 'sse', 'http'] as McpTransport[]).map((t) => {
                  const active = state.transport === t
                  return (
                    <button
                      key={t}
                      type="button"
                      onClick={() => dispatch({ type: 'set_transport', value: t })}
                      disabled={state.submitting}
                      style={{
                        flex: 1,
                        height: 34,
                        borderRadius: 8,
                        border: active
                          ? '1.5px solid var(--hachi-trust)'
                          : '0.5px solid var(--hachi-line)',
                        background: active ? 'var(--hachi-trust-tint)' : 'var(--hachi-canvas)',
                        color: active ? 'var(--hachi-trust)' : 'var(--hachi-muted)',
                        font: `${active ? 600 : 400} 13px var(--font-mono)`,
                        cursor: state.submitting ? 'not-allowed' : 'pointer',
                        transition: 'all var(--dur-fast) var(--ease)',
                      }}
                    >
                      {t}
                    </button>
                  )
                })}
              </div>
              <span style={{ fontSize: 11, color: 'var(--hachi-muted)' }}>
                {isStdio
                  ? 'stdio — launches a local process via command + args.'
                  : state.transport === 'sse'
                  ? 'SSE — connects to a server-sent events endpoint.'
                  : 'HTTP — connects to a streamable HTTP endpoint.'}
              </span>
            </div>

            {/* ---- stdio-only fields ---- */}
            {isStdio && (
              <>
                {/* Command */}
                <div style={fieldWrapStyle}>
                  <label htmlFor="mcp-command" style={labelStyle}>
                    Command <span aria-hidden="true" style={{ color: 'var(--hachi-danger)' }}>*</span>
                  </label>
                  <select
                    id="mcp-command"
                    value={state.command}
                    onChange={(e) =>
                      dispatch({ type: 'set_command', value: e.target.value as McpCommand })
                    }
                    disabled={state.submitting}
                    style={{
                      ...fieldInputStyle(false),
                      appearance: 'auto',
                      cursor: 'pointer',
                    }}
                  >
                    {ALLOWED_MCP_COMMANDS.map((cmd) => (
                      <option key={cmd} value={cmd}>
                        {cmd}
                      </option>
                    ))}
                  </select>
                  <span style={{ fontSize: 11, color: 'var(--hachi-muted)' }}>
                    Launcher executable. Only these commands are permitted for security.
                  </span>
                </div>

                {/* Args */}
                <div style={fieldWrapStyle}>
                  <label htmlFor="mcp-args" style={labelStyle}>
                    Args <span aria-hidden="true" style={{ color: 'var(--hachi-danger)' }}>*</span>
                  </label>
                  <input
                    id="mcp-args"
                    type="text"
                    value={state.args}
                    onChange={(e) => dispatch({ type: 'set_args', value: e.target.value })}
                    placeholder="e.g. -y @modelcontextprotocol/server-github"
                    disabled={state.submitting}
                    aria-invalid={!!state.fieldErrors.args}
                    aria-describedby={state.fieldErrors.args ? 'mcp-args-error' : undefined}
                    style={{
                      ...fieldInputStyle(!!state.fieldErrors.args),
                      fontFamily: 'var(--font-mono)',
                      fontSize: 12,
                    }}
                    autoComplete="off"
                    spellCheck={false}
                  />
                  {state.fieldErrors.args && (
                    <span id="mcp-args-error" style={errorTextStyle} role="alert">
                      {state.fieldErrors.args}
                    </span>
                  )}
                  <span style={{ fontSize: 11, color: 'var(--hachi-muted)' }}>
                    Shell metacharacters are not allowed.
                  </span>
                </div>

                {/* Env vars (optional) */}
                <div style={fieldWrapStyle}>
                  <span style={labelStyle}>Environment variables (optional)</span>
                  <span style={{ fontSize: 11, color: 'var(--hachi-muted)', marginTop: -4 }}>
                    Plain config only — secret/token/key/auth names are rejected. Use SecretStore for credentials.
                  </span>

                  {hasEnvPairs && (
                    <div
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 4,
                        background: 'var(--hachi-canvas)',
                        border: '0.5px solid var(--hachi-line)',
                        borderRadius: 8,
                        padding: '6px 10px',
                      }}
                    >
                      {Object.entries(state.envVars).map(([k, v]) => (
                        <div
                          key={k}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                            fontSize: 12,
                            fontFamily: 'var(--font-mono)',
                          }}
                        >
                          <span style={{ flex: 1, color: 'var(--hachi-ink)' }}>
                            {k}={v}
                          </span>
                          <button
                            type="button"
                            onClick={() => dispatch({ type: 'remove_env', key: k })}
                            disabled={state.submitting}
                            aria-label={`Remove env var ${k}`}
                            style={{
                              border: 'none',
                              background: 'transparent',
                              color: 'var(--hachi-muted)',
                              cursor: 'pointer',
                              padding: '2px 4px',
                              borderRadius: 4,
                              lineHeight: 1,
                              fontSize: 14,
                            }}
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  {state.fieldErrors.envVars && (
                    <span style={errorTextStyle} role="alert">
                      {state.fieldErrors.envVars}
                    </span>
                  )}

                  <div style={{ display: 'flex', gap: 6 }}>
                    <input
                      type="text"
                      value={state.envKey}
                      onChange={(e) => dispatch({ type: 'set_env_key', value: e.target.value })}
                      placeholder="KEY"
                      disabled={state.submitting}
                      aria-label="Environment variable key"
                      style={{
                        ...fieldInputStyle(false),
                        flex: 1,
                        fontFamily: 'var(--font-mono)',
                        fontSize: 12,
                      }}
                      autoComplete="off"
                      spellCheck={false}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault()
                          const result = validateMcpServerInput({
                            name: state.name,
                            transport: state.transport,
                            command: state.command,
                            args: state.args,
                            envVars: { ...state.envVars, [state.envKey]: state.envVal },
                          })
                          if (!result.ok && result.field === 'envVars') {
                            dispatch({ type: 'set_field_error', field: 'envVars', value: result.message })
                          } else {
                            dispatch({ type: 'add_env' })
                          }
                        }
                      }}
                    />
                    <input
                      type="text"
                      value={state.envVal}
                      onChange={(e) => dispatch({ type: 'set_env_val', value: e.target.value })}
                      placeholder="value"
                      disabled={state.submitting}
                      aria-label="Environment variable value"
                      style={{
                        ...fieldInputStyle(false),
                        flex: 2,
                        fontFamily: 'var(--font-mono)',
                        fontSize: 12,
                      }}
                      autoComplete="off"
                      spellCheck={false}
                    />
                    <button
                      type="button"
                      onClick={() => {
                        const result = validateMcpServerInput({
                          name: state.name,
                          transport: state.transport,
                          command: state.command,
                          args: state.args,
                          envVars: { ...state.envVars, [state.envKey]: state.envVal },
                        })
                        if (!result.ok && result.field === 'envVars') {
                          dispatch({ type: 'set_field_error', field: 'envVars', value: result.message })
                        } else {
                          dispatch({ type: 'add_env' })
                        }
                      }}
                      disabled={state.submitting || !state.envKey.trim()}
                      aria-label="Add environment variable"
                      style={{
                        height: 36,
                        padding: '0 12px',
                        borderRadius: 8,
                        border: '0.5px solid var(--hachi-line)',
                        background: 'var(--hachi-canvas)',
                        color: 'var(--hachi-trust)',
                        font: '500 12px var(--font-body)',
                        cursor: state.submitting || !state.envKey.trim() ? 'not-allowed' : 'pointer',
                        opacity: !state.envKey.trim() ? 0.5 : 1,
                        flexShrink: 0,
                      }}
                    >
                      Add
                    </button>
                  </div>
                </div>
              </>
            )}

            {/* ---- sse/http-only fields ---- */}
            {!isStdio && (
              <>
                {/* URL */}
                <div style={fieldWrapStyle}>
                  <label htmlFor="mcp-url" style={labelStyle}>
                    URL <span aria-hidden="true" style={{ color: 'var(--hachi-danger)' }}>*</span>
                  </label>
                  <input
                    id="mcp-url"
                    type="url"
                    value={state.url}
                    onChange={(e) => dispatch({ type: 'set_url', value: e.target.value })}
                    placeholder={
                      state.transport === 'sse'
                        ? 'e.g. https://mcp.example.com/sse'
                        : 'e.g. https://mcp.example.com/mcp'
                    }
                    disabled={state.submitting}
                    aria-invalid={!!state.fieldErrors.url}
                    aria-describedby={state.fieldErrors.url ? 'mcp-url-error' : undefined}
                    style={{
                      ...fieldInputStyle(!!state.fieldErrors.url),
                      fontFamily: 'var(--font-mono)',
                      fontSize: 12,
                    }}
                    autoComplete="off"
                    spellCheck={false}
                  />
                  {state.fieldErrors.url && (
                    <span id="mcp-url-error" style={errorTextStyle} role="alert">
                      {state.fieldErrors.url}
                    </span>
                  )}
                </div>

                {/* Optional headers */}
                <div style={fieldWrapStyle}>
                  <span style={labelStyle}>Request headers (optional)</span>
                  <span style={{ fontSize: 11, color: 'var(--hachi-muted)', marginTop: -4 }}>
                    e.g. Authorization header for private endpoints.
                  </span>

                  {hasHeaderPairs && (
                    <div
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 4,
                        background: 'var(--hachi-canvas)',
                        border: '0.5px solid var(--hachi-line)',
                        borderRadius: 8,
                        padding: '6px 10px',
                      }}
                    >
                      {Object.entries(state.headers).map(([k, v]) => (
                        <div
                          key={k}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                            fontSize: 12,
                            fontFamily: 'var(--font-mono)',
                          }}
                        >
                          <span style={{ flex: 1, color: 'var(--hachi-ink)' }}>
                            {k}: {v}
                          </span>
                          <button
                            type="button"
                            onClick={() => dispatch({ type: 'remove_header', key: k })}
                            disabled={state.submitting}
                            aria-label={`Remove header ${k}`}
                            style={{
                              border: 'none',
                              background: 'transparent',
                              color: 'var(--hachi-muted)',
                              cursor: 'pointer',
                              padding: '2px 4px',
                              borderRadius: 4,
                              lineHeight: 1,
                              fontSize: 14,
                            }}
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  <div style={{ display: 'flex', gap: 6 }}>
                    <input
                      type="text"
                      value={state.headerKey}
                      onChange={(e) => dispatch({ type: 'set_header_key', value: e.target.value })}
                      placeholder="Header-Name"
                      disabled={state.submitting}
                      aria-label="Request header name"
                      style={{
                        ...fieldInputStyle(false),
                        flex: 1,
                        fontFamily: 'var(--font-mono)',
                        fontSize: 12,
                      }}
                      autoComplete="off"
                      spellCheck={false}
                    />
                    <input
                      type="text"
                      value={state.headerVal}
                      onChange={(e) => dispatch({ type: 'set_header_val', value: e.target.value })}
                      placeholder="value"
                      disabled={state.submitting}
                      aria-label="Request header value"
                      style={{
                        ...fieldInputStyle(false),
                        flex: 2,
                        fontFamily: 'var(--font-mono)',
                        fontSize: 12,
                      }}
                      autoComplete="off"
                      spellCheck={false}
                    />
                    <button
                      type="button"
                      onClick={() => dispatch({ type: 'add_header' })}
                      disabled={state.submitting || !state.headerKey.trim()}
                      aria-label="Add header"
                      style={{
                        height: 36,
                        padding: '0 12px',
                        borderRadius: 8,
                        border: '0.5px solid var(--hachi-line)',
                        background: 'var(--hachi-canvas)',
                        color: 'var(--hachi-trust)',
                        font: '500 12px var(--font-body)',
                        cursor: state.submitting || !state.headerKey.trim() ? 'not-allowed' : 'pointer',
                        opacity: !state.headerKey.trim() ? 0.5 : 1,
                        flexShrink: 0,
                      }}
                    >
                      Add
                    </button>
                  </div>
                </div>
              </>
            )}

            {/* Submit error */}
            {state.submitError && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  fontSize: 12.5,
                  color: 'var(--hachi-danger)',
                  background: 'var(--hachi-danger-tint)',
                  padding: '10px 12px',
                  borderRadius: 8,
                }}
                role="alert"
              >
                <Icon name="alert" size={14} style={{ marginTop: 1, flexShrink: 0 }} />
                {state.submitError}
              </div>
            )}
          </div>

          {/* Actions */}
          <div
            style={{
              display: 'flex',
              gap: 10,
              padding: '0 22px 22px',
            }}
          >
            <CancelBtn onClick={onClose} disabled={state.submitting} />
            <SubmitBtn loading={state.submitting} />
          </div>
        </form>
      </div>
    </div>
  )
}

function CancelBtn({ onClick, disabled }: { onClick: () => void; disabled?: boolean }) {
  const [hovered, setHovered] = useState(false)
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        flex: 1,
        height: 42,
        borderRadius: 10,
        border: '1px solid var(--hachi-trust)',
        background: hovered && !disabled ? 'var(--hachi-trust-tint)' : 'transparent',
        color: 'var(--hachi-trust)',
        font: '500 14px var(--font-body)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.65 : 1,
        transition: 'background var(--dur-fast) var(--ease)',
      }}
    >
      Cancel
    </button>
  )
}

function SubmitBtn({ loading }: { loading: boolean }) {
  const [hovered, setHovered] = useState(false)
  return (
    <button
      type="submit"
      disabled={loading}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      aria-busy={loading}
      style={{
        flex: 1,
        height: 42,
        borderRadius: 10,
        border: '0.5px solid transparent',
        background: hovered && !loading ? 'var(--hachi-brand-hover)' : 'var(--hachi-brand)',
        color: 'var(--hachi-canvas)',
        font: '500 14px var(--font-body)',
        cursor: loading ? 'not-allowed' : 'pointer',
        opacity: loading ? 0.75 : 1,
        transition: 'background var(--dur-fast) var(--ease)',
      }}
    >
      {loading ? 'Registering…' : 'Register server'}
    </button>
  )
}

// ---- Add MCP affordance -------------------------------------------------

interface AddMcpRowProps {
  onAdd: () => void
}

function AddMcpRow({ onAdd }: AddMcpRowProps) {
  const [hover, setHover] = useState(false)
  return (
    <button
      type="button"
      onClick={onAdd}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-label="Add MCP server"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '11px 16px',
        borderWidth: '0.5px 0 0 0',
        borderStyle: 'solid',
        borderColor: 'var(--hachi-line)',
        cursor: 'pointer',
        background: hover ? 'var(--hachi-cream)' : 'transparent',
        transition: 'background var(--dur-fast) var(--ease)',
        color: 'var(--hachi-trust)',
        width: '100%',
        textAlign: 'left',
        font: '500 13px var(--font-body)',
      }}
    >
      <Icon name="plus" size={15} style={{ color: 'var(--hachi-trust)' }} />
      Add MCP server
    </button>
  )
}

// ---- Integration card wrapper with action status ------------------------

interface IntCardWithStatusProps {
  integration: Integration
}

function IntCardWithStatus({ integration: i }: IntCardWithStatusProps) {
  const [, forceUpdate] = useReducer((x: number) => x + 1, 0)

  useEffect(() => {
    return subscribeActionStatus(forceUpdate)
  }, [])

  const connectStatus = getConnectStatus(i.id)
  const disconnectStatus = getDisconnectStatus(i.id)

  // Extract auth_type from the extended shape if present.
  const ext = i as Partial<IntegrationExtended>
  const authType = ext.auth_type

  async function handleConnect() {
    await connectIntegration(i.id, authType)
  }

  async function handleDisconnect() {
    await disconnectIntegration(i.id, authType)
  }

  return (
    <IntCard
      integration={i}
      onConnect={handleConnect}
      onDisconnect={handleDisconnect}
      connectLoading={connectStatus === 'loading'}
      connectError={connectStatus === 'error'}
      disconnectLoading={disconnectStatus === 'loading'}
      disconnectError={disconnectStatus === 'error'}
    />
  )
}

// ---- Main screen --------------------------------------------------------

function Integrations() {
  const integrations = useStore((s) => s.integrations)
  const mcpServers = useStore((s) => s.mcpServers)
  const mcpTools = useStore((s) => s.mcpTools)
  const operator = useStore((s) => s.operator)
  const [showAddMcp, setShowAddMcp] = useState(false)

  useEffect(() => {
    void loadIntegrations()
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* page header */}
      <div
        className="hachi-int-header"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '0 32px',
          height: 68,
          flexShrink: 0,
          borderBottom: '0.5px solid var(--hachi-line)',
        }}
      >
        <h1
          style={{
            margin: 0,
            fontFamily: 'var(--font-display)',
            fontWeight: 500,
            fontSize: 22,
            lineHeight: '26px',
          }}
        >
          Integrations
        </h1>
        <span style={{ fontSize: 13, color: 'var(--hachi-muted)' }}>
          what Hachi can reach
        </span>
      </div>

      {/* scrollable body */}
      <div
        className="hachi-scroll hachi-int-body"
        style={{ flex: 1, overflow: 'auto', padding: '24px 32px 64px' }}
      >
        <div
          style={{
            maxWidth: 820,
            margin: '0 auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 24,
          }}
        >
          {/* ---- Your accounts ---- */}
          <div>
            <h2
              style={{
                margin: '0 0 12px',
                fontFamily: 'var(--font-display)',
                fontWeight: 500,
                fontSize: 15,
                color: 'var(--hachi-muted)',
              }}
            >
              Your accounts
            </h2>
            {integrations.length === 0 ? (
              <div
                style={{
                  background: 'var(--hachi-surface)',
                  border: '0.5px solid var(--hachi-line)',
                  borderRadius: 16,
                }}
              >
                <NoIntegrations />
              </div>
            ) : (
              <div
                className="hachi-int-grid"
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: 12,
                }}
              >
                {integrations.map((i) => (
                  <IntCardWithStatus key={i.id} integration={i} />
                ))}
              </div>
            )}
          </div>

          {/* ---- MCP servers ---- */}
          <div>
            <h2
              style={{
                margin: '0 0 12px',
                fontFamily: 'var(--font-display)',
                fontWeight: 500,
                fontSize: 15,
                color: 'var(--hachi-muted)',
              }}
            >
              MCP servers — tools Hachi can call
            </h2>
            <div
              style={{
                background: 'var(--hachi-surface)',
                border: '0.5px solid var(--hachi-line)',
                borderRadius: 16,
                overflow: 'hidden',
              }}
            >
              {/* table header */}
              <div
                className="hachi-mcp-table-header"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 13,
                  padding: '11px 16px',
                  borderBottom: '0.5px solid var(--hachi-line)',
                  fontSize: 11.5,
                  color: 'var(--hachi-muted)',
                }}
              >
                <span style={{ flex: 1, minWidth: 0 }}>Server</span>
                <span style={{ width: 96, flexShrink: 0 }}>Status</span>
                <span style={{ width: 52, flexShrink: 0, textAlign: 'right' }}>
                  Tools
                </span>
                <span style={{ width: 64, flexShrink: 0 }}>Transport</span>
                <span style={{ width: 64, flexShrink: 0, textAlign: 'right' }}>
                  Uptime
                </span>
              </div>

              {mcpServers.length === 0 ? (
                <NoMcpServers />
              ) : (
                mcpServers.map((s, idx) => (
                  <McpServerRow
                    key={s.name}
                    server={s}
                    isLast={idx === mcpServers.length - 1}
                  />
                ))
              )}

              {/* add MCP affordance */}
              <AddMcpRow onAdd={() => setShowAddMcp(true)} />
            </div>
          </div>

          {/* ---- Tool inventory (operator only) ---- */}
          {operator && (
            <div>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 9,
                  marginBottom: 12,
                }}
              >
                <h2
                  style={{
                    margin: 0,
                    fontFamily: 'var(--font-display)',
                    fontWeight: 500,
                    fontSize: 15,
                    color: 'var(--hachi-muted)',
                  }}
                >
                  Tool inventory
                </h2>
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
                  operator
                </span>
              </div>
              <div
                style={{
                  background: 'var(--hachi-surface)',
                  border: '0.5px solid var(--hachi-line)',
                  borderRadius: 16,
                  padding: '6px 18px',
                }}
              >
                {mcpTools.length === 0 ? (
                  <div
                    style={{
                      padding: '24px 0',
                      fontSize: 13,
                      color: 'var(--hachi-muted)',
                      textAlign: 'center',
                    }}
                  >
                    No tools in inventory.
                  </div>
                ) : (
                  mcpTools.map((t, idx) => (
                    <McpToolRow
                      key={t.name}
                      tool={t}
                      isLast={idx === mcpTools.length - 1}
                    />
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Add MCP dialog (rendered at root level to escape overflow:hidden) */}
      {showAddMcp && <AddMcpDialog onClose={() => setShowAddMcp(false)} />}
    </div>
  )
}

export const Route = createFileRoute('/integrations')({
  component: Integrations,
})
