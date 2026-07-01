import { CSSProperties, useId } from 'react';

export interface SwitchProps {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
  disabled?: boolean;
}

export function Switch({ checked, onChange, label, disabled = false }: SwitchProps) {
  const id = useId();

  // WCAG 2.5.5/2.5.8 — the interactive hit area is 44x44 even though the
  // visual pill stays compact (40x24); the pill is centered inside a
  // padded, invisible hit box rather than inflating the graphic itself.
  const hitAreaStyle: CSSProperties = {
    position: 'relative',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: '44px',
    height: '44px',
    margin: '-10px -2px',
    cursor: disabled ? 'not-allowed' : 'pointer',
    flexShrink: 0,
  };

  const trackStyle: CSSProperties = {
    position: 'relative',
    display: 'inline-block',
    width: '40px',
    height: '24px',
    borderRadius: 'var(--radius-pill)',
    background: checked ? 'var(--hachi-brand)' : 'var(--hachi-line)',
    transition: `background var(--dur-fast) var(--ease)`,
    opacity: disabled ? 0.45 : 1,
    border: '0.5px solid transparent',
    pointerEvents: 'none',
  };

  const thumbStyle: CSSProperties = {
    position: 'absolute',
    top: '3px',
    left: checked ? '19px' : '3px',
    width: '16px',
    height: '16px',
    borderRadius: '50%',
    background: 'white',
    transition: `left var(--dur-fast) var(--ease)`,
    pointerEvents: 'none',
  };

  const wrapStyle: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 'var(--space-2)',
    cursor: disabled ? 'not-allowed' : 'pointer',
    userSelect: 'none',
  };

  const labelStyle: CSSProperties = {
    font: 'var(--text-body)',
    color: disabled ? 'var(--text-muted)' : 'var(--text-default)',
  };

  function handleKeyDown(e: React.KeyboardEvent) {
    if (disabled) return;
    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      onChange(!checked);
    }
  }

  return (
    <label htmlFor={id} style={wrapStyle}>
      <span
        id={id}
        role="switch"
        aria-checked={checked}
        aria-disabled={disabled}
        tabIndex={disabled ? -1 : 0}
        style={hitAreaStyle}
        onClick={() => !disabled && onChange(!checked)}
        onKeyDown={handleKeyDown}
      >
        <span style={trackStyle}>
          <span style={thumbStyle} />
        </span>
      </span>
      {label && <span style={labelStyle}>{label}</span>}
    </label>
  );
}
