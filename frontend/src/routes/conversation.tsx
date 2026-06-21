// Conversation screen — /conversation
// Faithfully ports prototype markup lines 212–336 and logic handlers.
// Screen-local view state only in useState; all domain mutations go through useStore.

import React, { useEffect, useRef, useState, useCallback, memo } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useStore } from '../lib/store/store'
import { Icon } from '../components/icons/Icon'
import { HachiMark } from '../components/brand/HachiMark'
import { LeashPrompt } from '../components/brand/LeashPrompt'
import type { ChatMessage, ActionCard, Integration, PendingAttachment } from '../lib/data/types'
// Side-effect import: registers the "chat" loader with bootstrapLiveData()
// and makes loadSessions available for the per-route refresh below.
import {
  loadSessions,
  loadConversationHistory,
  abortSseLive,
  deleteSessionLive,
} from '../lib/data/domains/chat'
import {
  uploadFile,
  makeOptimisticAttachment,
  getFileMeta,
  openFileContent,
} from '../lib/data/domains/files'
import { Markdown } from '../components/ui/Markdown'

// ---- SessionPicker (M8 MED-020) -----------------------------------

interface SessionPickerProps {
  sessions: import('../lib/data/types').Session[]
  activeId: string | null
  onSwitch: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  onClose: () => void
}

function SessionPicker({ sessions, activeId, onSwitch, onNew, onDelete, onClose }: SessionPickerProps) {
  const ref = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  // Close on Escape
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div
      ref={ref}
      role="menu"
      aria-label="Recent sessions"
      style={{
        position: 'absolute',
        top: 'calc(100% + 6px)',
        left: 0,
        minWidth: 260,
        maxWidth: 340,
        background: 'var(--hachi-surface)',
        border: '0.5px solid var(--hachi-line)',
        borderRadius: 12,
        boxShadow: '0 8px 24px rgba(28,26,32,.10)',
        zIndex: 50,
        maxHeight: '60vh',
        overflowY: 'auto',
        overflowX: 'hidden',
      }}
    >
      {/* New conversation row */}
      <button
        type="button"
        role="menuitem"
        onClick={() => { onNew(); onClose() }}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '11px 14px',
          border: 'none',
          borderBottom: '0.5px solid var(--hachi-line)',
          background: 'transparent',
          cursor: 'pointer',
          textAlign: 'left',
          font: 'inherit',
          color: 'var(--hachi-brand)',
          fontSize: 13,
          fontWeight: 500,
        }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-cream)' }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
      >
        <Icon name="plus" size={14} />
        New conversation
      </button>

      {/* Session list */}
      {sessions.length === 0 && (
        <div style={{ padding: '12px 14px', fontSize: 13, color: 'var(--hachi-muted)' }}>
          No recent sessions
        </div>
      )}
      {sessions.map((s) => {
        const isActive = s.id === activeId
        return (
          <div
            key={s.id}
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '6px 8px 6px 10px',
              borderBottom: '0.5px solid var(--hachi-line)',
              background: isActive ? 'var(--hachi-cream)' : 'transparent',
            }}
          >
            <button
              type="button"
              role="menuitem"
              aria-current={isActive ? 'true' : undefined}
              onClick={() => { onSwitch(s.id); onClose() }}
              style={{
                flex: 1,
                minWidth: 0,
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '8px 6px',
                border: 'none',
                background: 'transparent',
                cursor: 'pointer',
                textAlign: 'left',
                font: 'inherit',
              }}
            >
              <span style={{ color: isActive ? 'var(--hachi-brand)' : 'var(--hachi-muted)', display: 'inline-flex', flexShrink: 0 }}>
                <Icon name={isActive ? 'circle-check' : 'message-square'} size={14} />
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: isActive ? 500 : 400,
                    color: 'var(--hachi-ink)',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {s.title}
                </div>
                <div style={{ fontSize: 11, color: 'var(--hachi-muted)', marginTop: 1 }}>
                  {s.date}{s.turns ? ` · ${s.turns} turn${s.turns !== 1 ? 's' : ''}` : ''}
                </div>
              </div>
            </button>
            <button
              type="button"
              aria-label={`Delete ${s.title}`}
              onClick={() => onDelete(s.id)}
              style={{
                width: 28,
                height: 28,
                borderRadius: 8,
                border: 'none',
                background: 'transparent',
                color: 'var(--hachi-muted)',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                flexShrink: 0,
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-danger-tint)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--hachi-danger)' }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--hachi-muted)' }}
            >
              <Icon name="trash" size={13} />
            </button>
          </div>
        )
      })}
    </div>
  )
}

// ---- helpers --------------------------------------------------------

function fmtDollar(n: number): string {
  return '$' + Number(n).toFixed(2)
}

function statusMeta(st: string): {
  bg: string
  fg: string
  dot: string
  text: string
} {
  const map: Record<string, { bg: string; fg: string; dot: string; text: string }> = {
    working: {
      bg: 'var(--hachi-working-tint)',
      fg: '#A8651A',
      dot: 'var(--hachi-working)',
      text: 'working',
    },
    'on-watch': {
      bg: 'var(--hachi-trust-tint)',
      fg: 'var(--hachi-trust)',
      dot: 'var(--hachi-trust)',
      text: 'on watch',
    },
    done: {
      bg: 'var(--hachi-success-tint)',
      fg: '#2C7A50',
      dot: 'var(--hachi-success)',
      text: 'done',
    },
    blocked: {
      bg: 'var(--hachi-danger-tint)',
      fg: '#9E2D21',
      dot: 'var(--hachi-danger)',
      text: 'blocked',
    },
    pending: {
      bg: 'var(--hachi-cream)',
      fg: 'var(--hachi-muted)',
      dot: 'var(--hachi-muted)',
      text: 'pending',
    },
  }
  return map[st] ?? map.working
}

// ---- slash command items (prototype renderVals lines 2214–2219) ------
// LOW-024: added /plan /memory /sessions /model /delegate

