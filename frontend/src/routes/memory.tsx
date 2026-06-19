// ============================================================
// Memory screen — /memory
// Recreates prototype lines 669–797 faithfully.
// Screen-local state: memSearch, recallQuery, recallDone, hovered buttons.
// All domain mutations via useStore.
// ============================================================

import React, { useEffect, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useStore } from '../lib/store/store'
import {
  loadMemory,
  recallMemory,
  saveFact as saveFactRemote,
  delFact as delFactRemote,
} from '../lib/data/domains/memory'
import { Icon } from '../components/icons/Icon'
import type { MemGroup, Fact, BlockedItem, RecallHit, Session, SkillUsage } from '../lib/data/types'

// ============================================================
// Small hover-aware button helpers (screen-local, no css class needed)
// ============================================================
function GhostIconBtn({
  onClick,
  hoverBg,
  hoverColor,
  'aria-label': ariaLabel,
  children,
}: {
  onClick: () => void
  hoverBg: string
  hoverColor: string
  'aria-label': string
  children: React.ReactNode
}) {
  const [hov, setHov] = useState(false)
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      aria-label={ariaLabel}
      style={{
        border: 'none',
        background: 'transparent',
        color: hov ? hoverColor : 'var(--hachi-muted)',
        cursor: 'pointer',
        display: 'inline-flex',
        padding: '4px',
        borderRadius: '6px',
        transition: 'background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)',
        ...(hov ? { background: hoverBg } : {}),
      }}
    >
      {children}
    </button>
  )
}

function TextBtn({
  onClick,
  children,
  style,
  hoverStyle,
}: {
  onClick: () => void
  children: React.ReactNode
  style: React.CSSProperties
  hoverStyle: React.CSSProperties
}) {
  const [hov, setHov] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      style={{ ...style, ...(hov ? hoverStyle : {}) }}
    >
      {children}
    </button>
  )
}

// ============================================================
// Fact card — view or edit mode
// ============================================================
function FactCard({
  fact,
  groupId,
  editingFactId,
  editingText,
  onEdit,
  onDelete,
  onSetEditText,
  onCancelEdit,
  onSave,
}: {
  fact: Fact
  groupId: string
  editingFactId: string | null
  editingText: string
  onEdit: () => void
  onDelete: () => void
  onSetEditText: (v: string) => void
  onCancelEdit: () => void
  onSave: () => void
}) {
  const isEditing = editingFactId === fact.id

  return (
    <div
      style={{
        background: 'var(--hachi-surface)',
        border: '0.5px solid var(--hachi-line)',
        borderRadius: '14px',
        padding: '15px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
      }}
    >
      {!isEditing ? (
        <>
          <div style={{ fontSize: '14px', lineHeight: '21px' }}>{fact.text || <span style={{ color: 'var(--hachi-muted)', fontStyle: 'italic' }}>empty fact</span>}</div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: '12px', color: 'var(--hachi-muted)' }}>{fact.source}</span>
            <div style={{ display: 'flex', gap: '2px' }}>
              <GhostIconBtn onClick={onEdit} hoverBg="var(--hachi-cream)" hoverColor="var(--hachi-trust)" aria-label="Edit fact">
                <Icon name="pencil" size={15} />
              </GhostIconBtn>
              <GhostIconBtn onClick={onDelete} hoverBg="var(--hachi-cream)" hoverColor="var(--hachi-danger)" aria-label="Delete fact">
                <Icon name="trash" size={15} />
              </GhostIconBtn>
            </div>
          </div>
        </>
      ) : (
        <>
          <textarea
            value={editingText}
            onChange={(e) => onSetEditText(e.target.value)}
            rows={2}
            placeholder="What should Hachi remember?"
            style={{
              width: '100%',
              resize: 'none',
              border: '0.5px solid var(--hachi-brand)',
              borderRadius: '8px',
              background: 'var(--hachi-canvas)',
              font: 'inherit',
              fontSize: '14px',
              lineHeight: '21px',
              color: 'var(--hachi-ink)',
              outline: 'none',
              padding: '9px 11px',
              boxSizing: 'border-box',
            }}
          />
          <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
            <TextBtn
              onClick={onCancelEdit}
              style={{
                height: '30px',
                padding: '0 12px',
                borderRadius: '8px',
                border: 'none',
                background: 'transparent',
                color: 'var(--hachi-muted)',
                font: '500 13px var(--font-body)',
                cursor: 'pointer',
              }}
              hoverStyle={{ background: 'var(--hachi-cream)' }}
            >
              Cancel
            </TextBtn>
            <TextBtn
              onClick={onSave}
              style={{
                height: '30px',
                padding: '0 13px',
                borderRadius: '8px',
                border: '0.5px solid transparent',
                background: 'var(--hachi-brand)',
                color: 'var(--hachi-canvas)',
                font: '500 13px var(--font-body)',
                cursor: 'pointer',
              }}
              hoverStyle={{ background: 'var(--hachi-brand-hover)' }}
            >
              Save
            </TextBtn>
          </div>
        </>
      )}
    </div>
  )
}

