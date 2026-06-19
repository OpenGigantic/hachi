import { CSSProperties, ReactNode } from 'react';

export type StatusValue = 'working' | 'on-watch' | 'done' | 'blocked';

export interface StatusPillProps {
  status: StatusValue;
  children?: ReactNode;
}

const STATUS_CONFIG: Record<StatusValue, { bg: string; color: string; label: string }> = {
  working: {
    bg: 'var(--hachi-working-tint)',
    color: 'var(--hachi-working)',
    label: 'working',
  },
  'on-watch': {
    bg: 'var(--hachi-trust-tint)',
    color: 'var(--hachi-trust)',
    label: 'on watch',
  },
  done: {
    bg: 'var(--hachi-success-tint)',
    color: 'var(--hachi-success)',
    label: 'done',
  },
  blocked: {
    bg: 'var(--hachi-danger-tint)',
    color: 'var(--hachi-danger)',
    label: 'blocked',
  },
};

export function StatusPill({ status, children }: StatusPillProps) {
  const config = STATUS_CONFIG[status];

  const pillStyle: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 'var(--space-1)',
    paddingInline: 'var(--space-2)',
    paddingBlock: '3px',
    borderRadius: 'var(--radius-pill)',
    background: config.bg,
    color: config.color,
    font: 'var(--text-small)',
    fontWeight: 'var(--weight-medium)' as CSSProperties['fontWeight'],
    lineHeight: '20px',
    whiteSpace: 'nowrap',
  };

  const dotStyle: CSSProperties = {
    width: '6px',
    height: '6px',
    borderRadius: '50%',
    background: config.color,
    flexShrink: 0,
  };

  return (
    <span style={pillStyle}>
      <span style={dotStyle} aria-hidden="true" />
      {children ?? config.label}
    </span>
  );
}