const SLASH_ITEMS = [
  { cmd: '/routine', desc: 'create a recurring routine' },
  { cmd: '/dry-run', desc: 'try a task once, safely' },
  { cmd: '/remember', desc: 'save a fact to memory' },
  { cmd: '/budget', desc: 'set a spend limit' },
  { cmd: '/plan', desc: 'show the current task plan' },
  { cmd: '/memory', desc: 'browse and edit memory' },
  { cmd: '/sessions', desc: 'switch to a previous session' },
  { cmd: '/model', desc: 'change the AI model' },
  { cmd: '/delegate', desc: 'hand off to a sub-agent' },
]

// ---- chat starters (prototype renderVals lines 2209–2213) -----------
// HIGH-019: integrations required to show certain starters

interface ChatStarter {
  label: string
  icon: string
  text: string
  requiresIntegration?: string  // integration name that must be "connected"
}

const CHAT_STARTERS: ChatStarter[] = [
  { label: 'Summarise my inbox', icon: 'mail', text: 'Summarise my inbox and flag anything urgent', requiresIntegration: 'Google' },
  { label: 'Draft a reply to Priya', icon: 'pencil', text: 'Draft a reply to Priya about the Q3 deck', requiresIntegration: 'Google' },
  { label: 'Create a routine', icon: 'repeat', text: 'Every weekday at 8, summarise my inbox and message me the urgent ones' },
]

// ---- ActionCard row -------------------------------------------------

interface ActionCardRowProps {
  card: ActionCard
  msgId: number
  operator: boolean
}

function ActionCardRow({ card, msgId, operator }: ActionCardRowProps) {
  const toggleCard = useStore((s) => s.toggleCard)
  // In operator mode, cards start expanded; otherwise start collapsed.
  // Local expanded state is layered on top of the store-level `expanded` flag.
  const [localExpanded, setLocalExpanded] = useState(operator ? true : card.expanded)

  // Sync when card.expanded changes (e.g. operator toggle)
  useEffect(() => {
    setLocalExpanded(operator ? true : card.expanded)
  }, [card.expanded, operator])

  const sm = statusMeta(card.status)
  const isExpanded = localExpanded
  const rot = isExpanded ? 180 : 0

  function handleToggle() {
    setLocalExpanded((v) => !v)
    toggleCard(msgId, card.key)
  }

  return (
    <div
      style={{
        border: '0.5px solid var(--hachi-line)',
        borderRadius: 12,
        background: 'var(--hachi-surface)',
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        onClick={handleToggle}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 11,
          padding: '12px 14px',
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          textAlign: 'left',
          font: 'inherit',
        }}
        onMouseEnter={(e) => {
          ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-cream)'
        }}
        onMouseLeave={(e) => {
          ;(e.currentTarget as HTMLButtonElement).style.background = 'transparent'
        }}
      >
        {/* icon */}
        <span style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}>
          <Icon name={card.icon} size={16} />
        </span>

        {/* title + operator tool sig */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 500, fontSize: 14, color: 'var(--hachi-ink)' }}>
            {card.title}
          </div>
          {operator && card.tool && (
            <div
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
                color: 'var(--hachi-muted)',
                marginTop: 2,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {card.tool}
            </div>
          )}
        </div>

        {/* status pill */}
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '4px 10px',
            borderRadius: 999,
            background: sm.bg,
            color: sm.fg,
            fontSize: 12,
            fontWeight: 500,
            flexShrink: 0,
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: sm.dot,
              flexShrink: 0,
            }}
          />
          {sm.text}
        </span>

        {/* cost */}
        {card.cost > 0 && (
          <span
            style={{
              fontSize: 12,
              color: 'var(--hachi-muted)',
              fontVariantNumeric: 'tabular-nums',
              flexShrink: 0,
            }}
          >
            {fmtDollar(card.cost)}
          </span>
        )}

        {/* chevron */}
        <span
          style={{
            color: 'var(--hachi-muted)',
            display: 'inline-flex',
            transform: `rotate(${rot}deg)`,
            transition: 'transform var(--dur) var(--ease)',
            flexShrink: 0,
          }}
        >
          <Icon name="chevron-down" size={15} />
        </span>
      </button>

      {/* expanded detail */}
      {isExpanded && card.detail && (
        <div
          style={{
            padding: '0 14px 14px 41px',
            fontSize: 13,
            lineHeight: '21px',
            color: 'var(--hachi-muted)',
            fontFamily: 'var(--font-mono)',
            whiteSpace: 'pre-wrap',
          }}
        >
          {card.detail}
        </div>
      )}
    </div>
  )
}

// ---- RoutineCard ---------------------------------------------------

interface RoutineCardProps {
  routine: NonNullable<ChatMessage['routine']>
  msgId: number
}

