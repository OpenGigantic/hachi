import { CSSProperties } from 'react';
import { StatusPill, StatusValue } from './StatusPill';

export interface RunCardProps {
  title: string;
  status: StatusValue;
  progress: number;
  spent: number;
  budget: number;
  meta?: string;
  onClick?: () => void;
}

export function RunCard({ title, status, progress, spent, budget, meta, onClick }: RunCardProps) {
  const clampedProgress = Math.max(0, Math.min(1, progress));

  const cardStyle: CSSProperties = {
    background: 'var(--hachi-surface)',
    border: '0.5px solid var(--hachi-line)',
    borderRadius: 'var(--radius-lg)',
    overflow: 'hidden',
    cursor: onClick ? 'pointer' : undefined,
    transition: `border-color var(--dur-fast) var(--ease)`,
  };

  const bodyStyle: CSSProperties = {
    padding: 'var(--space-4)',
    display: 'flex',
    flexDirection: 'column',
    gap: 'var(--space-2)',
  };

  const headerStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 'var(--space-2)',
  };

  const titleStyle: CSSProperties = {
    font: 'var(--text-body)',
    fontWeight: 'var(--weight-medium)' as CSSProperties['fontWeight'],
    color: 'var(--text-default)',
    margin: 0,
    flexGrow: 1,
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  };

  const metaStyle: CSSProperties = {
    font: 'var(--text-small)',
    color: 'var(--text-muted)',
  };

  const budgetStyle: CSSProperties = {
    font: 'var(--text-small)',
    color: 'var(--text-muted)',
    fontFeatureSettings: '"tnum"',
  };

  const trackStyle: CSSProperties = {
    width: '100%',
    height: '2px',
    background: 'var(--hachi-line)',
  };

  const fillStyle: CSSProperties = {
    height: '100%',
    width: `${clampedProgress * 100}%`,
    background: 'var(--hachi-brand)',
    transition: `width var(--dur) var(--ease)`,
  };

  function formatCurrency(n: number) {
    return `$${n.toFixed(2)}`;
  }

  function handleMouseEnter(e: React.MouseEvent<HTMLDivElement>) {
    if (onClick) e.currentTarget.style.borderColor = 'var(--hachi-brand)';
  }

  function handleMouseLeave(e: React.MouseEvent<HTMLDivElement>) {
    if (onClick) e.currentTarget.style.borderColor = 'var(--hachi-line)';
  }

  return (
    <div
      style={cardStyle}
      onClick={onClick}
      onMouseEnter={onClick ? handleMouseEnter : undefined}
      onMouseLeave={onClick ? handleMouseLeave : undefined}
    >
      {/* thin Torii-red progress bar at top */}
      <div style={trackStyle}>
        <div style={fillStyle} />
      </div>

      <div style={bodyStyle}>
        <div style={headerStyle}>
          <p style={titleStyle}>{title}</p>
          <StatusPill status={status} />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          {meta && <span style={metaStyle}>{meta}</span>}
          <span style={budgetStyle}>
            spent {formatCurrency(spent)} of {formatCurrency(budget)}
          </span>
        </div>
      </div>
    </div>
  );
}
