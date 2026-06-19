import React, { useEffect, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useStore } from '../lib/store/store'
import { Icon } from '../components/icons/Icon'
import type { OgNode } from '../lib/data/types'
import { loadOg } from '../lib/data/domains/og'

// ── palette constants ─────────────────────────────────────────────────────────
const OG_KIND_ICON: Record<string, string> = {
  root: 'brain',
  skill: 'network',
  leaf: 'file',
}

const OG_COLOR: Record<string, string> = {
  root: 'var(--hachi-trust)',
  skill: '#B5894E',
  leaf: '#8497B8',
}

// ── graph geometry ────────────────────────────────────────────────────────────
const GRAPH_H = 380
const COL_X: Record<string, number> = { root: 86, skill: 300, leaf: 514 }

function computePositions(nodes: OgNode[]): Record<string, { x: number; y: number }> {
  const byCol: Record<string, OgNode[]> = { root: [], skill: [], leaf: [] }
  nodes.forEach((n) => byCol[n.kind]?.push(n))
  const pos: Record<string, { x: number; y: number }> = {};
  (['root', 'skill', 'leaf'] as const).forEach((k) => {
    const list = byCol[k]
    const gap = GRAPH_H / (list.length + 1)
    list.forEach((n, i) => {
      pos[n.id] = { x: COL_X[k], y: gap * (i + 1) }
    })
  })
  return pos
}

function rOf(n: OgNode, maxFull: number): number {
  return 13 + (n.full / maxFull) * 14
}

// ── sub-components ────────────────────────────────────────────────────────────

interface StatCardProps {
  icon: string
  label: string
  value: string | number
  valueColor?: string
}

function StatCard({ icon, label, value, valueColor }: StatCardProps) {
  return (
    <div
      style={{
        background: 'var(--hachi-surface)',
        border: '0.5px solid var(--hachi-line)',
        borderRadius: 14,
        padding: 15,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 7,
          fontSize: 12,
          color: 'var(--hachi-muted)',
          marginBottom: 9,
        }}
      >
        <Icon name={icon} size={14} />
        {label}
      </div>
      <div
        style={{
          fontFamily: 'var(--font-display)',
          fontWeight: 500,
          fontSize: 24,
          fontVariantNumeric: 'tabular-nums',
          color: valueColor ?? 'var(--hachi-ink)',
        }}
      >
        {value}
      </div>
    </div>
  )
}

// ── graph SVG ─────────────────────────────────────────────────────────────────
interface GraphPanelProps {
  nodes: OgNode[]
  ogSel: string
  ogHover: string | null
  setOgSel: (id: string) => void
  setOgHover: (id: string | null) => void
}