// ============================================================
// Main Memory component
// ============================================================
function Memory() {
  // --- store ---
  const memGroups = useStore((s) => s.memGroups)
  const blocked = useStore((s) => s.blocked)
  const proposed = useStore((s) => s.proposed)
  const recall = useStore((s) => s.recall)
  const sessions = useStore((s) => s.sessions)
  const skillUsage = useStore((s) => s.skillUsage)
  const editingFactId = useStore((s) => s.editingFactId)
  const editingText = useStore((s) => s.editingText)

  const addFact = useStore((s) => s.addFact)
  const startEditFact = useStore((s) => s.startEditFact)
  const delFact = useStore((s) => s.delFact)
  const setEditText = useStore((s) => s.setEditText)
  const cancelEdit = useStore((s) => s.cancelEdit)
  const saveFact = useStore((s) => s.saveFact)
  const approveProposed = useStore((s) => s.approveProposed)
  const rejectProposed = useStore((s) => s.rejectProposed)
  const showToast = useStore((s) => s.showToast)

  // --- load on mount (refresh-on-navigate; bootstrapLiveData also runs on app mount) ---
  useEffect(() => { void loadMemory() }, [])

  // --- local state ---
  const [memSearch, setMemSearch] = useState('')
  const [recallQuery, setRecallQuery] = useState('what do you know about my repo?')
  const [recallDone, setRecallDone] = useState(true)

  // --- computed ---
  const q = memSearch.trim().toLowerCase()
  const filteredGroups: MemGroup[] = memGroups
    .map((g) => ({
      ...g,
      facts: g.facts.filter((f) => !q || f.text.toLowerCase().includes(q)),
    }))
    .filter((g) => g.facts.length > 0)
  const memEmpty = q.length > 0 && filteredGroups.length === 0

  // --- session renderVals (computed inline, mirroring prototype line 2289) ---
  const sessionRows = sessions.map((s: Session) => ({
    ...s,
    turnsLabel: s.turns + ' turns',
    label: s.active ? 'Current' : 'Resume',
    icon: s.active ? 'eye' : 'chat',
    resume: () => {
      if (s.active) return
      showToast('Resuming "' + s.title + '" — picking up where you left off', 'success')
    },
  }))

  // skill usage — calls as string + saved with minus sign
  const skillRows = skillUsage.map((k: SkillUsage) => ({
    ...k,
    callsLabel: k.calls + ' calls',
    savedLabel: '−' + k.saved,
  }))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* ---- header ---- */}
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
          Memory
        </h1>
        <span style={{ fontSize: '13px', color: 'var(--hachi-muted)' }}>
          what Hachi knows about you
        </span>
        <div style={{ flex: 1 }} />
        {/* add a fact */}
        <TextBtn
          onClick={() => {
            // Optimistic only: insert a temp fact in the store and open edit mode.
            // The actual POST to /memory/facts fires when the user saves (saveFact).
            addFact('you')
          }}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '7px',
            height: '38px',
            padding: '0 14px',
            borderRadius: '10px',
            border: '0.5px solid var(--hachi-line)',
            background: 'var(--hachi-canvas)',
            color: 'var(--hachi-ink)',
            font: '500 13px var(--font-body)',
            cursor: 'pointer',
          }}
          hoverStyle={{ background: 'var(--hachi-cream)' }}
        >
          <Icon name="plus" size={15} />
          Add a fact
        </TextBtn>
        {/* search */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            height: '38px',
            padding: '0 12px',
            background: 'var(--hachi-surface)',
            border: '0.5px solid var(--hachi-line)',
            borderRadius: '10px',
            width: '240px',
            color: 'var(--hachi-muted)',
          }}
        >
          <Icon name="search" size={15} />
          <input
            value={memSearch}
            onChange={(e) => setMemSearch(e.target.value)}
            placeholder="Search memory"
            style={{
              flex: 1,
              border: 'none',
              background: 'transparent',
              font: 'inherit',
              fontSize: '13px',
              color: 'var(--hachi-ink)',
              outline: 'none',
            }}
          />
        </div>
      </div>

      {/* ---- scrollable body ---- */}
      <div
        className="hachi-scroll"
        style={{ flex: 1, overflow: 'auto', padding: '24px 32px 64px' }}
      >
        <div
          style={{
            maxWidth: '820px',
            margin: '0 auto',
            display: 'flex',
            flexDirection: 'column',
            gap: '26px',
          }}
        >

          {/* ---- semantic recall ---- */}
          <div
            style={{
              background: 'var(--hachi-surface)',
              border: '0.5px solid var(--hachi-line)',
              borderRadius: '16px',
              padding: '18px',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '9px',
                marginBottom: '13px',
              }}
            >
              <span style={{ color: 'var(--hachi-trust)', display: 'inline-flex' }}>
                <Icon name="sparkles" size={17} />
              </span>
              <span style={{ fontWeight: 500, fontSize: '15px' }}>Ask memory anything</span>
            </div>
            <div style={{ display: 'flex', gap: '10px' }}>
              <input
                value={recallQuery}
                onChange={(e) => {
                  setRecallQuery(e.target.value)
                  setRecallDone(false)
                }}
                placeholder="What do you want Hachi to recall?"
                style={{
                  flex: 1,
                  minWidth: 0,
                  height: '40px',
                  padding: '0 13px',
                  border: '0.5px solid var(--hachi-line)',
                  borderRadius: '10px',
                  background: 'var(--hachi-canvas)',
                  font: 'inherit',
                  fontSize: '14px',
                  color: 'var(--hachi-ink)',
                  outline: 'none',
                }}
              />
              <TextBtn
                onClick={() => {
                  void recallMemory(recallQuery)
                  setRecallDone(true)
                }}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '7px',
                  height: '40px',
                  padding: '0 15px',
                  borderRadius: '10px',
                  border: '0.5px solid transparent',
                  background: 'var(--hachi-brand)',
                  color: 'var(--hachi-canvas)',
                  font: '500 13px var(--font-body)',
                  cursor: 'pointer',
                }}
                hoverStyle={{ background: 'var(--hachi-brand-hover)' }}
              >
                <Icon name="search" size={15} />
                Recall
              </TextBtn>
            </div>

            {recallDone && (
              <>
                <div
                  style={{
                    fontSize: '12px',
                    color: 'var(--hachi-muted)',
                    margin: '14px 0 8px',
                  }}
                >
                  Closest matches
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {recall.map((r: RecallHit) => (
                    <div
                      key={r.fact}
                      style={{
                        display: 'flex',
                        gap: '12px',
                        alignItems: 'flex-start',
                        padding: '11px 13px',
                        background: 'var(--hachi-canvas)',
                        border: '0.5px solid var(--hachi-line)',
                        borderRadius: '10px',
                      }}
                    >
                      <span
                        style={{
                          color: 'var(--hachi-trust)',
                          flexShrink: 0,
                          marginTop: '1px',
                          display: 'inline-flex',
                        }}
                      >
                        <Icon name="sparkles" size={15} />
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: '13.5px', lineHeight: '20px' }}>{r.fact}</div>
                        <div
                          style={{
                            fontFamily: 'var(--font-mono)',
                            fontSize: '11.5px',
                            color: 'var(--hachi-muted)',
                            marginTop: '4px',
                          }}
                        >
                          {r.src}
                        </div>
                      </div>
                      <span
                        style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: '12px',
                          color: 'var(--hachi-trust)',
                          flexShrink: 0,
                        }}
                      >
                        {r.score}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>

          {/* ---- proposed change ---- */}
          {proposed && (
            <div
              style={{
                background: 'var(--hachi-surface)',
                border: '0.5px solid var(--hachi-brand)',
                borderRadius: '16px',
                padding: '18px',
                display: 'flex',
                gap: '14px',
                alignItems: 'flex-start',
              }}
            >
              <div
                style={{
                  flexShrink: 0,
                  width: '38px',
                  height: '38px',
                  borderRadius: '10px',
                  background: 'var(--hachi-brand-tint)',
                  display: 'grid',
                  placeItems: 'center',
                  color: 'var(--hachi-brand)',
                }}
              >
                <Icon name="sparkles" size={19} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 500, fontSize: '15px', marginBottom: '6px' }}>
                  Hachi proposes an update
                </div>
                <div
                  style={{
                    fontSize: '14px',
                    color: 'var(--hachi-muted)',
                    lineHeight: '22px',
                  }}
                >
                  {proposed.from}{' '}
                  <span style={{ color: 'var(--hachi-ink)' }}>→ {proposed.to}</span>
                </div>
                <div style={{ display: 'flex', gap: '9px', marginTop: '14px' }}>
                  <TextBtn
                    onClick={rejectProposed}
                    style={{
                      height: '34px',
                      padding: '0 14px',
                      borderRadius: '10px',
                      border: '1px solid var(--hachi-trust)',
                      background: 'transparent',
                      color: 'var(--hachi-trust)',
                      font: '500 13px var(--font-body)',
                      cursor: 'pointer',
                    }}
                    hoverStyle={{ background: 'var(--hachi-trust-tint)' }}
                  >
                    Reject
                  </TextBtn>
                  <TextBtn
                    onClick={approveProposed}
                    style={{
                      height: '34px',
                      padding: '0 14px',
                      borderRadius: '10px',
                      border: '0.5px solid transparent',
                      background: 'var(--hachi-brand)',
                      color: 'var(--hachi-canvas)',
                      font: '500 13px var(--font-body)',
                      cursor: 'pointer',
                    }}
                    hoverStyle={{ background: 'var(--hachi-brand-hover)' }}
                  >
                    Approve change
                  </TextBtn>
                </div>
              </div>
            </div>
          )}

          {/* ---- memory groups ---- */}
          {filteredGroups.map((g: MemGroup) => (
            <div key={g.id}>
              <h2
                style={{
                  margin: '0 0 12px',
                  fontFamily: 'var(--font-display)',
                  fontWeight: 500,
                  fontSize: '15px',
                  color: 'var(--hachi-muted)',
                }}
              >
                {g.title}
              </h2>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: '12px',
                }}
              >
                {g.facts.map((fact: Fact) => (
                  <FactCard
                    key={fact.id}
                    fact={fact}
                    groupId={g.id}
                    editingFactId={editingFactId}
                    editingText={editingText}
                    onEdit={() => startEditFact(fact.id)}
                    onDelete={() => {
                      // 1. Optimistic: remove from store immediately
                      delFact(g.id, fact.id)
                      // 2. Network: DELETE /memory/facts/{fact_id}
                      //    On failure, loadMemory() inside delFactRemote reverts
                      void delFactRemote(g.id, fact.id)
                    }}
                    onSetEditText={setEditText}
                    onCancelEdit={cancelEdit}
                    onSave={() => {
                      const text = editingText
                      // 1. Optimistic: persist text in store immediately
                      saveFact(g.id, fact.id)
                      // 2. Network: DELETE old + POST new (or just POST for temp ids)
                      //    On failure, loadMemory() inside saveFactRemote reverts
                      void saveFactRemote(g.id, fact.id, text)
                    }}
                  />
                ))}
              </div>
            </div>
          ))}

          {/* ---- empty state ---- */}
          {memEmpty && (
            <div
              style={{
                textAlign: 'center',
                padding: '40px 0',
                color: 'var(--hachi-muted)',
              }}
            >
              No memories match "{memSearch}".
            </div>
          )}

          {/* ---- blocked / quarantined ---- */}
          <div>
            <h2
              style={{
                margin: '0 0 12px',
                fontFamily: 'var(--font-display)',
                fontWeight: 500,
                fontSize: '15px',
                color: 'var(--hachi-muted)',
              }}
            >
              Blocked — Hachi refused to store
            </h2>
            <div
              style={{
                background: 'var(--hachi-surface)',
                border: '0.5px solid var(--hachi-line)',
                borderRadius: '14px',
                overflow: 'hidden',
              }}
            >
              {blocked.map((b: BlockedItem, i: number) => (
                <div
                  key={b.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '11px',
                    padding: '13px 16px',
                    borderBottom:
                      i < blocked.length - 1
                        ? '0.5px solid var(--hachi-line)'
                        : undefined,
                  }}
                >
                  <span style={{ color: 'var(--hachi-danger)', display: 'inline-flex' }}>
                    <Icon name="shield" size={16} />
                  </span>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: '14px' }}>{b.text}</div>
                    <div style={{ fontSize: '12px', color: 'var(--hachi-muted)', marginTop: '2px' }}>
                      {b.reason}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* ---- skills Hachi leans on ---- */}
          <div>
            <h2
              style={{
                margin: '0 0 12px',
                fontFamily: 'var(--font-display)',
                fontWeight: 500,
                fontSize: '15px',
                color: 'var(--hachi-muted)',
              }}
            >
              Skills Hachi leans on
            </h2>
            <div
              style={{
                background: 'var(--hachi-surface)',
                border: '0.5px solid var(--hachi-line)',
                borderRadius: '14px',
                overflow: 'hidden',
              }}
            >
              {skillRows.map((k, i) => (
                <div
                  key={k.name}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '13px',
                    padding: '12px 16px',
                    borderBottom:
                      i < skillRows.length - 1
                        ? '0.5px solid var(--hachi-line)'
                        : undefined,
                  }}
                >
                  <span
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: '13px',
                      color: 'var(--hachi-ink)',
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {k.name}
                  </span>
                  <span style={{ fontSize: '12.5px', color: 'var(--hachi-muted)' }}>
                    {k.callsLabel}
                  </span>
                  <span
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: '12.5px',
                      color: 'var(--hachi-muted)',
                      width: '48px',
                      textAlign: 'right',
                    }}
                  >
                    {k.tokens}
                  </span>
                  <span
                    style={{
                      fontSize: '11.5px',
                      fontWeight: 500,
                      color: 'var(--hachi-success)',
                      background: 'var(--hachi-success-tint)',
                      padding: '3px 9px',
                      borderRadius: '999px',
                      flexShrink: 0,
                    }}
                  >
                    {k.savedLabel} tokens
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* ---- resumable sessions ---- */}
          <div>
            <h2
              style={{
                margin: '0 0 12px',
                fontFamily: 'var(--font-display)',
                fontWeight: 500,
                fontSize: '15px',
                color: 'var(--hachi-muted)',
              }}
            >
              Sessions — pick up where you left off
            </h2>
            <div
              style={{
                background: 'var(--hachi-surface)',
                border: '0.5px solid var(--hachi-line)',
                borderRadius: '14px',
                overflow: 'hidden',
              }}
            >
              {sessionRows.map((s, i) => (
                <div
                  key={s.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '13px',
                    padding: '13px 16px',
                    borderBottom:
                      i < sessionRows.length - 1
                        ? '0.5px solid var(--hachi-line)'
                        : undefined,
                  }}
                >
                  <span style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}>
                    <Icon name={s.icon} size={16} />
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: '14px' }}>{s.title}</div>
                    <div
                      style={{
                        fontSize: '12px',
                        color: 'var(--hachi-muted)',
                        marginTop: '2px',
                      }}
                    >
                      {s.turnsLabel} · {s.model} · {s.date}
                    </div>
                  </div>
                  <TextBtn
                    onClick={s.resume}
                    style={{
                      height: '32px',
                      padding: '0 13px',
                      borderRadius: '9px',
                      border: '0.5px solid var(--hachi-line)',
                      background: 'var(--hachi-canvas)',
                      color: 'var(--hachi-ink)',
                      font: '500 13px var(--font-body)',
                      cursor: 'pointer',
                    }}
                    hoverStyle={{ background: 'var(--hachi-cream)' }}
                  >
                    {s.label}
                  </TextBtn>
                </div>
              ))}
            </div>
          </div>

        </div>
      </div>
    </div>
  )
}

export const Route = createFileRoute('/memory')({
  component: Memory,
})