function RoutineCard({ routine, msgId }: RoutineCardProps) {
  const dryRunRoutine = useStore((s) => s.dryRunRoutine)
  const createRoutine = useStore((s) => s.createRoutine)

  const steps = routine.steps ?? []
  const needs = routine.needs ?? []
  const created = !!routine.created
  const notCreated = !created

  return (
    <div
      style={{
        border: '0.5px solid var(--hachi-line)',
        borderRadius: 14,
        background: 'var(--hachi-surface)',
        padding: 18,
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
      }}
    >
      {/* title row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ color: 'var(--hachi-trust)', display: 'inline-flex' }}>
          <Icon name="repeat" size={18} />
        </span>
        <span
          style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 500,
            fontSize: 16,
          }}
        >
          {routine.title}
        </span>
      </div>

      {/* trigger */}
      {routine.trigger && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 13,
            color: 'var(--hachi-muted)',
          }}
        >
          <Icon name="calendar" size={14} />
          {routine.trigger}
        </div>
      )}

      {/* steps */}
      {steps.length > 0 && (
        <div>
          <div style={{ fontSize: 12, color: 'var(--hachi-muted)', marginBottom: 7 }}>
            Steps
          </div>
          {steps.map((step, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 9,
                fontSize: 14,
                padding: '4px 0',
              }}
            >
              <span
                style={{
                  width: 5,
                  height: 5,
                  borderRadius: '50%',
                  background: 'var(--hachi-trust)',
                  flexShrink: 0,
                }}
              />
              {step}
            </div>
          ))}
        </div>
      )}

      {/* footer: needs + cost */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderTop: '0.5px solid var(--hachi-line)',
          paddingTop: 12,
        }}
      >
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
          {needs.map((need, i) => (
            <span
              key={i}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                padding: '4px 9px',
                borderRadius: 999,
                background: 'var(--hachi-cream)',
                fontSize: 12,
                color: 'var(--hachi-ink)',
              }}
            >
              <Icon name="leash" size={12} />
              {need}
            </span>
          ))}
        </div>
        {typeof routine.costPerRun === 'number' && (
          <span
            style={{
              fontSize: 13,
              color: 'var(--hachi-muted)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {fmtDollar(routine.costPerRun)}/run
          </span>
        )}
      </div>

      {/* actions (not yet created) */}
      {notCreated && (
        <div style={{ display: 'flex', gap: 9 }}>
          <button
            type="button"
            onClick={() => dryRunRoutine(msgId)}
            style={{
              flex: 1,
              height: 38,
              borderRadius: 10,
              border: '1px solid var(--hachi-trust)',
              background: 'transparent',
              color: 'var(--hachi-trust)',
              font: '500 13px var(--font-body)',
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 7,
            }}
            onMouseEnter={(e) => {
              ;(e.currentTarget as HTMLButtonElement).style.background =
                'var(--hachi-trust-tint)'
            }}
            onMouseLeave={(e) => {
              ;(e.currentTarget as HTMLButtonElement).style.background = 'transparent'
            }}
          >
            <Icon name="play" size={14} />
            Try once (dry run)
          </button>
          <button
            type="button"
            onClick={() => createRoutine(msgId)}
            style={{
              flex: 1,
              height: 38,
              borderRadius: 10,
              border: '0.5px solid transparent',
              background: 'var(--hachi-brand-hover)',
              color: 'var(--hachi-canvas)',
              font: '500 13px var(--font-body)',
              cursor: 'pointer',
            }}
            onMouseEnter={(e) => {
              ;(e.currentTarget as HTMLButtonElement).style.background =
                'var(--hachi-brand-active)'
            }}
            onMouseLeave={(e) => {
              ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-brand-hover)'
            }}
          >
            Create routine
          </button>
        </div>
      )}

      {/* created confirmation */}
      {created && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 13,
            color: '#2C7A50',
            background: 'var(--hachi-success-tint)',
            padding: '9px 12px',
            borderRadius: 10,
          }}
        >
          <Icon name="check" size={15} />
          On watch — runs weekdays at 8:00
        </div>
      )}
    </div>
  )
}

// ---- TtsButton -----------------------------------------------------

