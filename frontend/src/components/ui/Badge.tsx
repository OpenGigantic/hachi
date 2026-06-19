import { CSSProperties, ReactNode } from 'react';

export type BadgeTone = 'default' | 'brand' | 'muted' | 'navy';

export interface BadgeProps {
  tone?: BadgeTone;
  children?: ReactNode;
}

const TONE_STYLES: Record<BadgeTone, { bg: string; color: string }> = {
  default: {
    bg: 'var(--hachi-cream)',
    color: 'var(--text-default)',
  },
  brand: {
    bg: 'var(--hachi-brand-tint)',
    color: 'var(--hachi-brand)',
  },
  muted: {
    bg: 'var(--hachi-cream)',
    color: 'var(--text-muted)',
  },
  navy: {
    bg: 'var(--hachi-trust-tint)',
    color: 'var(--hachi-trust)',
  },
};

export function Badge({ tone = 'default', children }: BadgeProps) {
  const { bg, color } = TONE_STYLES[tone];

  const style: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    paddingInline: 'var(--space-2)',
    paddingBlock: '2px',
    borderRadius: 'var(--radius-sm)',
    background: bg,
    color,
    font: 'var(--text-small)',
    fontWeight: 'var(--weight-medium)' as CSSProperties['fontWeight'],
    whiteSpace: 'nowrap',
  };

  return <span style={style}>{children}</span>;
}