function GraphPanel({ nodes, ogSel, ogHover, setOgSel, setOgHover }: GraphPanelProps) {
  const pos = computePositions(nodes)
  const maxFull = Math.max(...nodes.map((n) => n.full))
  const nodeById = (id: string) => nodes.find((n) => n.id === id)

  // build edge list
  const edges: [string, string][] = []
  nodes.forEach((n) =>
    (n.deps ?? []).forEach((d) => {
      if (pos[d]) edges.push([n.id, d])
    })
  )

  const active = ogHover ?? ogSel

  const isLit = (id: string): boolean => {
    if (!ogHover && !ogSel) return true
    return (
      id === active ||
      edges.some(([a, b]) => (a === active && b === id) || (b === active && a === id))
    )
  }

  return (
    <div style={{ padding: '8px 8px 0' }}>
      <svg
        viewBox="0 0 600 400"
        style={{ width: '100%', height: 'auto', display: 'block' }}
      >
        <defs>
          <marker
            id="ogarrow"
            markerWidth={7}
            markerHeight={7}
            refX={6}
            refY={3.5}
            orient="auto"
          >
            <path d="M0 0 L7 3.5 L0 7 z" fill="var(--hachi-line)" />
          </marker>
          <marker
            id="ogarrowOn"
            markerWidth={7.5}
            markerHeight={7.5}
            refX={6}
            refY={3.5}
            orient="auto"
          >
            <path d="M0 0 L7 3.5 L0 7 z" fill="var(--hachi-trust)" />
          </marker>
        </defs>

        {/* edges */}
        {edges.map(([a, b], i) => {
          const pa = pos[a]
          const pb = pos[b]
          const na = nodeById(a)
          const nb = nodeById(b)
          if (!pa || !pb || !na || !nb) return null
          const ra = rOf(na, maxFull)
          const rb = rOf(nb, maxFull)
          const x1 = pa.x + ra
          const x2 = pb.x - rb
          const mx = (x1 + x2) / 2
          const act = active && (a === active || b === active)
          const d = `M ${x1} ${pa.y} C ${mx} ${pa.y}, ${mx} ${pb.y}, ${x2} ${pb.y}`
          return (
            <path
              key={'e' + i}
              d={d}
              fill="none"
              stroke={act ? 'var(--hachi-trust)' : 'var(--hachi-line)'}
              strokeWidth={act ? 2.2 : 1.3}
              opacity={active && !act ? 0.22 : 0.9}
              strokeLinecap="round"
              strokeDasharray={act ? '5 4' : '0'}
              markerEnd={act ? 'url(#ogarrowOn)' : 'url(#ogarrow)'}
              style={act ? { animation: 'ogflow .7s linear infinite' } : undefined}
            />
          )
        })}

        {/* nodes */}
        {nodes.map((n) => {
          const p = pos[n.id]
          if (!p) return null
          const r = rOf(n, maxFull)
          const sel = ogSel === n.id
          const lit = isLit(n.id)
          const color = OG_COLOR[n.kind] ?? 'var(--hachi-muted)'
          const nameY = p.y + r + 13

          return (
            <g
              key={n.id}
              style={{ cursor: 'pointer' }}
              onClick={() => setOgSel(n.id)}
              onMouseEnter={() => setOgHover(n.id)}
              onMouseLeave={() => setOgHover(null)}
            >
              {/* selection glow ring */}
              <circle
                cx={p.x}
                cy={p.y}
                r={r + 6}
                fill={color}
                opacity={sel ? 0.18 : 0}
              />
              {/* node circle */}
              <circle
                cx={p.x}
                cy={p.y}
                r={r}
                fill="var(--hachi-surface)"
                stroke={color}
                strokeWidth={sel ? 2.5 : 1.6}
                opacity={lit ? 1 : 0.35}
                style={
                  sel
                    ? { filter: `drop-shadow(0 0 7px ${color})` }
                    : undefined
                }
              />
              {/* index token count */}
              <text
                x={p.x}
                y={p.y + 4}
                textAnchor="middle"
                fontSize={10}
                fontWeight={500}
                fill={color}
                opacity={lit ? 1 : 0.4}
                style={{ fontFamily: 'var(--font-mono)' }}
              >
                {n.index}
              </text>
              {/* label below node */}
              <text
                x={p.x}
                y={nameY}
                textAnchor="middle"
                fontSize={10.5}
                fill="var(--hachi-ink)"
                opacity={lit ? 1 : 0.4}
                style={{ fontFamily: 'var(--font-body)' }}
              >
                {n.name.replace('.og', '')}
              </text>
            </g>
          )
        })}
      </svg>

      {/* legend */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '10px 10px 12px',
          fontSize: 11.5,
          color: 'var(--hachi-muted)',
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              width: 9,
              height: 9,
              borderRadius: '50%',
              background: 'var(--hachi-trust)',
              display: 'inline-block',
            }}
          />
          root (brain)
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              width: 9,
              height: 9,
              borderRadius: '50%',
              background: '#B5894E',
              display: 'inline-block',
            }}
          />
          skill
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              width: 9,
              height: 9,
              borderRadius: '50%',
              background: '#8497B8',
              display: 'inline-block',
            }}
          />
          leaf
        </span>
        <span style={{ marginLeft: 'auto' }}>label = index-pass tokens · size = full size</span>
      </div>
    </div>
  )
}