function TtsButton({ text }: { text: string }) {
  const [speaking, setSpeaking] = useState(false)

  function handleSpeak() {
    if (!('speechSynthesis' in window)) return
    if (speaking) {
      window.speechSynthesis.cancel()
      setSpeaking(false)
      return
    }
    // Strip markdown for a cleaner read-aloud experience.
    const plain = text
      .replace(/```[\s\S]*?```/g, '')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/[*_~#>]/g, '')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .trim()
    const utterance = new SpeechSynthesisUtterance(plain)
    utterance.onend = () => setSpeaking(false)
    utterance.onerror = () => setSpeaking(false)
    window.speechSynthesis.speak(utterance)
    setSpeaking(true)
  }

  if (!('speechSynthesis' in window)) return null

  return (
    <button
      type="button"
      onClick={handleSpeak}
      title={speaking ? 'Stop speaking' : 'Read aloud'}
      style={{
        alignSelf: 'flex-start',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        padding: '3px 9px',
        borderRadius: 8,
        border: '0.5px solid var(--hachi-line)',
        background: 'transparent',
        color: speaking ? 'var(--hachi-brand)' : 'var(--hachi-muted)',
        font: '500 12px var(--font-body)',
        cursor: 'pointer',
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-cream)' }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
    >
      <Icon name="volume-2" size={13} />
      {speaking ? 'Stop' : 'Listen'}
    </button>
  )
}

// ---- StreamingCursor -----------------------------------------------
// Isolated so its CSS animation doesn't force the whole message list to
// re-render on every animation frame. Only renders when isStreaming=true.

const StreamingCursor = memo(function StreamingCursor() {
  return (
    <span
      style={{
        display: 'inline-block',
        width: 8,
        height: 17,
        background: 'var(--hachi-brand)',
        marginLeft: 2,
        verticalAlign: 'text-bottom',
        animation: 'hachiBlink 1s steps(1) infinite',
      }}
    />
  )
})

// ---- StreamingText -------------------------------------------------
// Renders the actively-streaming assistant text as append-only, memoized
// chunks. CRITICAL for performance: rendering `<span>{fullText}</span>` and
// growing `fullText` every flush forces the browser to re-lay-out the ENTIRE
// message on each update — O(size) per flush, O(size²) over the response, which
// hangs the main thread (RESULT_CODE_HUNG) on long replies. By splitting into
// fixed-size chunks, every chunk except the last is a stable string: memo()
// skips re-rendering them, so React touches no DOM and the browser re-flows only
// the final (small) chunk. Streaming cost becomes O(size) total.
const STREAM_CHUNK_SIZE = 2048

const StreamingChunk = memo(function StreamingChunk({ text }: { text: string }) {
  return <>{text}</>
})

const StreamingText = memo(function StreamingText({ text }: { text: string }) {
  const chunks: string[] = []
  for (let i = 0; i < text.length; i += STREAM_CHUNK_SIZE) {
    chunks.push(text.slice(i, i + STREAM_CHUNK_SIZE))
  }
  return (
    <span style={{ whiteSpace: 'pre-wrap' }}>
      {chunks.map((c, i) => (
        <StreamingChunk key={i} text={c} />
      ))}
    </span>
  )
})

// ---- MessageItem ---------------------------------------------------

interface MessageItemProps {
  msg: ChatMessage
  isLast: boolean
  isStreaming: boolean
  operator: boolean
  onRetry?: () => void
}

// Wrapped in React.memo so that updating the last streaming message text does
// NOT re-render every prior (stable) message in the list.
const MessageItem = memo(function MessageItem({ msg, isLast, isStreaming, operator, onRetry }: MessageItemProps) {
  const isYou = msg.role === 'you'
  const isHachi = msg.role === 'hachi'
  const showCursor = isStreaming && isHachi && isLast
  // Detect error state: streaming ended but message text is the error string
  const isError = isHachi && !isStreaming && msg.text === 'Something went wrong — please try again.'

  if (isYou) {
    const hasAttachments = msg.attachments && msg.attachments.length > 0
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <div
          style={{
            maxWidth: '80%',
            minWidth: 0,
            background: 'var(--hachi-cream)',
            border: '0.5px solid var(--hachi-line)',
            borderRadius: '16px 16px 4px 16px',
            padding: '12px 16px',
            fontSize: 15,
            lineHeight: '24px',
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            overflowWrap: 'anywhere',
            wordBreak: 'break-word',
          }}
        >
          {hasAttachments && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {msg.attachments!.map((a, i) => (
                <span
                  key={i}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 5,
                    padding: '3px 9px',
                    borderRadius: 999,
                    background: 'var(--hachi-surface)',
                    border: '0.5px solid var(--hachi-line)',
                    fontSize: 12,
                    color: 'var(--hachi-muted)',
                    maxWidth: 200,
                  }}
                >
                  <Icon name={a.mediaType.startsWith('image/') ? 'image' : 'file'} size={12} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {a.name}
                  </span>
                </span>
              ))}
            </div>
          )}
          {msg.text && <Markdown text={msg.text} />}
        </div>
      </div>
    )
  }

  if (isHachi) {
    const hasRoutine = !!(msg.routine && msg.routine.title)

    return (
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        {/* Hachi avatar */}
        <div
          style={{
            flexShrink: 0,
            width: 34,
            height: 34,
            borderRadius: 10,
            background: 'var(--hachi-cream)',
            display: 'grid',
            placeItems: 'center',
            marginTop: 2,
          }}
        >
          <HachiMark size={22} />
        </div>

        {/* message body */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            paddingTop: 4,
          }}
        >
          {/* text + blinking cursor (cursor is isolated to avoid re-rendering siblings) */}
          <div
            style={{
              fontSize: 15,
              lineHeight: '24px',
              color: isError ? 'var(--hachi-danger)' : 'var(--hachi-ink)',
              overflowWrap: 'anywhere',
              wordBreak: 'break-word',
              minWidth: 0,
            }}
          >
            {showCursor ? (
              <StreamingText text={msg.text} />
            ) : (
              <Markdown text={msg.text} />
            )}
            {showCursor && <StreamingCursor />}
          </div>

          {/* TTS speak button — only on completed messages with text */}
          {!showCursor && msg.text && !isError && (
            <TtsButton text={msg.text} />
          )}

          {/* retry button on error */}
          {isError && onRetry && (
            <button
              type="button"
              onClick={onRetry}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                height: 32,
                padding: '0 12px',
                borderRadius: 8,
                border: '1px solid var(--hachi-danger)',
                background: 'transparent',
                color: 'var(--hachi-danger)',
                font: '500 13px var(--font-body)',
                cursor: 'pointer',
                alignSelf: 'flex-start',
              }}
              onMouseEnter={(e) => {
                ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-danger-tint)'
              }}
              onMouseLeave={(e) => {
                ;(e.currentTarget as HTMLButtonElement).style.background = 'transparent'
              }}
            >
              <Icon name="repeat" size={13} />
              Retry
            </button>
          )}

          {/* action cards */}
          {msg.cards.map((card) => (
            <ActionCardRow
              key={card.key}
              card={card}
              msgId={msg.id}
              operator={operator}
            />
          ))}

          {/* routine proposal card */}
          {hasRoutine && msg.routine && (
            <RoutineCard routine={msg.routine} msgId={msg.id} />
          )}
        </div>
      </div>
    )
  }

  return null
})

// ---- Conversation component ----------------------------------------

