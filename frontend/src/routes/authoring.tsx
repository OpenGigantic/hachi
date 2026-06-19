import React, { useEffect, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useStore } from '../lib/store/store'
import { Icon } from '../components/icons/Icon'
import type { AuthTarget, DiffLine } from '../lib/data/types'
import {
  loadAuthoring,
  loadAuthoringSource,
  applyAuthoring,
  revertAuthoring,
} from '../lib/data/domains/authoring'

// ---- syntax-highlight tokeniser (ported from prototype _ogTokens) ----
interface TokenPart {
  text: string
  color: string
}

function tokenise(line: string): TokenPart[] {
  const parts: TokenPart[] = []
  const re =
    /("[^"]*"|@\w+|\b(?:node|pass|tools|budget|strategy|cite|required|max_subagents)\b)/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(line)) !== null) {
    if (m.index > last) {
      parts.push({ text: line.slice(last, m.index), color: 'var(--hachi-ink)' })
    }
    const tok = m[0]
    let color = 'var(--hachi-trust)'
    if (tok[0] === '"') color = '#2C7A50'
    else if (tok[0] === '@') color = 'var(--hachi-muted)'
    parts.push({ text: tok, color })
    last = m.index + tok.length
  }
  if (last < line.length) {
    parts.push({ text: line.slice(last), color: 'var(--hachi-ink)' })
  }
  if (parts.length === 0) {
    parts.push({ text: line || ' ', color: 'var(--hachi-ink)' })
  }
  return parts
}

// ---- diff-line row colours ----
function diffRowStyle(type: '' | 'add' | 'del', isDiff: boolean) {
  if (isDiff && type === 'add') {
    return {
      bg: 'rgba(63,157,107,0.18)',
      gutColor: '#2C7A50',
      barColor: 'var(--hachi-success)',
    }
  }
  if (isDiff && type === 'del') {
    return {
      bg: 'rgba(192,57,43,0.18)',
      gutColor: '#9E2D21',
      barColor: 'var(--hachi-danger)',
    }
  }
  return { bg: 'transparent', gutColor: 'var(--hachi-muted)', barColor: 'transparent' }
}

const AUTH_KIND_ICON: Record<AuthTarget['kind'], string> = {
  brain: 'brain',
  config: 'settings',
  skill: 'file-code',
}