// ── tree view ─────────────────────────────────────────────────────────────────
interface TreePanelProps {
  nodes: OgNode[]
  ogSel: string
  setOgSel: (id: string) => void
}

function TreePanel({ nodes, ogSel, setOgSel }: TreePanelProps) {
  const [hovered, setHovered] = useState<string | null>(null)

  const depthOf = (kind: string) =>
    kind === 'root' ? 0 : kind === 'skill' ? 1 : 2

  return (
    <div style={{ padding: 12 }}>
      {nodes.map((n) => {
        const isHov = hovered === n.id
        const isSel = ogSel === n.id
        const bg = isSel
          ? 'var(--hachi-cream)'
          : isHov
          ? 'var(--hachi-cream)'
          : 'transparent'

        return (
          <div
            key={n.id}
            onClick={() => setOgSel(n.id)}
            onMouseEnter={() => setHovered(n.id)}
            onMouseLeave={() => setHovered(null)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              marginLeft: depthOf(n.kind) * 16,
              padding: '8px 14px',
              cursor: 'pointer',
              borderRadius: 8,
              background: bg,
              transition: 'background var(--dur-fast) var(--ease)',
            }}
          >
            <span
              style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}
            >
              <Icon name={OG_KIND_ICON[n.kind] ?? 'file'} size={13} />
            </span>
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 12.5,
                flex: 1,
              }}
            >
              {n.name}
            </span>
            <span style={{ fontSize: 12, color: 'var(--hachi-muted)' }}>
              {n.full}t
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ── detail panel ──────────────────────────────────────────────────────────────
interface DetailPanelProps {
  dn: OgNode
  nodes: OgNode[]
  setOgSel: (id: string) => void
}

function DetailPanel({ dn, nodes, setOgSel }: DetailPanelProps) {
  const [hovDep, setHovDep] = useState<string | null>(null)
  const [hovUsed, setHovUsed] = useState<string | null>(null)

  const savedPct = Math.round((1 - dn.index / dn.full) * 100)
  const barWidth = Math.round((dn.index / dn.full) * 100)

  const nodeById = (id: string) => nodes.find((n) => n.id === id)

  const deps = (dn.deps ?? [])
    .map((d) => nodeById(d))
    .filter((d): d is OgNode => Boolean(d))

  const usedBy = nodes.filter((n) => (n.deps ?? []).includes(dn.id))

  const kindColor = OG_COLOR[dn.kind] ?? 'var(--hachi-muted)'
  const kindIcon = OG_KIND_ICON[dn.kind] ?? 'file'

  return (
    <>
      {/* three-pass card */}
      <div
        style={{
          background: 'var(--hachi-surface)',
          border: '0.5px solid var(--hachi-line)',
          borderRadius: 16,
          padding: 18,
        }}
      >
        {/* header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            marginBottom: 16,
          }}
        >
          <span
            style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}
          >
            <Icon name={kindIcon} size={16} />
          </span>
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontWeight: 500,
              fontSize: 14,
              flex: 1,
            }}
          >
            {dn.name}
          </span>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 10px',
              borderRadius: 999,
              background: 'var(--hachi-cream)',
              color: kindColor,
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: kindColor,
                display: 'inline-block',
              }}
            />
            {dn.kind}
          </span>
        </div>

        <div
          style={{
            fontSize: 13,
            color: 'var(--hachi-muted)',
            marginBottom: 12,
          }}
        >
          Three-pass read
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
          {/* pass 1 — index */}
          <div
            style={{
              border: '0.5px solid var(--hachi-brand)',
              borderRadius: 10,
              padding: '12px 13px',
              background: 'var(--hachi-brand-tint)',
            }}
          >
            <div
              style={{
                fontSize: 12,
                color: 'var(--hachi-muted)',
                marginBottom: 3,
              }}
            >
              1 · Index
            </div>
            <div
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 500,
                fontSize: 21,
                color: 'var(--hachi-brand)',
              }}
            >
              {dn.index}
              <span
                style={{
                  fontSize: 12,
                  color: 'var(--hachi-muted)',
                  fontWeight: 400,
                }}
              >
                {' '}
                tok
              </span>
            </div>
            <div
              style={{
                fontSize: 12.5,
                color: 'var(--hachi-muted)',
                marginTop: 4,
              }}
            >
              Signature + summary. Loaded first, for routing.
            </div>
          </div>

          {/* pass 2 — dense */}
          <div
            style={{
              border: '0.5px solid var(--hachi-line)',
              borderRadius: 10,
              padding: '12px 13px',
              background: 'var(--hachi-canvas)',
            }}
          >
            <div
              style={{
                fontSize: 12,
                color: 'var(--hachi-muted)',
                marginBottom: 3,
              }}
            >
              2 · Dense
            </div>
            <div
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 500,
                fontSize: 21,
                color: 'var(--hachi-working)',
              }}
            >
              {dn.dense}
              <span
                style={{
                  fontSize: 12,
                  color: 'var(--hachi-muted)',
                  fontWeight: 400,
                }}
              >
                {' '}
                tok
              </span>
            </div>
            <div
              style={{
                fontSize: 12.5,
                color: 'var(--hachi-muted)',
                marginTop: 4,
              }}
            >
              Compressed body. Loaded when the node is selected.
            </div>
          </div>

          {/* pass 3 — full */}
          <div
            style={{
              border: '0.5px solid var(--hachi-line)',
              borderRadius: 10,
              padding: '12px 13px',
              background: 'var(--hachi-canvas)',
            }}
          >
            <div
              style={{
                fontSize: 12,
                color: 'var(--hachi-muted)',
                marginBottom: 3,
              }}
            >
              3 · Full
            </div>
            <div
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 500,
                fontSize: 21,
                color: 'var(--hachi-ink)',
              }}
            >
              {dn.full}
              <span
                style={{
                  fontSize: 12,
                  color: 'var(--hachi-muted)',
                  fontWeight: 400,
                }}
              >
                {' '}
                tok
              </span>
            </div>
            <div
              style={{
                fontSize: 12.5,
                color: 'var(--hachi-muted)',
                marginTop: 4,
              }}
            >
              Complete source. Loaded only if the task drills in.
            </div>
          </div>
        </div>

        {/* savings bar */}
        <div style={{ marginTop: 18 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 12,
              marginBottom: 7,
            }}
          >
            <span style={{ color: 'var(--hachi-muted)' }}>
              Index-pass vs the whole node
            </span>
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                color: 'var(--hachi-success)',
              }}
            >
              {'−'}{savedPct}% tokens
            </span>
          </div>
          <div
            style={{
              position: 'relative',
              height: 30,
              borderRadius: 6,
              background: 'var(--hachi-line)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'flex-end',
                paddingRight: 10,
              }}
            >
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 11,
                  color: 'var(--hachi-muted)',
                }}
              >
                vs full: {dn.full} tok
              </span>
            </div>
            <div
              style={{
                width: `${barWidth}%`,
                height: '100%',
                background: 'var(--hachi-brand)',
                display: 'grid',
                placeItems: 'center',
                minWidth: 52,
              }}
            >
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 11,
                  color: 'var(--hachi-canvas)',
                  fontWeight: 500,
                }}
              >
                {dn.index}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* connections card */}
      <div
        style={{
          background: 'var(--hachi-surface)',
          border: '0.5px solid var(--hachi-line)',
          borderRadius: 16,
          padding: 18,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            marginBottom: 14,
          }}
        >
          <span
            style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}
          >
            <Icon name="network" size={16} />
          </span>
          <span style={{ fontWeight: 500, fontSize: 14 }}>Connections</span>
        </div>

        <div
          style={{
            fontSize: 12,
            color: 'var(--hachi-muted)',
            marginBottom: 9,
          }}
        >
          Depends on
        </div>

        {deps.length > 0 ? (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {deps.map((d) => (
              <button
                key={d.id}
                onClick={() => setOgSel(d.id)}
                onMouseEnter={() => setHovDep(d.id)}
                onMouseLeave={() => setHovDep(null)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 7,
                  height: 30,
                  padding: '0 11px',
                  borderRadius: 8,
                  border: '0.5px solid var(--hachi-line)',
                  background:
                    hovDep === d.id
                      ? 'var(--hachi-cream)'
                      : 'var(--hachi-canvas)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                  color: 'var(--hachi-ink)',
                  cursor: 'pointer',
                  transition: 'background var(--dur-fast) var(--ease)',
                }}
              >
                <Icon name="network" size={12} />
                {d.name}{' '}
                <span style={{ color: 'var(--hachi-muted)' }}>{d.index}t</span>
              </button>
            ))}
          </div>
        ) : (
          <div style={{ fontSize: 13, color: 'var(--hachi-muted)' }}>
            Leaf node — no dependencies. Cheapest to load.
          </div>
        )}

        {usedBy.length > 0 && (
          <>
            <div
              style={{
                fontSize: 12,
                color: 'var(--hachi-muted)',
                margin: '14px 0 9px',
              }}
            >
              Used by
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {usedBy.map((u) => (
                <button
                  key={u.id}
                  onClick={() => setOgSel(u.id)}
                  onMouseEnter={() => setHovUsed(u.id)}
                  onMouseLeave={() => setHovUsed(null)}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 7,
                    height: 30,
                    padding: '0 11px',
                    borderRadius: 8,
                    border: '0.5px solid var(--hachi-line)',
                    background:
                      hovUsed === u.id
                        ? 'var(--hachi-cream)'
                        : 'var(--hachi-canvas)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 12,
                    color: 'var(--hachi-ink)',
                    cursor: 'pointer',
                    transition: 'background var(--dur-fast) var(--ease)',
                  }}
                >
                  <Icon name="network" size={12} />
                  {u.name}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </>
  )
}

// ── main screen component ─────────────────────────────────────────────────────
function OgExplorer() {
  const ogNodes = useStore((s) => s.ogNodes)
  const ogSel = useStore((s) => s.ogSel)
  const ogView = useStore((s) => s.ogView)
  const setOgSel = useStore((s) => s.setOgSel)
  const setOgView = useStore((s) => s.setOgView)

  // fetch live data on mount (idempotent; bootstrapLiveData also fires on app mount)
  useEffect(() => { void loadOg() }, [])

  // hover is screen-local (store.setOgHover is a no-op shim per contract)
  const [ogHover, setOgHover] = useState<string | null>(null)

  // computed stats
  const totalFull = ogNodes.reduce((a, n) => a + n.full, 0)
  const totalIndex = ogNodes.reduce((a, n) => a + n.index, 0)
  const ogStats = {
    nodes: ogNodes.length,
    fullK: (totalFull / 1000).toFixed(1) + 'K',
    indexCost: totalIndex,
    avoidedPct:
      Math.round((1 - totalIndex / totalFull) * 100) + '%',
  }

  // selected node (fallback to first)
  const dn = ogNodes.find((n) => n.id === ogSel) ?? ogNodes[0]

  const isGraph = ogView === 'graph'
  const isTree = ogView === 'tree'

  const tabBtnStyle = (active: boolean): React.CSSProperties => ({
    height: 28,
    padding: '0 13px',
    border: 'none',
    borderRadius: 6,
    font: '500 12px var(--font-body)',
    cursor: 'pointer',
    background: active ? 'var(--hachi-surface)' : 'transparent',
    color: active ? 'var(--hachi-ink)' : 'var(--hachi-muted)',
    transition: 'background var(--dur-fast) var(--ease)',
  })

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
      }}
    >
      {/* header */}
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
          OG explorer
        </h1>
        <span
          style={{ fontSize: 13, color: 'var(--hachi-muted)' }}
        >
          how Hachi reads only what a task needs
        </span>
      </div>

      {/* scrollable body */}
      <div
        className="hachi-scroll"
        style={{ flex: 1, overflow: 'auto', padding: '24px 32px 64px' }}
      >
        <div
          style={{
            maxWidth: 1080,
            margin: '0 auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 18,
          }}
        >
          {/* description */}
          <div
            style={{
              fontSize: 14,
              color: 'var(--hachi-muted)',
              lineHeight: '22px',
              maxWidth: 760,
            }}
          >
            The .og library is an object graph, read in three passes — index, then
            dense, then full — so Hachi loads only what a task needs instead of
            whole files.
          </div>

          {/* stat cards */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(4, 1fr)',
              gap: 12,
            }}
          >
            <StatCard
              icon="network"
              label="Nodes"
              value={ogStats.nodes}
            />
            <StatCard
              icon="hash"
              label="If fully loaded"
              value={ogStats.fullK}
            />
            <StatCard
              icon="zap"
              label="Index-pass cost"
              value={ogStats.indexCost}
              valueColor="var(--hachi-trust)"
            />
            <StatCard
              icon="sparkles"
              label="Tokens avoided"
              value={ogStats.avoidedPct}
              valueColor="var(--hachi-success)"
            />
          </div>

          {/* main two-column area */}
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 16,
              alignItems: 'start',
            }}
          >
            {/* left: graph / tree panel */}
            <div
              style={{
                flex: '1 1 440px',
                minWidth: 0,
                background: 'var(--hachi-surface)',
                border: '0.5px solid var(--hachi-line)',
                borderRadius: 16,
                overflow: 'hidden',
              }}
            >
              {/* panel header */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '14px 16px',
                  borderBottom: '0.5px solid var(--hachi-line)',
                }}
              >
                <span
                  style={{
                    color: 'var(--hachi-muted)',
                    display: 'inline-flex',
                  }}
                >
                  <Icon name="network" size={16} />
                </span>
                <span
                  style={{ fontWeight: 500, fontSize: 14, flex: 1 }}
                >
                  Object graph
                </span>
                {/* view toggle */}
                <div
                  style={{
                    display: 'inline-flex',
                    background: 'var(--hachi-cream)',
                    borderRadius: 8,
                    padding: 2,
                  }}
                >
                  <button
                    onClick={() => setOgView('graph')}
                    style={tabBtnStyle(isGraph)}
                  >
                    Graph
                  </button>
                  <button
                    onClick={() => setOgView('tree')}
                    style={tabBtnStyle(isTree)}
                  >
                    Tree
                  </button>
                </div>
              </div>

              {/* graph view */}
              {isGraph && (
                <GraphPanel
                  nodes={ogNodes}
                  ogSel={ogSel}
                  ogHover={ogHover}
                  setOgSel={setOgSel}
                  setOgHover={setOgHover}
                />
              )}

              {/* tree view */}
              {isTree && (
                <TreePanel
                  nodes={ogNodes}
                  ogSel={ogSel}
                  setOgSel={setOgSel}
                />
              )}
            </div>

            {/* right: detail panels */}
            {dn && (
              <div
                style={{
                  flex: '1 1 320px',
                  minWidth: 0,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 16,
                }}
              >
                <DetailPanel
                  dn={dn}
                  nodes={ogNodes}
                  setOgSel={setOgSel}
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── route export (TanStack Router file-based) ─────────────────────────────────
export const Route = createFileRoute('/og')({
  component: OgExplorer,
})
