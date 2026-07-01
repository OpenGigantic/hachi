import { CSSProperties, ReactNode } from 'react';

export interface CardProps {
  children?: ReactNode;
  padding?: number | string;
  style?: CSSProperties;
  onClick?: () => void;
}

export function Card({ children, padding = 24, style, onClick }: CardProps) {
  const cardStyle: CSSProperties = {
    background: 'var(--hachi-surface)',
    border: '0.5px solid var(--hachi-line)',
    borderRadius: 'var(--radius-lg)',
    padding: typeof padding === 'number' ? `${padding}px` : padding,
    cursor: onClick ? 'pointer' : undefined,
    transition: onClick ? `border-color var(--dur-fast) var(--ease)` : undefined,
    ...style,
  };

  function handleMouseEnter(e: React.MouseEvent<HTMLDivElement>) {
    if (onClick) {
      e.currentTarget.style.borderColor = 'var(--hachi-brand)';
    }
  }

  function handleMouseLeave(e: React.MouseEvent<HTMLDivElement>) {
    if (onClick) {
      e.currentTarget.style.borderColor = 'var(--hachi-line)';
    }
  }

  return (
    <div
      style={cardStyle}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
      onMouseEnter={onClick ? handleMouseEnter : undefined}
      onMouseLeave={onClick ? handleMouseLeave : undefined}
    >
      {children}
    </div>
  );
}