function Conversation() {
  const messages = useStore((s) => s.messages)
  const streaming = useStore((s) => s.streaming)
  const inContext = useStore((s) => s.inContext)
  const operator = useStore((s) => s.operator)
  const taskSpent = useStore((s) => s.taskSpent)
  const budget = useStore((s) => s.budget)
  const sessions = useStore((s) => s.sessions)
  const integrations = useStore((s) => s.integrations)
  const startTask = useStore((s) => s.startTask)
  const icAllow = useStore((s) => s.icAllow)
  const icAlwaysAllow = useStore((s) => s.icAlwaysAllow)
  const icDeny = useStore((s) => s.icDeny)

  const switchSession = useStore((s) => s.switchSession)
  const newSession = useStore((s) => s.newSession)
  const dataMode = useStore((s) => s.dataMode)
  const showToast = useStore((s) => s.showToast)

  const scrollRef = useRef<HTMLDivElement>(null)
  const followOutputRef = useRef(true)
  // M8: session picker open state
  const [pickerOpen, setPickerOpen] = useState(false)

  // The transcript is intentionally NOT virtualized. Messages are hard-capped at
  // 400 (store.capMessages) — a safe DOM size — and a dynamic virtualizer's
  // ResizeObserver measurement of the actively-streaming (continuously-growing)
  // message caused a main-thread freeze (RESULT_CODE_HUNG). A plain memoized
  // list is robust: React.memo on MessageItem means only the streaming message
  // re-renders during a turn; all prior messages stay stable. The streaming text
  // itself is chunked (StreamingText) so its own re-render cost is bounded.
  const lastMsg = messages.length > 0 ? messages[messages.length - 1] : null
  const isStreamingLast = streaming && lastMsg?.role === 'hachi'

  // Refresh recent sessions list on navigate-to (idempotent loader)
  useEffect(() => { void loadSessions() }, [])

  // Restore conversation history on mount and when the active session changes.
  // Guards: skipped in mock mode, skipped if a stream is in-flight.
  const activeSessionId = sessions.find((s) => s.active)?.id ?? null
  useEffect(() => {
    void loadConversationHistory()
  // Re-run whenever the active session id changes (e.g. user switches sessions
  // or the sessions list loads for the first time on a refresh).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionId])

  // Abort the SSE stream when the conversation screen unmounts so the reader
  // is released and the idle timeout doesn't keep it alive in the background.
  useEffect(() => {
    return () => {
      abortSseLive()
    }
  }, [])

  // Follow output while the user is near the bottom: a single scrollTop write
  // when the message count changes or the streaming text grows. No measurement
  // observers, no virtualizer scroll listener — nothing that can feed back.
  const streamingLen = isStreamingLast ? lastMsg?.text.length ?? 0 : 0
  useEffect(() => {
    if (followOutputRef.current && scrollRef.current) {
      const el = scrollRef.current
      el.scrollTop = el.scrollHeight
    }
  }, [messages.length, streamingLen])

  const handleTranscriptScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    followOutputRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96
  }, [])

  const chatEmpty = messages.length === 0

  // Computed state text for header (prototype logic line 2187).
  // HACHI-002: derive `working` from `streaming` alone — the old empty-bubble
  // clause ( m.text === '' ) pinned "Hachi is working" forever when a POST
  // failed and left an empty, non-streaming assistant bubble.
  const working = streaming
  const chatState = working ? 'Hachi is working' : 'Hachi is on watch'

  // LOW-020: active session title (most recent session)
  const activeSession = sessions.find((s) => s.active) ?? sessions[0] ?? null
  const sessionLabel = activeSession?.title ?? 'New conversation'

  // HIGH-019: filter chat starters to those whose integration is connected (or have no requirement)
  const connectedIntegrationNames = new Set(
    (integrations as Integration[]).filter((i) => i.status === 'connected').map((i) => i.name)
  )
  const visibleStarters = CHAT_STARTERS.filter(
    (s) => !s.requiresIntegration || connectedIntegrationNames.has(s.requiresIntegration)
  )

  const handleDeleteSession = (sessionId: string) => {
    if (dataMode !== 'live') {
      showToast('Session deletion is available in live mode only', 'blocked')
      return
    }
    void (async () => {
      const ok = await deleteSessionLive(sessionId)
      if (ok) {
        showToast('Session deleted', 'success')
      } else {
        showToast('Could not delete session — please try again', 'blocked')
      }
    })()
  }

  // MED-020: retry handler — resend the last "you" message
  const handleRetry = useCallback(() => {
    const currentMessages = useStore.getState().messages
    for (let i = currentMessages.length - 1; i >= 0; i -= 1) {
      if (currentMessages[i].role === 'you') {
        startTask(currentMessages[i].text)
        return
      }
    }
  }, [startTask])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* ---- header ---- */}
      <div
        className="hachi-conv-header"
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
          Conversation
        </h1>
        {/* M8 MED-020: session title — clickable to open session picker */}
        <div style={{ position: 'relative' }}>
          <button
            type="button"
            aria-haspopup="menu"
            aria-expanded={pickerOpen}
            onClick={() => setPickerOpen((v) => !v)}
            title={sessionLabel}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              maxWidth: 200,
              padding: '4px 8px',
              borderRadius: 8,
              border: '0.5px solid transparent',
              background: pickerOpen ? 'var(--hachi-cream)' : 'transparent',
              cursor: 'pointer',
              font: 'inherit',
              fontSize: 13,
              color: 'var(--hachi-muted)',
            }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-cream)' }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = pickerOpen ? 'var(--hachi-cream)' : 'transparent' }}
          >
            <span
              style={{
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {sessionLabel}
            </span>
            <span style={{ display: 'inline-flex', flexShrink: 0, transform: pickerOpen ? 'rotate(180deg)' : 'none', transition: 'transform var(--dur) var(--ease)' }}>
              <Icon name="chevron-down" size={12} />
            </span>
          </button>
          {pickerOpen && (
            <SessionPicker
              sessions={sessions}
              activeId={activeSession?.id ?? null}
              onSwitch={(id) => switchSession(id)}
              onNew={() => newSession()}
              onDelete={handleDeleteSession}
              onClose={() => setPickerOpen(false)}
            />
          )}
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 13,
            color: 'var(--hachi-muted)',
          }}
        >
          <span>{chatState}</span>
        </div>
        <div style={{ flex: 1 }} />
        <span
          style={{
            fontSize: 13,
            color: 'var(--hachi-muted)',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          spent {fmtDollar(taskSpent)} of {fmtDollar(budget)} this task
        </span>
      </div>

      {/* ---- transcript scroll area ---- */}
      <div
        ref={scrollRef}
        onScroll={handleTranscriptScroll}
        className="hachi-scroll"
        style={{ flex: 1, overflow: 'auto', padding: '28px 0' }}
      >
        <div
          className="hachi-transcript-inner"
          style={{
            maxWidth: 760,
            margin: '0 auto',
            padding: '0 32px',
          }}
        >
          {/* ---- empty / welcome state ---- */}
          {chatEmpty && (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                textAlign: 'center',
                gap: 18,
                padding: '48px 0',
              }}
            >
              <HachiMark size={56} />
              <div>
                <div
                  style={{
                    fontFamily: 'var(--font-display)',
                    fontWeight: 500,
                    fontSize: 22,
                    marginBottom: 6,
                  }}
                >
                  Hachi is on watch
                </div>
                <div style={{ fontSize: 15, color: 'var(--hachi-muted)' }}>
                  What would you like to do? Hachi works on your machine and asks before anything
                  risky.
                </div>
              </div>
              {/* HIGH-019: only show starters whose integration is connected */}
              {visibleStarters.length > 0 && (
                <div
                  style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: 9,
                    justifyContent: 'center',
                    maxWidth: 560,
                  }}
                >
                  {visibleStarters.map((s) => (
                    <StarterButton
                      key={s.label}
                      label={s.label}
                      icon={s.icon}
                      onClick={() => startTask(s.text)}
                    />
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ---- message list (plain, memoized; capped at 400 messages) ---- */}
          {!chatEmpty && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20, width: '100%' }}>
              {messages.map((msg, idx) => {
                const isLast = idx === messages.length - 1
                const streamingThis = isLast && isStreamingLast
                return (
                  // `contain: content` on the streaming row bounds its reflow/paint
                  // so the growing text never re-lays-out the messages above it.
                  <div key={msg.id} style={streamingThis ? { contain: 'content' } : undefined}>
                    <MessageItem
                      msg={msg}
                      isLast={isLast}
                      isStreaming={streamingThis}
                      operator={operator}
                      onRetry={handleRetry}
                    />
                  </div>
                )
              })}
            </div>
          )}

          {/* ---- in-context leash prompt ---- */}
          {inContext && (
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              {/* spacer aligns with Hachi avatar column */}
              <div style={{ width: 34, flexShrink: 0 }} />
              <LeashPrompt
                request={inContext.request}
                action={inContext.action}
                why={inContext.why}
                onAllowOnce={icAllow}
                onAlwaysAllow={icAlwaysAllow}
                onDecline={icDeny}
              />
            </div>
          )}
        </div>
      </div>

      {/* ---- composer ---- */}
      <ConversationComposer onOpenSessionsPicker={() => setPickerOpen(true)} />

    </div>
  )
}