// ---- Authoring screen ----
function Authoring() {
  const authTargets = useStore((s) => s.authTargets)
  const authSource = useStore((s) => s.authSource)
  const authSel = useStore((s) => s.authSel)
  const authMode = useStore((s) => s.authMode)
  const authApplied = useStore((s) => s.authApplied)

  const selectAuthTarget = useStore((s) => s.selectAuthTarget)
  const setAuthMode = useStore((s) => s.setAuthMode)
  const rejectPatch = useStore((s) => s.rejectPatch)

  // Load targets list on mount (and refresh on navigate)
  useEffect(() => {
    void loadAuthoring()
  }, [])

  // When the selected target changes, fetch its source
  useEffect(() => {
    if (!authSel) return
    void loadAuthoringSource(authSel)
  }, [authSel])

  // local: prompt input (screen-local, not in global store)
  const [prompt, setPrompt] = useState(
    'Raise the dense-pass budget and switch to adaptive research strategy.'
  )
  const [hoverTarget, setHoverTarget] = useState<string | null>(null)
  const [hoverGenerate, setHoverGenerate] = useState(false)
  const [hoverApply, setHoverApply] = useState(false)
  const [hoverReject, setHoverReject] = useState(false)
  const [hoverRevert, setHoverRevert] = useState(false)

  // Compute visible diff lines (filter out "del" lines in source mode)
  const isDiff = authMode === 'diff'
  const visibleLines: DiffLine[] = authSource.filter(
    (l) => !(authMode === 'source' && l[0] === 'del')
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
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
          Authoring
        </h1>
        <span style={{ fontSize: 13, color: 'var(--hachi-muted)' }}>
          edit Hachi's own brain — review every change first
        </span>
      </div>

      {/* Scrollable body */}
      <div
        className="hachi-scroll"
        style={{ flex: 1, overflow: 'auto', padding: '24px 32px 64px' }}
      >
        <div style={{ maxWidth: 1000, margin: '0 auto' }}>
          {/* Lead copy */}
          <div
            style={{
              fontSize: 14,
              color: 'var(--hachi-muted)',
              lineHeight: '22px',
              maxWidth: 740,
              marginBottom: 18,
            }}
          >
            Generate a patch, review the diff, then apply or reject. Nothing is written
            silently — you hold the leash here too.
          </div>

          {/* Two-column layout */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '260px minmax(0,1fr)',
              gap: 16,
              alignItems: 'start',
            }}
          >
            {/* ---- Left: targets list ---- */}
            <div
              style={{
                background: 'var(--hachi-surface)',
                border: '0.5px solid var(--hachi-line)',
                borderRadius: 16,
                padding: 14,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  fontSize: 13,
                  color: 'var(--hachi-muted)',
                  marginBottom: 12,
                  padding: '0 2px',
                }}
              >
                <Icon name="folder" size={15} />
                Targets
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {authTargets.map((t) => {
                  const isSelected = t.name === authSel
                  const isHovered = hoverTarget === t.name
                  let bg = 'transparent'
                  if (isSelected) bg = 'var(--hachi-brand-tint)'
                  else if (isHovered) bg = 'var(--hachi-cream)'

                  return (
                    <div
                      key={t.name}
                      onClick={() => selectAuthTarget(t.name)}
                      onMouseEnter={() => setHoverTarget(t.name)}
                      onMouseLeave={() => setHoverTarget(null)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 9,
                        padding: '8px 10px',
                        borderRadius: 8,
                        cursor: 'pointer',
                        background: bg,
                        transition: 'background var(--dur-fast) var(--ease)',
                      }}
                    >
                      <span
                        style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}
                      >
                        <Icon name={AUTH_KIND_ICON[t.kind] ?? 'file-code'} size={13} />
                      </span>
                      <span
                        style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: 12.5,
                          flex: 1,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {t.name}
                      </span>
                      {t.dirty && (
                        <span
                          style={{
                            width: 7,
                            height: 7,
                            borderRadius: '50%',
                            background: 'var(--hachi-working)',
                            flexShrink: 0,
                          }}
                        />
                      )}
                    </div>
                  )
                })}
              </div>
            </div>

            {/* ---- Right: viewer + generate panel ---- */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* Diff / Source viewer */}
              <div
                style={{
                  background: 'var(--hachi-surface)',
                  border: '0.5px solid var(--hachi-line)',
                  borderRadius: 16,
                  overflow: 'hidden',
                }}
              >
                {/* Viewer header */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '13px 16px',
                    borderBottom: '0.5px solid var(--hachi-line)',
                  }}
                >
                  <span style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}>
                    <Icon name="file-code" size={15} />
                  </span>
                  <span
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontWeight: 500,
                      fontSize: 13,
                      flex: 1,
                    }}
                  >
                    {authSel}
                  </span>

                  {/* Diff / Source toggle */}
                  <div
                    style={{
                      display: 'inline-flex',
                      background: 'var(--hachi-cream)',
                      borderRadius: 8,
                      padding: 2,
                    }}
                  >
                    <button
                      onClick={() => setAuthMode('diff')}
                      style={{
                        height: 28,
                        padding: '0 13px',
                        border: 'none',
                        borderRadius: 6,
                        font: '500 12px var(--font-body)',
                        cursor: 'pointer',
                        background: isDiff ? 'var(--hachi-surface)' : 'transparent',
                        color: isDiff ? 'var(--hachi-ink)' : 'var(--hachi-muted)',
                        transition: 'background var(--dur-fast) var(--ease)',
                      }}
                    >
                      Diff
                    </button>
                    <button
                      onClick={() => setAuthMode('source')}
                      style={{
                        height: 28,
                        padding: '0 13px',
                        border: 'none',
                        borderRadius: 6,
                        font: '500 12px var(--font-body)',
                        cursor: 'pointer',
                        background: !isDiff ? 'var(--hachi-surface)' : 'transparent',
                        color: !isDiff ? 'var(--hachi-ink)' : 'var(--hachi-muted)',
                        transition: 'background var(--dur-fast) var(--ease)',
                      }}
                    >
                      Source
                    </button>
                  </div>
                </div>

                {/* Code viewer */}
                <div
                  className="hachi-scroll"
                  style={{
                    background: '#16151A',
                    maxHeight: 380,
                    overflow: 'auto',
                    padding: '12px 0',
                  }}
                >
                  {visibleLines.map((l, i) => {
                    const type = l[0]
                    const lineText = l[1]
                    const { bg, gutColor, barColor } = diffRowStyle(type, isDiff)
                    const gutter = isDiff && type === 'add'
                      ? '+'
                      : isDiff && type === 'del'
                      ? '−'
                      : String(i + 1)
                    const parts = tokenise(lineText)

                    return (
                      <div
                        key={i}
                        style={{
                          display: 'flex',
                          alignItems: 'flex-start',
                          background: bg,
                          minHeight: 22,
                        }}
                      >
                        {/* Gutter */}
                        <span
                          style={{
                            width: 38,
                            flexShrink: 0,
                            textAlign: 'right',
                            paddingRight: 12,
                            fontFamily: 'var(--font-mono)',
                            fontSize: 12.5,
                            lineHeight: '22px',
                            color: gutColor,
                            userSelect: 'none',
                          }}
                        >
                          {gutter}
                        </span>
                        {/* Color bar */}
                        <span
                          style={{
                            width: 3,
                            flexShrink: 0,
                            alignSelf: 'stretch',
                            background: barColor,
                          }}
                        />
                        {/* Code tokens — overflow-hidden, no wrap */}
                        <span
                          style={{
                            flex: 1,
                            paddingLeft: 12,
                            fontFamily: 'var(--font-mono)',
                            fontSize: 12.5,
                            lineHeight: '22px',
                            color: '#FBF7EF',
                            whiteSpace: 'pre',
                            overflow: 'hidden',
                          }}
                        >
                          {parts.map((p, pi) => (
                            <span key={pi} style={{ color: p.color }}>
                              {p.text}
                            </span>
                          ))}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* Generate patch panel */}
              <div
                style={{
                  background: 'var(--hachi-surface)',
                  border: '0.5px solid var(--hachi-line)',
                  borderRadius: 16,
                  padding: 18,
                }}
              >
                {/* Panel header */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    marginBottom: 13,
                  }}
                >
                  <span style={{ color: 'var(--hachi-brand)', display: 'inline-flex' }}>
                    <Icon name="sparkles" size={16} />
                  </span>
                  <span style={{ fontWeight: 500, fontSize: 14 }}>Generate patch</span>
                </div>

                {/* Prompt row */}
                <div style={{ display: 'flex', gap: 10, marginBottom: 14 }}>
                  <input
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    style={{
                      flex: 1,
                      minWidth: 0,
                      height: 38,
                      padding: '0 12px',
                      border: '0.5px solid var(--hachi-line)',
                      borderRadius: 10,
                      background: 'var(--hachi-canvas)',
                      font: 'inherit',
                      fontSize: 13,
                      color: 'var(--hachi-ink)',
                      outline: 'none',
                    }}
                    placeholder="Describe the change you want…"
                  />
                  <button
                    onMouseEnter={() => setHoverGenerate(true)}
                    onMouseLeave={() => setHoverGenerate(false)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 7,
                      height: 38,
                      padding: '0 14px',
                      borderRadius: 10,
                      border: '0.5px solid transparent',
                      background: hoverGenerate
                        ? 'var(--hachi-brand-hover)'
                        : 'var(--hachi-brand)',
                      color: 'var(--hachi-canvas)',
                      font: '500 13px var(--font-body)',
                      cursor: 'pointer',
                      transition: 'background var(--dur-fast) var(--ease)',
                      flexShrink: 0,
                    }}
                  >
                    <Icon name="sparkles" size={14} />
                    Generate
                  </button>
                </div>

                {/* Patch summary row */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    padding: '12px 14px',
                    background: 'var(--hachi-cream)',
                    border: '0.5px solid var(--hachi-line)',
                    borderRadius: 10,
                  }}
                >
                  {/* Applied badge */}
                  {authApplied && (
                    <span
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 6,
                        padding: '4px 10px',
                        borderRadius: 999,
                        background: 'var(--hachi-success-tint)',
                        color: '#2C7A50',
                        fontSize: 12,
                        fontWeight: 500,
                        flexShrink: 0,
                      }}
                    >
                      <span
                        style={{
                          width: 7,
                          height: 7,
                          borderRadius: '50%',
                          background: 'var(--hachi-success)',
                        }}
                      />
                      applied
                    </span>
                  )}

                  <span
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: 13,
                      color: 'var(--hachi-muted)',
                    }}
                  >
                    +3 &minus;2 lines &middot; {authSel}
                  </span>

                  {/* Action buttons */}
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                    {authApplied ? (
                      <button
                        onClick={() => void revertAuthoring(authSel)}
                        onMouseEnter={() => setHoverRevert(true)}
                        onMouseLeave={() => setHoverRevert(false)}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 6,
                          height: 34,
                          padding: '0 13px',
                          borderRadius: 10,
                          border: '0.5px solid var(--hachi-line)',
                          background: hoverRevert
                            ? 'var(--hachi-cream)'
                            : 'var(--hachi-canvas)',
                          color: 'var(--hachi-ink)',
                          font: '500 13px var(--font-body)',
                          cursor: 'pointer',
                          transition: 'background var(--dur-fast) var(--ease)',
                        }}
                      >
                        <Icon name="refresh" size={13} />
                        Revert
                      </button>
                    ) : (
                      <>
                        <button
                          onClick={rejectPatch}
                          onMouseEnter={() => setHoverReject(true)}
                          onMouseLeave={() => setHoverReject(false)}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 6,
                            height: 34,
                            padding: '0 13px',
                            borderRadius: 10,
                            border: '1px solid var(--hachi-trust)',
                            background: hoverReject
                              ? 'var(--hachi-trust-tint)'
                              : 'transparent',
                            color: 'var(--hachi-trust)',
                            font: '500 13px var(--font-body)',
                            cursor: 'pointer',
                            transition: 'background var(--dur-fast) var(--ease)',
                          }}
                        >
                          <Icon name="x" size={13} />
                          Reject
                        </button>
                        <button
                          onClick={() => void applyAuthoring(authSel)}
                          onMouseEnter={() => setHoverApply(true)}
                          onMouseLeave={() => setHoverApply(false)}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 6,
                            height: 34,
                            padding: '0 13px',
                            borderRadius: 10,
                            border: '0.5px solid transparent',
                            background: hoverApply
                              ? 'var(--hachi-brand-hover)'
                              : 'var(--hachi-brand)',
                            color: 'var(--hachi-canvas)',
                            font: '500 13px var(--font-body)',
                            cursor: 'pointer',
                            transition: 'background var(--dur-fast) var(--ease)',
                          }}
                        >
                          <Icon name="check" size={13} />
                          Apply patch
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {/* Post-apply confirmation line */}
                {authApplied && (
                  <div
                    style={{
                      marginTop: 11,
                      fontSize: 12.5,
                      color: '#2C7A50',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 7,
                    }}
                  >
                    <Icon name="check" size={14} />
                    Written to ~/.hachi/skills/{authSel} &middot; re-indexed in 0.3s
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export const Route = createFileRoute('/authoring')({
  component: Authoring,
})
