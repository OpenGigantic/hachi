// MessageItem — renders a single chat turn (you/hachi), including
// attachments, action cards, routine proposals, and the streaming cursor.
// Wrapped in React.memo so that updating the last streaming message text does
// NOT re-render every prior (stable) message in the list.

import { memo } from 'react'
import { Icon } from '../icons/Icon'
import { HachiMark } from '../brand/HachiMark'
import { Markdown } from '../ui/Markdown'
import type { ChatMessage } from '../../lib/data/types'
import { attMeta } from './helpers'
import { TtsButton } from './TtsButton'
import { StreamingCursor } from './StreamingText'
import { ActionCardRow } from './ActionCardRow'
import { RoutineCard } from './RoutineCard'

interface MessageItemProps {
  msg: ChatMessage
  isLast: boolean
  isStreaming: boolean
  operator: boolean
  onRetry?: () => void
  lastUsedModel?: string | null
}

export const MessageItem = memo(function MessageItem({ msg, isLast, isStreaming, operator, onRetry, lastUsedModel }: MessageItemProps) {
  const isYou = msg.role === 'you'
  const isHachi = msg.role === 'hachi'
  const showCursor = isStreaming && isHachi && isLast
  // Detect error state: streaming ended but message text is the error string
  const isError = isHachi && !isStreaming && msg.text === 'Something went wrong — please try again.'

  if (isYou) {
    const hasAttachments = !!(msg.attachments && msg.attachments.length > 0)
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
        {/* attachments — chips above the bubble, right-aligned */}
        {hasAttachments && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, justifyContent: 'flex-end', maxWidth: '80%' }}>
            {msg.attachments!.map((a, i) => {
              const meta = attMeta(a.name, a.mediaType)
              return (
                <div
                  key={i}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    background: 'var(--hachi-surface)',
                    border: '0.5px solid var(--hachi-line)',
                    borderRadius: 10,
                    padding: '5px 9px 5px 5px',
                  }}
                >
                  <div
                    style={{
                      width: 30,
                      height: 30,
                      borderRadius: 6,
                      background: 'var(--hachi-cream)',
                      display: 'grid',
                      placeItems: 'center',
                      flexShrink: 0,
                      color: meta.tint,
                    }}
                  >
                    <Icon name={meta.icon} size={15} />
                  </div>
                  <span
                    style={{
                      fontSize: 12,
                      fontWeight: 500,
                      color: 'var(--hachi-ink)',
                      maxWidth: 130,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {a.name}
                  </span>
                </div>
              )
            })}
          </div>
        )}
        {/* text bubble */}
        {msg.text && (
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
              overflowWrap: 'anywhere',
              wordBreak: 'break-word',
            }}
          >
            <Markdown text={msg.text} />
          </div>
        )}
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
            {/* FIX 1: always render Markdown (even during streaming) so the DOM
                node is stable across the streaming→done transition (no remount/FOUC).
                The isStreaming prop tells the parser to treat unclosed fences as valid
                code blocks so the chrome appears immediately. */}
            <Markdown text={msg.text} isStreaming={showCursor} />
            {showCursor && <StreamingCursor />}
          </div>

          {/* TTS speak button — only on completed messages with text */}
          {!showCursor && msg.text && !isError && (
            <TtsButton text={msg.text} />
          )}

          {/* FIX 4: last-used model badge — only on final completed assistant message */}
          {!showCursor && isLast && lastUsedModel && (
            <div
              style={{
                fontSize: 11,
                color: 'var(--hachi-muted)',
                opacity: 0.7,
                fontFamily: 'var(--font-mono, monospace)',
                letterSpacing: '0.01em',
              }}
            >
              via {lastUsedModel}
            </div>
          )}

          {/* retry button on error */}
          {isError && onRetry && (
            <button
              type="button"
              className="hachi-danger-outline-btn"
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