interface ConversationComposerProps {
  onOpenSessionsPicker: () => void
}

const ConversationComposer = memo(function ConversationComposer({ onOpenSessionsPicker }: ConversationComposerProps) {
  const composer = useStore((s) => s.composer)
  const slashOpen = useStore((s) => s.slashOpen)
  const streaming = useStore((s) => s.streaming)
  const pendingAttachments = useStore((s) => s.pendingAttachments)
  const setComposer = useStore((s) => s.setComposer)
  const startTask = useStore((s) => s.startTask)
  const stopStreaming = useStore((s) => s.stopStreaming)
  const addPendingAttachment = useStore((s) => s.addPendingAttachment)
  const updatePendingAttachment = useStore((s) => s.updatePendingAttachment)
  const removePendingAttachment = useStore((s) => s.removePendingAttachment)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [slashIdx, setSlashIdx] = useState(-1)
  const [uploading, setUploading] = useState(false)
  // STT state — use `any` because Web Speech API lacks standard TS lib typings
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const recognitionRef = useRef<any>(null)
  const [listening, setListening] = useState(false)

  useEffect(() => {
    if (!slashOpen) setSlashIdx(-1)
  }, [slashOpen])

  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'
  }, [composer])

  const canSend = (composer.trim().length > 0 || pendingAttachments.length > 0) && !streaming

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setComposer(e.target.value)
  }

  function handleSend() {
    if (canSend) startTask(composer)
  }

  function handleSlashPick(cmd: string) {
    if (cmd === '/sessions') {
      setComposer('')
      setSlashIdx(-1)
      onOpenSessionsPicker()
      return
    }
    setComposer(cmd + ' ')
    setSlashIdx(-1)
  }

  // ── File picker ──────────────────────────────────────────────
  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? [])
    if (!files.length) return
    // Reset input so picking the same file again re-triggers
    e.target.value = ''

    for (const file of files) {
      // Unique local key so we can update the chip once the server responds.
      const localId = `local-${Date.now()}-${Math.random().toString(36).slice(2)}`
      // 1. Show the chip immediately with uploading=true.
      const optimistic = makeOptimisticAttachment(file, localId)
      addPendingAttachment(optimistic)
      setUploading(true)

      // 2. Upload in the background; update or error the chip when done.
      // Pass the already-determined mediaType so the server gets it via ?media_type=
      // even when the browser omits the multipart Content-Type (HEIC, AVIF, etc.).
      uploadFile(file, optimistic.mediaType)
        .then(async (realFileId) => {
          const meta = await getFileMeta(realFileId)
          updatePendingAttachment(localId, {
            fileId: realFileId,
            uploading: false,
            name: meta?.name ?? file.name,
            mediaType: meta?.media_type ?? optimistic.mediaType,
            size: meta?.size ?? file.size,
          })
        })
        .catch((err: unknown) => {
          const msg = err instanceof Error ? err.message : 'Upload failed'
          updatePendingAttachment(localId, { uploading: false, error: msg })
        })
        .finally(() => {
          setUploading(false)
        })
    }
  }

  // ── Voice input (STT) ────────────────────────────────────────
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = typeof window !== 'undefined' ? (window as any) : null
  const hasSpeechRecognition = !!(w?.SpeechRecognition || w?.webkitSpeechRecognition)

  function handleMic() {
    const SR = w?.SpeechRecognition ?? w?.webkitSpeechRecognition
    if (!SR) return

    if (listening && recognitionRef.current) {
      recognitionRef.current.stop()
      setListening(false)
      return
    }

    const rec = new SR()
    rec.lang = 'en-US'
    rec.interimResults = true
    rec.continuous = false
    recognitionRef.current = rec

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    rec.onresult = (event: any) => {
      const transcript = Array.from(event.results as ArrayLike<{ 0: { transcript: string } }>)
        .map((r) => r[0].transcript)
        .join('')
      setComposer(transcript)
    }
    rec.onend = () => setListening(false)
    rec.onerror = () => setListening(false)

    rec.start()
    setListening(true)
  }

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSlashIdx((i) => Math.min(i + 1, SLASH_ITEMS.length - 1))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSlashIdx((i) => Math.max(i - 1, 0))
        return
      }
      if (e.key === 'Enter' && slashIdx >= 0) {
        e.preventDefault()
        handleSlashPick(SLASH_ITEMS[slashIdx].cmd)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setComposer('')
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (canSend) startTask(composer)
    }
  }, [slashOpen, slashIdx, canSend, composer, startTask, setComposer])

  return (
    <div
      className="hachi-composer-wrap"
      style={{
        flexShrink: 0,
        borderTop: '0.5px solid var(--hachi-line)',
        background: 'var(--hachi-canvas)',
        padding: '16px 32px 22px',
      }}
    >
      <div
        style={{
          maxWidth: 760,
          margin: '0 auto',
          position: 'relative',
        }}
      >
        {slashOpen && (
          <div
            style={{
              position: 'absolute',
              bottom: 'calc(100% + 8px)',
              left: 0,
              right: 0,
              background: 'var(--hachi-surface)',
              border: '0.5px solid var(--hachi-line)',
              borderRadius: 12,
              overflow: 'hidden',
              boxShadow: '0 8px 24px rgba(28,26,32,.08)',
              zIndex: 10,
            }}
            role="listbox"
            aria-label="Slash commands"
          >
            {SLASH_ITEMS.map((item, idx) => (
              <SlashItem
                key={item.cmd}
                cmd={item.cmd}
                desc={item.desc}
                active={idx === slashIdx}
                onPick={() => handleSlashPick(item.cmd)}
              />
            ))}
          </div>
        )}

        {/* Pending attachment chips */}
        {pendingAttachments.length > 0 && (
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 6,
              padding: '0 4px 10px',
            }}
          >
            {pendingAttachments.map((a) => (
              <AttachmentChip
                key={a.fileId}
                attachment={a}
                onRemove={() => {
                  if (a.localUrl) URL.revokeObjectURL(a.localUrl)
                  removePendingAttachment(a.fileId)
                }}
              />
            ))}
          </div>
        )}

        <div
          style={{
            display: 'flex',
            gap: 10,
            alignItems: 'flex-end',
            background: 'var(--hachi-surface)',
            border: '0.5px solid var(--hachi-line)',
            borderRadius: 14,
            padding: '8px 8px 8px 12px',
          }}
        >
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/*,text/*,application/json,application/pdf,.md,.csv,.ts,.tsx,.js,.jsx,.py,.go,.rs,.java"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />

          {/* File attach button */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading || streaming}
            title="Attach file or image"
            style={{
              flexShrink: 0,
              width: 34,
              height: 34,
              borderRadius: 8,
              border: 'none',
              background: 'transparent',
              color: uploading ? 'var(--hachi-brand)' : 'var(--hachi-muted)',
              display: 'grid',
              placeItems: 'center',
              cursor: uploading || streaming ? 'default' : 'pointer',
              opacity: streaming ? 0.4 : 1,
            }}
            onMouseEnter={(e) => {
              if (!uploading && !streaming) {
                (e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-cream)'
              }
            }}
            onMouseLeave={(e) => {
              ;(e.currentTarget as HTMLButtonElement).style.background = 'transparent'
            }}
          >
            <Icon name={uploading ? 'refresh' : 'paperclip'} size={16} />
          </button>

          {/* Mic button */}
          {hasSpeechRecognition && (
            <button
              type="button"
              onClick={handleMic}
              disabled={streaming}
              title={listening ? 'Stop listening' : 'Speak your message'}
              style={{
                flexShrink: 0,
                width: 34,
                height: 34,
                borderRadius: 8,
                border: 'none',
                background: listening ? 'var(--hachi-danger-tint)' : 'transparent',
                color: listening ? 'var(--hachi-danger)' : 'var(--hachi-muted)',
                display: 'grid',
                placeItems: 'center',
                cursor: streaming ? 'default' : 'pointer',
                opacity: streaming ? 0.4 : 1,
              }}
              onMouseEnter={(e) => {
                if (!streaming && !listening) {
                  (e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-cream)'
                }
              }}
              onMouseLeave={(e) => {
                if (!listening) {
                  (e.currentTarget as HTMLButtonElement).style.background = 'transparent'
                }
              }}
            >
              <Icon name={listening ? 'mic-off' : 'mic'} size={16} />
            </button>
          )}

          <textarea
            ref={textareaRef}
            value={composer}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder={listening ? 'Listening…' : 'Message Hachi…  /  for commands'}
            aria-label="Message Hachi"
            aria-autocomplete="list"
            aria-expanded={slashOpen}
            style={{
              flex: 1,
              resize: 'none',
              border: 'none',
              background: 'transparent',
              font: 'inherit',
              fontSize: 15,
              lineHeight: '24px',
              color: 'var(--hachi-ink)',
              outline: 'none',
              padding: '9px 0',
              maxHeight: 140,
              overflowY: 'auto',
            }}
          />

          {streaming && (
            <button
              type="button"
              aria-label="Stop generating"
              onClick={stopStreaming}
              style={{
                flexShrink: 0,
                width: 42,
                height: 42,
                borderRadius: 10,
                border: '1px solid var(--hachi-trust)',
                background: 'transparent',
                color: 'var(--hachi-trust)',
                display: 'grid',
                placeItems: 'center',
                cursor: 'pointer',
              }}
              onMouseEnter={(e) => {
                ;(e.currentTarget as HTMLButtonElement).style.background =
                  'var(--hachi-trust-tint)'
              }}
              onMouseLeave={(e) => {
                ;(e.currentTarget as HTMLButtonElement).style.background = 'transparent'
              }}
            >
              <Icon name="stop" size={16} />
            </button>
          )}

          {canSend && (
            <button
              type="button"
              aria-label="Send message"
              onClick={handleSend}
              style={{
                flexShrink: 0,
                width: 42,
                height: 42,
                borderRadius: 10,
                border: '0.5px solid transparent',
                background: 'var(--hachi-brand-hover)',
                color: 'var(--hachi-canvas)',
                display: 'grid',
                placeItems: 'center',
                cursor: 'pointer',
              }}
              onMouseEnter={(e) => {
                ;(e.currentTarget as HTMLButtonElement).style.background =
                  'var(--hachi-brand-active)'
              }}
              onMouseLeave={(e) => {
                ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-brand-hover)'
              }}
            >
              <Icon name="send" size={16} />
            </button>
          )}
        </div>

        <div
          style={{
            fontSize: 12,
            color: 'var(--hachi-muted)',
            marginTop: 8,
            textAlign: 'center',
          }}
        >
          Hachi runs on your machine · it asks before anything risky · you can stop anytime
        </div>
      </div>
    </div>
  )
})

