import { CSSProperties, ReactNode, AriaAttributes } from 'react';

export interface ButtonProps extends AriaAttributes {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md';
  type?: 'button' | 'submit' | 'reset';
  onClick?: () => void;
  disabled?: boolean;
  children?: ReactNode;
  style?: CSSProperties;
}

export function Button({
  variant = 'primary',
  size = 'md',
  type = 'button',
  onClick,
  disabled = false,
  children,
  style,
  ...ariaProps
}: ButtonProps) {
  const height = size === 'sm' ? '32px' : '40px';
  const px = size === 'sm' ? 'var(--space-3)' : 'var(--space-4)';
  const fontSize = size === 'sm' ? 'var(--type-small-size)' : 'var(--type-body-size)';

  const variantStyles: CSSProperties = (() => {
    switch (variant) {
      case 'primary':
        return {
          background: 'var(--hachi-brand)',
          color: 'var(--hachi-canvas)',
          border: 'none',
        };
      case 'secondary':
        return {
          background: 'transparent',
          color: 'var(--hachi-trust)',
          border: '0.5px solid var(--hachi-trust)',
        };
      case 'ghost':
        return {
          background: 'transparent',
          color: 'var(--text-muted)',
          border: 'none',
        };
      case 'danger':
        return {
          background: 'var(--hachi-danger)',
          color: 'var(--hachi-canvas)',
          border: 'none',
        };
    }
  })();

  const disabledStyles: CSSProperties = disabled
    ? { opacity: 0.45, cursor: 'not-allowed', pointerEvents: 'none' }
    : { cursor: 'pointer' };

  const baseStyles: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 'var(--space-2)',
    height,
    padding: `0 ${px}`,
    borderRadius: 'var(--radius-md)',
    font: 'var(--text-body)',
    fontSize,
    fontWeight: 'var(--weight-medium)' as CSSProperties['fontWeight'],
    lineHeight: 1,
    letterSpacing: 0,
    transition: `background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease)`,
    whiteSpace: 'nowrap',
    userSelect: 'none',
    ...variantStyles,
    ...disabledStyles,
    ...style,
  };

  function handleMouseEnter(e: React.MouseEvent<HTMLButtonElement>) {
    if (disabled) return;
    const el = e.currentTarget;
    if (variant === 'primary') el.style.background = 'var(--hachi-brand-hover)';
    if (variant === 'danger') el.style.background = '#a83226';
    if (variant === 'secondary') el.style.background = 'var(--hachi-trust-tint)';
    if (variant === 'ghost') el.style.color = 'var(--text-default)';
  }

  function handleMouseLeave(e: React.MouseEvent<HTMLButtonElement>) {
    if (disabled) return;
    const el = e.currentTarget;
    if (variant === 'primary') el.style.background = 'var(--hachi-brand)';
    if (variant === 'danger') el.style.background = 'var(--hachi-danger)';
    if (variant === 'secondary') el.style.background = 'transparent';
    if (variant === 'ghost') el.style.color = 'var(--text-muted)';
  }

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      aria-disabled={disabled || undefined}
      style={baseStyles}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      {...ariaProps}
    >
      {children}
    </button>
  );
}
