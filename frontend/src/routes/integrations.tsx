// ============================================================
// Integrations screen — /integrations
// Shows: connected accounts/tools w/ health/status, MCP servers,
// tool inventory (operator-only), add-MCP affordance.
// ============================================================
import React, { useEffect, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useStore } from '../lib/store/store'
import { Icon } from '../components/icons/Icon'
import type { Integration, McpServer, McpTool } from '../lib/data/types'
import {
  loadIntegrations,
  connectIntegration,
  disconnectIntegration,
} from '../lib/data/domains/integrations'

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
}

function IntCard({ integration: i, onConnect, onDisconnect }: IntCardProps) {
  const sm = getIntMeta(i.status)
  const connected = i.status === 'connected'
  const primaryLabel =
    i.status === 'connected'
      ? 'Re-authorise'
      : i.status === 'degraded'
      ? 'Fix access'
      : 'Connect'
  const primaryFg =
    i.status === 'none' ? 'var(--hachi-trust)' : 'var(--hachi-ink)'

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
          <Icon name={i.icon} size={20} />
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

      {/* action buttons */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={onConnect}
          onMouseEnter={() => setPrimaryHover(true)}
          onMouseLeave={() => setPrimaryHover(false)}
          style={{
            flex: 1,
            height: 34,
            borderRadius: 10,
            border: '0.5px solid var(--hachi-line)',
            background: primaryHover ? 'var(--hachi-cream)' : 'var(--hachi-canvas)',
            color: primaryFg,
            font: '500 13px var(--font-body)',
            cursor: 'pointer',
            transition: 'background var(--dur-fast) var(--ease)',
          }}
        >
          {primaryLabel}
        </button>
        {connected && (
          <button
            onClick={onDisconnect}
            onMouseEnter={() => setDisconnectHover(true)}
            onMouseLeave={() => setDisconnectHover(false)}
            style={{
              height: 34,
              padding: '0 14px',
              borderRadius: 10,
              border: 'none',
              background: disconnectHover ? 'var(--hachi-cream)' : 'transparent',
              color: 'var(--hachi-muted)',
              font: '500 13px var(--font-body)',
              cursor: 'pointer',
              transition: 'background var(--dur-fast) var(--ease)',
            }}
          >
            Disconnect
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

// ---- Add MCP affordance -------------------------------------------------

function AddMcpRow() {
  const [hover, setHover] = useState(false)
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '11px 16px',
        borderTop: '0.5px solid var(--hachi-line)',
        cursor: 'pointer',
        background: hover ? 'var(--hachi-cream)' : 'transparent',
        transition: 'background var(--dur-fast) var(--ease)',
        color: 'var(--hachi-trust)',
        fontSize: 13,
        fontWeight: 500,
      }}
    >
      <Icon name="plus" size={15} style={{ color: 'var(--hachi-trust)' }} />
      Add MCP server
    </div>
  )
}

// ---- Main screen --------------------------------------------------------

function Integrations() {
  const integrations = useStore((s) => s.integrations)
  const mcpServers = useStore((s) => s.mcpServers)
  const mcpTools = useStore((s) => s.mcpTools)
  const operator = useStore((s) => s.operator)

  // Refresh integrations + MCP inventory whenever this route is visited.
  // bootstrapLiveData() in __root.tsx runs loadIntegrations() on first app mount;
  // this useEffect handles subsequent navigations (loaders are idempotent).
  useEffect(() => {
    void loadIntegrations()
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* page header */}
      <div
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
        className="hachi-scroll"
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
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: 12,
                }}
              >
                {integrations.map((i) => (
                  <IntCard
                    key={i.id}
                    integration={i}
                    onConnect={() => connectIntegration(i.id)}
                    onDisconnect={() => disconnectIntegration(i.id)}
                  />
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
              <AddMcpRow />
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
    </div>
  )
}

export const Route = createFileRoute('/integrations')({
  component: Integrations,
})
