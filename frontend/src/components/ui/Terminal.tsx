import { CSSProperties, ReactNode, useEffect, useRef } from 'react';

export type TerminalLineTone = 'default' | 'muted' | 'brand' | 'working' | 'success' | 'danger';

export interface TerminalLine {
  text: string;
  /** One of TerminalLineTone; accepts any string and falls back to the default color. */
  tone?: string;
}

export interface TerminalProps {
  lines?: TerminalLine[];
  prompt?: string;
  children?: ReactNode;
  style?: CSSProperties;
}

const TONE_COLOR: Record<TerminalLineTone, string> = {
  default: '#FBF7EF',
  muted: '#9C968C',
  brand: '#E25A44',
  working: '#E0922E',
  success: '#3F9D6B',
  danger: '#C0392B',
};

export function Terminal({ lines = [], prompt, children, style }: TerminalProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [lines]);

  const surfaceStyle: CSSProperties = {
    background: '#16151A',
    borderRadius: 'var(--radius-lg)',
    border: '0.5px solid #2A2730',
    fontFamily: 'var(--font-mono)',
    fontSize: 'var(--type-mono-size)',
    lineHeight: 'var(--type-mono-lh)',
    color: '#FBF7EF',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    ...style,
  };

  const outputStyle: CSSProperties = {
    flex: 1,
    overflowY: 'auto',
    padding: 'var(--space-4)',
    paddingBottom: 'var(--space-2)',
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
  };

  const promptBarStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--space-2)',
    padding: 'var(--space-2) var(--space-4)',
    borderTop: '0.5px solid #2A2730',
    minHeight: '36px',
  };

  const promptLabelStyle: CSSProperties = {
    color: '#E25A44',  // Torii red in dark context
    fontWeight: 'var(--weight-medium)' as CSSProperties['fontWeight'],
    userSelect: 'none',
    flexShrink: 0,
  };

  const promptTextStyle: CSSProperties = {
    color: '#FBF7EF',
    flexGrow: 1,
    minWidth: 0,
  };

  return (
    <div style={surfaceStyle}>
      {/* output lines */}
      <div style={outputStyle}>
        {lines.map((line, i) => (
          <div
            key={i}
            style={{
              color: TONE_COLOR[(line.tone ?? 'default') as TerminalLineTone] ?? TONE_COLOR.default,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
            }}
          >
            {line.text}
          </div>
        ))}
        {children}
        <div ref={bottomRef} />
      </div>

      {/* prompt bar */}
      <div style={promptBarStyle}>
        <span style={promptLabelStyle}>hachi ▸</span>
        {prompt !== undefined && (
          <span style={promptTextStyle}>{prompt}</span>
        )}
        {/* blinking caret */}
        <span
          style={{
            display: 'inline-block',
            width: '8px',
            height: '14px',
            background: '#E25A44',
            animation: 'hachi-caret-blink 1s step-end infinite',
            flexShrink: 0,
            alignSelf: 'center',
          }}
          aria-hidden="true"
        />
      </div>

      <style>{`
        @keyframes hachi-caret-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
        @media (prefers-reduced-motion: reduce) {
          [class*="hachi-caret"] { animation: none !important; }
        }
      `}</style>
    </div>
  );
}
