import { CSSProperties } from 'react';

export type ToastTone = 'success' | 'blocked' | 'info';

export interface ToastProps {
  message: string;
  tone?: ToastTone;
  onDismiss?: () => void;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}

const TONE_STYLES: Record<ToastTone, { bg: string; color: string; dot: string }> = {
  success: {
    bg: 'var(--hachi-success-tint)',
    color: 'var(--hachi-success-fg)',  /* -fg: text on tint bg (design-token contract) */
    dot: 'var(--hachi-success)',
  },
  blocked: {
    bg: 'var(--hachi-danger-tint)',
    color: 'var(--hachi-danger-fg)',   /* -fg: text on tint bg (design-token contract) */
    dot: 'var(--hachi-danger)',
  },
  info: {
    bg: 'var(--hachi-trust-tint)',
    color: 'var(--hachi-trust)',
    dot: 'var(--hachi-trust)',
  },
};

export function Toast({ message, tone = 'success', onDismiss, onMouseEnter, onMouseLeave }: ToastProps) {
  const { bg, color, dot } = TONE_STYLES[tone];
  // Errors are announced assertively (interrupt) and get their own alert
  // role; everything else is a polite status update.
  const isError = tone === 'blocked';

  const toastStyle: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 'var(--space-2)',
    padding: 'var(--space-2) var(--space-3) var(--space-2) var(--space-4)',
    borderRadius: 'var(--radius-pill)',
    background: bg,
    color,
    font: 'var(--text-small)',
    fontWeight: 'var(--weight-medium)' as CSSProperties['fontWeight'],
    boxShadow: '0 2px 8px rgba(0,0,0,0.10)',
    whiteSpace: 'nowrap',
    pointerEvents: 'auto',
  };

  const dotStyle: CSSProperties = {
    width: '6px',
    height: '6px',
    borderRadius: '50%',
    background: dot,
    flexShrink: 0,
  };

  const dismissStyle: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: '18px',
    height: '18px',
    marginLeft: 2,
    border: 'none',
    background: 'transparent',
    color: 'currentColor',
    opacity: 0.6,
    cursor: 'pointer',
    borderRadius: '50%',
    flexShrink: 0,
    font: 'inherit',
    fontSize: '13px',
    lineHeight: 1,
    padding: 0,
  };

  return (
    <div
      role={isError ? 'alert' : 'status'}
      aria-live={isError ? 'assertive' : 'polite'}
      style={toastStyle}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <span style={dotStyle} aria-hidden="true" />
      {message}
      {onDismiss && (
        <button type="button" onClick={onDismiss} aria-label="Dismiss" style={dismissStyle}>
          ×
        </button>
      )}
    </div>
  );
}