// ---- AttachmentChip ------------------------------------------------

interface AttachmentChipProps {
  attachment: PendingAttachment
  onRemove: () => void
}

function AttachmentChip({ attachment, onRemove }: AttachmentChipProps) {
  const isImage = attachment.mediaType.startsWith('image/')
  const hasError = !!attachment.error
  const canOpen = !attachment.uploading && !hasError && !attachment.fileId.startsWith('local-')

  // Derive a readable extension from the filename
  const ext = attachment.name.includes('.')
    ? attachment.name.split('.').pop()?.toUpperCase() ?? ''
    : ''
  const displayName = attachment.name.length > 24
    ? attachment.name.slice(0, 20) + '…' + (ext ? `.${ext.toLowerCase()}` : '')
    : attachment.name

  return (
    <div
      title={attachment.error ?? attachment.name}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '5px 6px 5px 8px',
        borderRadius: 10,
        background: hasError ? 'var(--hachi-danger-tint)' : 'var(--hachi-surface)',
        border: `0.5px solid ${hasError ? 'var(--hachi-danger)' : 'var(--hachi-line)'}`,
        fontSize: 12,
        color: hasError ? 'var(--hachi-danger)' : 'var(--hachi-ink)',
        maxWidth: 240,
        opacity: attachment.uploading ? 0.7 : 1,
        transition: 'opacity 0.15s',
      }}
    >
      {/* Thumbnail or icon */}
      {isImage && attachment.localUrl ? (
        <img
          src={attachment.localUrl}
          alt=""
          style={{ width: 20, height: 20, borderRadius: 4, objectFit: 'cover', flexShrink: 0 }}
        />
      ) : (
        <span style={{ color: hasError ? 'var(--hachi-danger)' : 'var(--hachi-muted)', display: 'inline-flex', flexShrink: 0 }}>
          <Icon name={hasError ? 'circle-alert' : (isImage ? 'image' : 'file')} size={13} />
        </span>
      )}

      {/* Name + extension */}
      <button
        type="button"
        onClick={() => {
          if (canOpen) void openFileContent(attachment.fileId)
        }}
        disabled={!canOpen}
        style={{
          display: 'flex',
          flexDirection: 'column',
          minWidth: 0,
          flex: 1,
          background: 'transparent',
          border: 'none',
          padding: 0,
          textAlign: 'left',
          cursor: canOpen ? 'pointer' : 'default',
        }}
      >
        <span
          style={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontWeight: 500,
            lineHeight: '15px',
          }}
        >
          {displayName}
        </span>
        {/* Extension badge or upload status */}
        <span style={{ fontSize: 10, color: hasError ? 'var(--hachi-danger)' : 'var(--hachi-muted)', lineHeight: '13px' }}>
          {hasError
            ? (attachment.error && attachment.error.length <= 32
                ? attachment.error
                : 'Upload failed — hover for detail')
            : attachment.uploading
              ? 'Uploading…'
              : (ext || attachment.mediaType.split('/')[1] || 'file')}
        </span>
          </button>

      {/* Remove button */}
      <button
        type="button"
        onClick={onRemove}
        title="Remove"
        style={{
          flexShrink: 0,
          display: 'inline-flex',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          color: 'var(--hachi-muted)',
          padding: 2,
          borderRadius: 4,
          marginLeft: 2,
        }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = 'var(--hachi-danger)' }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = 'var(--hachi-muted)' }}
      >
        <Icon name="x" size={12} />
      </button>
    </div>
  )
}

