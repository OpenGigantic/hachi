import { CSSProperties, useState } from 'react';

export interface InputProps {
  value?: string;
  onChange?: (v: string) => void;
  placeholder?: string;
  label?: string;
  mono?: boolean;
  type?: string;
  style?: CSSProperties;
}

export function Input({
  value,
  onChange,
  placeholder,
  label,
  mono = false,
  type = 'text',
  style,
}: InputProps) {
  const [focused, setFocused] = useState(false);

  const wrapStyle: CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    gap: 'var(--space-1)',
    ...style,
  };

  const labelStyle: CSSProperties = {
    font: 'var(--text-small)',
    color: 'var(--text-muted)',
    fontWeight: 'var(--weight-medium)' as CSSProperties['fontWeight'],
  };

  const inputStyle: CSSProperties = {
    height: '40px',
    padding: '0 var(--space-3)',
    borderRadius: 'var(--radius-md)',
    background: 'var(--hachi-surface)',
    border: focused
      ? '0.5px solid var(--hachi-brand)'
      : '0.5px solid var(--hachi-line)',
    outline: focused ? '2px solid var(--hachi-brand-tint)' : 'none',
    outlineOffset: '2px',
    font: mono ? 'var(--text-mono)' : 'var(--text-body)',
    color: 'var(--text-default)',
    width: '100%',
    boxSizing: 'border-box',
    transition: `border-color var(--dur-fast) var(--ease), outline-color var(--dur-fast) var(--ease)`,
  };

  return (
    <div style={wrapStyle}>
      {label && <label style={labelStyle}>{label}</label>}
      <input
        type={type}
        value={value}
        onChange={e => onChange?.(e.target.value)}
        placeholder={placeholder}
        style={inputStyle}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      />
    </div>
  );
}
