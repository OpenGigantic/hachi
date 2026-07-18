import React, { useState, useEffect, CSSProperties } from 'react'
import { HachiMark } from './HachiMark'
import { ConfirmButton } from '../ui/ConfirmButton'

/** localStorage key — suppress leash explanation after first interaction. */
const LEASH_EXPLAIN_KEY = 'hachi.leash-explain-dismissed'

export interface LeashPromptProps {
  /** Plain-language request headline, e.g. "Hachi wants to run a command". */
  request: string
  /** One-line reason rendered after "why:". */
  why: string
  /** The exact action shown verbatim in mono (e.g. "npm install"). Optional. */
  action?: string
  /** Risk qualifier appended in muted text, e.g. "writes outside workspace". Optional. */
  risk?: string
  /** Called when user taps "Allow once". */
  onAllowOnce: () => void
  /** Called when user taps "Always allow". */
  onAlwaysAllow: () => void
  /** Called when user taps "Decline". */
  onDecline: () => void
  /**
   * Remaining milliseconds before the prompt auto-declines.
   * When provided, renders "auto-declines in {label}" below the action block.
   * Supply a live value (e.g. from useCountdown) — this component is stateless.
   */
  countdown?: number
  style?: CSSProperties
}

/** Format a millisecond countdown as "1m 19s" / "45s". Clamps at 0. */
function fmtCountdown(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000))
  const m = Math.floor(s / 60)
  const sec = s % 60
  if (m > 0) return `${m}m ${sec}s`
  return `${sec}s`
}

/**
 * LeashPrompt — Hachi's signature calm permission card.
 *
 * Never looks like an error. Always a matter-of-fact question.
 * The caption "you hold the leash" anchors user control at the bottom.
 *
 * Button order (left→right): Decline · Allow once · Always allow.
 * Decline and Allow once carry EQUAL visual weight (both outlined) — the
 * safe default must never look like the "lesser" option next to a filled
 * red CTA (Nielsen error prevention). "Always allow" grants a *standing*
 * permission, so it gets a distinct amber-leaning outline and arms on the
 * first click, requiring a second click (or auto-disarms after 4s) to fire.
 *
 * Ported from design-system/components/hachi/LeashPrompt.jsx + contract §5.
 */