// ---- Sub-components (kept local, no entrance animations) ------------

interface StarterButtonProps {
  label: string
  icon: string
  onClick: () => void
}

function StarterButton({ label, icon, onClick }: StarterButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 7,
        padding: '9px 14px',
        borderRadius: 999,
        border: '0.5px solid var(--hachi-line)',
        background: 'var(--hachi-surface)',
        color: 'var(--hachi-ink)',
        font: 'inherit',
        fontSize: 13,
        cursor: 'pointer',
      }}
      onMouseEnter={(e) => {
        ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-cream)'
      }}
      onMouseLeave={(e) => {
        ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-surface)'
      }}
    >
      <span style={{ color: 'var(--hachi-muted)', display: 'inline-flex' }}>
        <Icon name={icon} size={14} />
      </span>
      {label}
    </button>
  )
}

interface SlashItemProps {
  cmd: string
  desc: string
  active: boolean
  onPick: () => void
}

function SlashItem({ cmd, desc, active, onPick }: SlashItemProps) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      onClick={onPick}
      style={{
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        gap: 11,
        padding: '11px 14px',
        border: 'none',
        borderBottom: '0.5px solid var(--hachi-line)',
        background: active ? 'var(--hachi-cream)' : 'transparent',
        cursor: 'pointer',
        textAlign: 'left',
        font: 'inherit',
      }}
      onMouseEnter={(e) => {
        ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-cream)'
      }}
      onMouseLeave={(e) => {
        ;(e.currentTarget as HTMLButtonElement).style.background = active ? 'var(--hachi-cream)' : 'transparent'
      }}
    >
      <span
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 13,
          color: 'var(--hachi-brand)',
          minWidth: 96,
        }}
      >
        {cmd}
      </span>
      <span style={{ fontSize: 13, color: 'var(--hachi-muted)' }}>{desc}</span>
    </button>
  )
}

// ---- Route export (TanStack Router file-based routing) --------------

export const Route = createFileRoute('/conversation')({
  component: Conversation,
})
