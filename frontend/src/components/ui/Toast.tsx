import { CSSProperties } from 'react';

export type ToastTone = 'success' | 'blocked' | 'info';

export interface ToastProps {
  message: string;
  tone?: ToastTone;
}

const TONE_STYLES: Record<ToastTone, { bg: string; color: string; dot: string }> = {
  success: {
    bg: 'var(--hachi-success-tint)',
    color: 'var(--hachi-success)',
    dot: 'var(--hachi-success)',
  },
  blocked: {
    bg: 'var(--hachi-danger-tint)',
    color: 'var(--hachi-danger)',
    dot: 'var(--hachi-danger)',
  },
  info: {
    bg: 'var(--hachi-trust-tint)',
    color: 'var(--hachi-trust)',
    dot: 'var(--hachi-trust)',
  },
};

export function Toast({ message, tone = 'success' }: ToastProps) {
  const { bg, color, dot } = TONE_STYLES[tone];

  const toastStyle: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 'var(--space-2)',
    padding: 'var(--space-2) var(--space-4)',
    borderRadius: 'var(--radius-pill)',
    background: bg,
    color,
    font: 'var(--text-small)',
    fontWeight: 'var(--weight-medium)' as CSSProperties['fontWeight'],
    boxShadow: '0 2px 8px rgba(0,0,0,0.10)',
    whiteSpace: 'nowrap',
    pointerEvents: 'none',
  };

  const dotStyle: CSSProperties = {
    width: '6px',
    height: '6px',
    borderRadius: '50%',
    background: dot,
    flexShrink: 0,
  };

  return (
    <div role="status" aria-live="polite" style={toastStyle}>
      <span style={dotStyle} aria-hidden="true" />
      {message}
    </div>
  );
}