export function LeashPrompt({
  request,
  why,
  action,
  risk,
  onAllowOnce,
  onAlwaysAllow,
  onDecline,
  countdown,
  style,
}: LeashPromptProps) {
  const [showExplain, setShowExplain] = useState<boolean>(
    () => localStorage.getItem(LEASH_EXPLAIN_KEY) !== '1'
  )

  // Reset explanation visibility when a new prompt is presented (request changes while mounted).
  useEffect(() => {
    setShowExplain(localStorage.getItem(LEASH_EXPLAIN_KEY) !== '1')
  }, [request])

  /**
   * Mark explanation as seen.
   * localStorage.setItem can throw in Safari private browsing (QuotaExceededError);
   * we swallow the error so the downstream action callback always fires.
   */
  const markSeen = () => {
    try {
      localStorage.setItem(LEASH_EXPLAIN_KEY, '1')
    } catch {
      // Storage unavailable (e.g. private browsing quota) — continue without persisting.
    }
    setShowExplain(false)
  }

  const handleAllowOnce = () => { markSeen(); onAllowOnce() }
  const handleDecline = () => { markSeen(); onDecline() }

  const [declineFocused, setDeclineFocused] = useState(false)
  const [allowFocused, setAllowFocused] = useState(false)
  const focusRing = '2px solid var(--hachi-brand-tint, #FBE7E0)'

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-label={request}
      style={{
        width: 420,
        maxWidth: '100%',
        background: 'var(--surface-card, var(--hachi-surface, #FFFDF8))',
        border: '0.5px solid var(--border-hairline, var(--hachi-line, #E2D9C9))',
        borderRadius: 'var(--radius-lg, 16px)',
        padding: 24,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        ...style,
      }}
    >
      {/* Header row: mark avatar + request + why */}
      <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
        <div
          style={{
            flexShrink: 0,
            width: 48,
            height: 48,
            borderRadius: 'var(--radius-md, 10px)',
            background: 'var(--surface-cream, var(--hachi-cream, #F3E9D2))',
            display: 'grid',
            placeItems: 'center',
          }}
        >
          <HachiMark size={34} />
        </div>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
            paddingTop: 2,
            minWidth: 0,
          }}
        >
          <div
            style={{
              fontFamily: 'var(--font-body, Inter, system-ui, sans-serif)',
              fontSize: 17,
              fontWeight: 500,
              lineHeight: '24px',
              color: 'var(--text-default, var(--hachi-ink, #1C1A20))',
            }}
          >
            {request}
          </div>
          <div
            style={{
              fontFamily: 'var(--font-body, Inter, system-ui, sans-serif)',
              fontSize: 13,
              lineHeight: '20px',
              color: 'var(--text-muted, var(--hachi-muted, #696460))',
            }}
          >
            why: {why}
          </div>
        </div>
      </div>

      {/* Leash explanation — shown once on first encounter, suppressed after any interaction */}
      {showExplain && (
        <div
          style={{
            fontSize: 12,
            lineHeight: '18px',
            fontFamily: 'var(--font-body, Inter, system-ui, sans-serif)',
            color: 'var(--text-muted, var(--hachi-muted, #696460))',
            background: 'var(--hachi-trust-tint, #E4EAF3)',
            borderRadius: 'var(--radius-md, 10px)',
            padding: '8px 12px',
          }}
        >
          The leash keeps you in control — Hachi stops here for any risky or
          irreversible action and waits for your decision before proceeding.
        </div>
      )}

      {/* Action block (mono) — only rendered when action is provided */}
      {action && (
        <div
          style={{
            background: 'var(--surface-cream, var(--hachi-cream, #F3E9D2))',
            border: '0.5px solid var(--border-hairline, var(--hachi-line, #E2D9C9))',
            borderRadius: 'var(--radius-md, 10px)',
            padding: '12px 14px',
            fontFamily: 'var(--font-mono, JetBrains Mono, monospace)',
            fontSize: 13,
            lineHeight: '22px',
            color: 'var(--text-default, var(--hachi-ink, #1C1A20))',
            wordBreak: 'break-word',
          }}
        >
          <span style={{ color: 'var(--hachi-brand, #C94230)' }}>
            {'hachi ▸'}
          </span>{' '}
          {action}
        </div>
      )}

      {/* Risk qualifier — only rendered when risk is provided */}
      {risk && (
        <div
          style={{
            fontSize: 12,
            lineHeight: '18px',
            fontFamily: 'var(--font-body, Inter, system-ui, sans-serif)',
            color: 'var(--text-muted, var(--hachi-muted, #696460))',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <span
            style={{
              display: 'inline-block',
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: 'var(--hachi-danger, #C0392B)',
              flexShrink: 0,
            }}
          />
          {risk}
        </div>
      )}

      {/* Countdown line — only rendered when countdown prop is provided */}
      {countdown !== undefined && (
        <div
          style={{
            fontSize: 12,
            lineHeight: '18px',
            fontFamily: 'var(--font-mono, JetBrains Mono, monospace)',
            color: 'var(--text-muted, var(--hachi-muted, #696460))',
          }}
        >
          auto-declines in {fmtCountdown(countdown)}
        </div>
      )}

      {/* Actions: Decline · Allow once — equal weight, both outlined, neither
          looks like the "safe"/"lesser" option. Always allow (standing grant)
          gets a distinct amber outline and arms on first click. */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          type="button"
          className="hachi-leash-decline-btn"
          onClick={handleDecline}
          onFocus={() => setDeclineFocused(true)}
          onBlur={() => setDeclineFocused(false)}
          style={{
            flex: 1,
            height: 38,
            padding: '0 14px',
            borderRadius: 'var(--radius-md, 10px)',
            border: '1.5px solid var(--hachi-trust, #2F4B7C)',
            background: 'transparent',
            color: 'var(--hachi-trust, #2F4B7C)',
            fontFamily: 'var(--font-body, Inter, system-ui, sans-serif)',
            fontSize: 14,
            fontWeight: 500,
            cursor: 'pointer',
            outline: declineFocused ? focusRing : 'none',
            outlineOffset: 2,
            transition: 'background var(--dur-fast, 120ms) var(--ease, cubic-bezier(.2,.7,.3,1))',
          }}
        >
          Decline
        </button>
        <button
          type="button"
          className="hachi-leash-allow-once-btn"
          onClick={handleAllowOnce}
          onFocus={() => setAllowFocused(true)}
          onBlur={() => setAllowFocused(false)}
          style={{
            flex: 1,
            height: 38,
            padding: '0 14px',
            borderRadius: 'var(--radius-md, 10px)',
            border: '1.5px solid var(--hachi-brand, #C94230)',
            background: 'transparent',
            color: 'var(--hachi-brand, #C94230)',
            fontFamily: 'var(--font-body, Inter, system-ui, sans-serif)',
            fontSize: 14,
            fontWeight: 500,
            cursor: 'pointer',
            outline: allowFocused ? focusRing : 'none',
            outlineOffset: 2,
            transition: 'background var(--dur-fast, 120ms) var(--ease, cubic-bezier(.2,.7,.3,1))',
          }}
        >
          Allow once
        </button>
        <ConfirmButton
          label="Always allow"
          confirmLabel="Always allow — confirm"
          onConfirm={() => { markSeen(); onAlwaysAllow() }}
          tone="neutral"
          ariaLabel="Always allow — grants a standing permission"
          style={{
            flex: 1,
            height: 38,
            borderRadius: 'var(--radius-md, 10px)',
            fontFamily: 'var(--font-body, Inter, system-ui, sans-serif)',
            fontSize: 13,
          }}
        />
      </div>

      {/* Footer caption */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 13,
          lineHeight: '20px',
          fontFamily: 'var(--font-body, Inter, system-ui, sans-serif)',
          color: 'var(--text-muted, var(--hachi-muted, #696460))',
          borderTop: '0.5px solid var(--border-hairline, var(--hachi-line, #E2D9C9))',
          paddingTop: 12,
        }}
      >
        <HachiMark
          size={16}
          variant="knockout"
          style={{ color: 'var(--text-muted, var(--hachi-muted, #696460))' }}
        />
        you hold the leash
      </div>
    </div>
  )
}
