// Conversation screen — /conversation
// Faithfully ports prototype markup lines 212–336 and logic handlers.
// Screen-local view state only in useState; all domain mutations go through useStore.

import React, { useEffect, useRef, useState, useCallback } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useStore } from '../lib/store/store'
import { Icon } from '../components/icons/Icon'
import { HachiMark } from '../components/brand/HachiMark'
import { LeashPrompt } from '../components/brand/LeashPrompt'
import type { ChatMessage, ActionCard, Integration } from '../lib/data/types'
// Side-effect import: registers the "chat" loader with bootstrapLiveData()
// and makes loadSessions available for the per-route refresh below.
import { loadSessions } from '../lib/data/domains/chat'
import { Markdown } from '../components/ui/Markdown'

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
              background: 'var(--hachi-brand)',
              color: 'var(--hachi-canvas)',
              font: '500 13px var(--font-body)',
              cursor: 'pointer',
            }}
            onMouseEnter={(e) => {
              ;(e.currentTarget as HTMLButtonElement).style.background =
                'var(--hachi-brand-hover)'
            }}
            onMouseLeave={(e) => {
              ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-brand)'
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

// ---- MessageItem ---------------------------------------------------

interface MessageItemProps {
  msg: ChatMessage
  isLast: boolean
  isStreaming: boolean
  operator: boolean
  onRetry?: () => void
}

function MessageItem({ msg, isLast, isStreaming, operator, onRetry }: MessageItemProps) {
  const isYou = msg.role === 'you'
  const isHachi = msg.role === 'hachi'
  const showCursor = isStreaming && isHachi && isLast
  // Detect error state: streaming ended but message text is the error string
  const isError = isHachi && !isStreaming && msg.text === 'Something went wrong — please try again.'

  if (isYou) {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <div
          style={{
            maxWidth: '80%',
            background: 'var(--hachi-cream)',
            border: '0.5px solid var(--hachi-line)',
            borderRadius: '16px 16px 4px 16px',
            padding: '12px 16px',
            fontSize: 15,
            lineHeight: '24px',
          }}
        >
          <Markdown text={msg.text} />
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
          {/* text + blinking cursor */}
          <div
            style={{
              fontSize: 15,
              lineHeight: '24px',
              color: isError ? 'var(--hachi-danger)' : 'var(--hachi-ink)',
            }}
          >
            <Markdown text={msg.text} />
            {showCursor && (
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
            )}
          </div>

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
}

// ---- Conversation component ----------------------------------------

function Conversation() {
  const messages = useStore((s) => s.messages)
  const composer = useStore((s) => s.composer)
  const slashOpen = useStore((s) => s.slashOpen)
  const streaming = useStore((s) => s.streaming)
  const inContext = useStore((s) => s.inContext)
  const operator = useStore((s) => s.operator)
  const taskSpent = useStore((s) => s.taskSpent)
  const budget = useStore((s) => s.budget)
  const sessions = useStore((s) => s.sessions)
  const integrations = useStore((s) => s.integrations)
  const setComposer = useStore((s) => s.setComposer)
  const startTask = useStore((s) => s.startTask)
  const stopStreaming = useStore((s) => s.stopStreaming)
  const icAllow = useStore((s) => s.icAllow)
  const icAlwaysAllow = useStore((s) => s.icAlwaysAllow)
  const icDeny = useStore((s) => s.icDeny)

  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  // E6-T5: slash command keyboard navigation index (-1 = none selected)
  const [slashIdx, setSlashIdx] = useState(-1)

  // Refresh recent sessions list on navigate-to (idempotent loader)
  useEffect(() => { void loadSessions() }, [])

  // Reset slash index when menu opens/closes
  useEffect(() => {
    if (!slashOpen) setSlashIdx(-1)
  }, [slashOpen])

  // Auto-scroll when messages change or streaming
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, streaming])

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'
  }, [composer])

  const chatEmpty = messages.length === 0
  const canSend = composer.trim().length > 0 && !streaming

  // Computed state text for header (prototype logic line 2187)
  const working = streaming || messages.some((m) => m.role === 'hachi' && m.text === '')
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

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setComposer(e.target.value)
  }

  // E6-T5: keyboard nav for slash menu
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

  function handleSend() {
    if (canSend) startTask(composer)
  }

  function handleSlashPick(cmd: string) {
    setComposer(cmd + ' ')
    setSlashIdx(-1)
  }

  // MED-020: retry handler — resend the last "you" message
  function handleRetry() {
    const lastYou = [...messages].reverse().find((m) => m.role === 'you')
    if (lastYou) {
      startTask(lastYou.text)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* ---- header ---- */}
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
          Conversation
        </h1>
        {/* LOW-020: session title */}
        <span
          style={{
            fontSize: 13,
            color: 'var(--hachi-muted)',
            maxWidth: 200,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={sessionLabel}
        >
          {sessionLabel}
        </span>
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
        className="hachi-scroll"
        style={{ flex: 1, overflow: 'auto', padding: '28px 0' }}
      >
        <div
          style={{
            maxWidth: 760,
            margin: '0 auto',
            padding: '0 32px',
            display: 'flex',
            flexDirection: 'column',
            gap: 20,
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

          {/* ---- message list ---- */}
          {messages.map((msg, idx) => (
            <MessageItem
              key={msg.id}
              msg={msg}
              isLast={idx === messages.length - 1}
              isStreaming={streaming}
              operator={operator}
              onRetry={handleRetry}
            />
          ))}

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
      <div
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
          {/* slash command menu */}
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

          {/* textarea row */}
          <div
            style={{
              display: 'flex',
              gap: 10,
              alignItems: 'flex-end',
              background: 'var(--hachi-surface)',
              border: '0.5px solid var(--hachi-line)',
              borderRadius: 14,
              padding: '8px 8px 8px 16px',
            }}
          >
            <textarea
              ref={textareaRef}
              value={composer}
              onChange={handleInput}
              onKeyDown={handleKeyDown}
              rows={1}
              placeholder="Message Hachi…  /  for commands"
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

            {/* stop button (streaming) */}
            {streaming && (
              <button
                type="button"
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

            {/* send button (can send) */}
            {canSend && (
              <button
                type="button"
                onClick={handleSend}
                style={{
                  flexShrink: 0,
                  width: 42,
                  height: 42,
                  borderRadius: 10,
                  border: '0.5px solid transparent',
                  background: 'var(--hachi-brand)',
                  color: 'var(--hachi-canvas)',
                  display: 'grid',
                  placeItems: 'center',
                  cursor: 'pointer',
                }}
                onMouseEnter={(e) => {
                  ;(e.currentTarget as HTMLButtonElement).style.background =
                    'var(--hachi-brand-hover)'
                }}
                onMouseLeave={(e) => {
                  ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-brand)'
                }}
              >
                <Icon name="send" size={16} />
              </button>
            )}
          </div>

          {/* footer hint */}
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
