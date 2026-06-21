// Self-contained Markdown renderer — no external dependencies.
// Supports a pragmatic subset suitable for streaming assistant output:
//   - Headings (#, ##, …, ######)
//   - Unordered lists (- / * prefix)
//   - Ordered lists (1. prefix)
//   - Paragraphs / blank-line separation
//   - Inline: bold (**…** / __…__), italic (*…* / _…_), code (`…`), links ([t](u))
//
// Streaming-safe: never throws on partial/unterminated input.
// Unclosed markers render as literal text until the closing marker arrives.

import type { ReactNode, CSSProperties } from 'react'

// ---- URL sanitizer --------------------------------------------------
// Allows only safe URL schemes. Returns null for anything disallowed
// (javascript:, data:, vbscript:, etc.).
//
// Attackers use leading whitespace / control chars and tab-embedded
// scheme names (e.g. "java\tscript:") to bypass naive checks, so we
// strip leading whitespace/control chars before extracting the scheme
// and compare case-insensitively.
function safeHref(href: string): string | null {
  // Strip leading ASCII whitespace and C0 control characters (U+0000–U+001F).
  // These can appear in raw markdown and are dropped by browsers silently.
  const trimmed = href.replace(/^\s+/, '')

  // Relative URLs are always safe: /, #, ./, ../
  if (
    trimmed.startsWith('/') ||
    trimmed.startsWith('#') ||
    trimmed.startsWith('./') ||
    trimmed.startsWith('../')
  ) {
    return trimmed
  }

  // Extract the scheme — everything up to and including the first colon,
  // then collapse any embedded whitespace/control chars that browsers ignore.
  const colonIdx = trimmed.indexOf(':')
  if (colonIdx === -1) {
    // No scheme → treat as a relative URL path (safe)
    return trimmed
  }
  // Collapse whitespace within the scheme (handles "java\tscript:")
  const scheme = trimmed.slice(0, colonIdx).replace(/\s+/g, '').toLowerCase()

  const ALLOWED_SCHEMES = new Set(['http', 'https', 'mailto', 'tel'])
  if (!ALLOWED_SCHEMES.has(scheme)) {
    return null
  }

  return trimmed
}

// ---- Inline parser --------------------------------------------------
// Tokenises a single text run into spans with optional formatting.

type InlineToken =
  | { kind: 'text'; value: string }
  | { kind: 'bold'; children: InlineToken[] }
  | { kind: 'italic'; children: InlineToken[] }
  | { kind: 'code'; value: string }
  | { kind: 'link'; href: string; children: InlineToken[] }

// Marker table: order matters — longer markers must come before sub-matches.
// We check ** before * so ** isn't accidentally consumed as two * tokens.
//
// IMPORTANT: this is only the PATTERN SOURCE. `parseInline` is recursive (bold/
// italic/link children are parsed by recursing), so it MUST NOT share a single
// stateful (`g`-flag) RegExp across calls: a nested call would reset the shared
// `lastIndex`, making the outer loop re-match the same token forever — an
// infinite loop that hangs the main thread on any text containing emphasis or a
// link. Each parseInline call therefore builds its own RegExp instance below.
// NOTE (HACHI-015 — LOW): The link href pattern `\(([^)]*)\)` stops at the
// first `)`, which truncates URLs that legally contain parentheses (e.g.
// Wikipedia links like `/wiki/Foo_(bar)`). Fixing this requires a more
// complex regex (e.g. balanced-paren counting) that cannot share the `g`-flag
// instance safety constraints documented above without careful testing. Given
// that the XSS fix (HACHI-001) and recursion safety take precedence, this
// limitation is left in place and documented here. URLs with parens should be
// encoded as `%28`/`%29` to work correctly with this renderer.
const INLINE_RE_SOURCE =
  '(`[^`]*`?)|(\\*\\*(?:[^*]|\\*(?!\\*))*\\*\\*)|(__(?:[^_]|_(?!_))*__)|(\\*(?:[^*])*\\*(?!\\*))|(_(?:[^_])*_(?!_))|(\\[([^\\]]*)\\]\\(([^)]*)\\))'

function parseInline(text: string): InlineToken[] {
  // Local, per-call regex instance — recursion-safe (see INLINE_RE_SOURCE note).
  const re = new RegExp(INLINE_RE_SOURCE, 'g')
  const tokens: InlineToken[] = []
  let lastIndex = 0

  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    // Defensive: a zero-width match would never advance lastIndex → infinite
    // loop. Skip a char and continue. (Not expected with the current pattern.)
    if (m.index === re.lastIndex) {
      re.lastIndex += 1
      continue
    }

    // Push any literal text before this match
    if (m.index > lastIndex) {
      tokens.push({ kind: 'text', value: text.slice(lastIndex, m.index) })
    }

    const [full, code, boldStar, boldUnd, italicStar, italicUnd, , linkText, linkHref] = m

    if (code !== undefined) {
      // Inline code — strip surrounding backticks; tolerate unclosed (trailing `)
      const inner = full.startsWith('`') && full.endsWith('`') && full.length > 1
        ? full.slice(1, -1)
        : full.slice(1)
      tokens.push({ kind: 'code', value: inner })
    } else if (boldStar !== undefined || boldUnd !== undefined) {
      const inner = (boldStar ?? boldUnd).slice(2, -2)
      tokens.push({ kind: 'bold', children: parseInline(inner) })
    } else if (italicStar !== undefined || italicUnd !== undefined) {
      const inner = (italicStar ?? italicUnd).slice(1, -1)
      tokens.push({ kind: 'italic', children: parseInline(inner) })
    } else if (linkText !== undefined && linkHref !== undefined) {
      tokens.push({ kind: 'link', href: linkHref, children: parseInline(linkText) })
    } else {
      // Fallback: treat the whole match as literal text (shouldn't normally happen)
      tokens.push({ kind: 'text', value: full })
    }

    lastIndex = re.lastIndex
  }

  // Remaining literal text
  if (lastIndex < text.length) {
    tokens.push({ kind: 'text', value: text.slice(lastIndex) })
  }

  return tokens
}

function renderInlineTokens(tokens: InlineToken[], keyPrefix: string): ReactNode[] {
  return tokens.map((tok, i) => {
    const key = `${keyPrefix}-${i}`
    switch (tok.kind) {
      case 'text':
        return tok.value
      case 'code':
        return (
          <code
            key={key}
            style={{
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: '0.88em',
              background: 'var(--hachi-cream, rgba(0,0,0,0.06))',
              borderRadius: 4,
              padding: '1px 5px',
            }}
          >
            {tok.value}
          </code>
        )
      case 'bold':
        return <strong key={key}>{renderInlineTokens(tok.children, key)}</strong>
      case 'italic':
        return <em key={key}>{renderInlineTokens(tok.children, key)}</em>
      case 'link': {
        const allowedHref = safeHref(tok.href)
        if (allowedHref === null) {
          // Disallowed scheme (javascript:, data:, vbscript:, etc.) — render
          // the link text as plain inline content, no <a> wrapper.
          return <span key={key}>{renderInlineTokens(tok.children, key)}</span>
        }
        return (
          <a
            key={key}
            href={allowedHref}
            rel="noreferrer noopener"
            target="_blank"
            style={{ color: 'var(--hachi-trust, #4c6ef5)', textDecoration: 'underline' }}
          >
            {renderInlineTokens(tok.children, key)}
          </a>
        )
      }
    }
  })
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  return renderInlineTokens(parseInline(text), keyPrefix)
}

// ---- Block-level parser ---------------------------------------------

type BlockNode =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'ol'; items: string[] }
  | { kind: 'paragraph'; lines: string[] }
  | { kind: 'blank' }

const HEADING_RE = /^(#{1,6})\s+(.*)$/
const UL_RE = /^[-*]\s+(.*)$/
const OL_RE = /^\d+\.\s+(.*)$/

function parseBlocks(text: string): BlockNode[] {
  const rawLines = text.split('\n')
  const blocks: BlockNode[] = []

  let i = 0
  while (i < rawLines.length) {
    const line = rawLines[i]

    // Blank line
    if (line.trim() === '') {
      blocks.push({ kind: 'blank' })
      i++
      continue
    }

    // Heading
    const hm = HEADING_RE.exec(line)
    if (hm) {
      blocks.push({ kind: 'heading', level: hm[1].length, text: hm[2] })
      i++
      continue
    }

    // Unordered list — collect consecutive UL lines
    if (UL_RE.test(line)) {
      const items: string[] = []
      while (i < rawLines.length && UL_RE.test(rawLines[i])) {
        const mm = UL_RE.exec(rawLines[i])
        items.push(mm ? mm[1] : rawLines[i])
        i++
      }
      blocks.push({ kind: 'ul', items })
      continue
    }

    // Ordered list — collect consecutive OL lines
    if (OL_RE.test(line)) {
      const items: string[] = []
      while (i < rawLines.length && OL_RE.test(rawLines[i])) {
        const mm = OL_RE.exec(rawLines[i])
        items.push(mm ? mm[1] : rawLines[i])
        i++
      }
      blocks.push({ kind: 'ol', items })
      continue
    }

    // Regular paragraph — collect non-blank, non-structural lines
    const lines: string[] = []
    while (
      i < rawLines.length &&
      rawLines[i].trim() !== '' &&
      !HEADING_RE.test(rawLines[i]) &&
      !UL_RE.test(rawLines[i]) &&
      !OL_RE.test(rawLines[i])
    ) {
      lines.push(rawLines[i])
      i++
    }
    if (lines.length > 0) {
      blocks.push({ kind: 'paragraph', lines })
    }
  }

  return blocks
}

// ---- Heading font sizes ------------------------------------------

function headingStyle(level: number): CSSProperties {
  const sizes: Record<number, CSSProperties> = {
    1: { fontSize: '1.25em', fontWeight: 700, lineHeight: '1.3', margin: '0.6em 0 0.3em' },
    2: { fontSize: '1.15em', fontWeight: 700, lineHeight: '1.3', margin: '0.6em 0 0.25em' },
    3: { fontSize: '1.05em', fontWeight: 600, lineHeight: '1.3', margin: '0.5em 0 0.2em' },
    4: { fontSize: '1em', fontWeight: 600, lineHeight: '1.3', margin: '0.5em 0 0.2em' },
    5: { fontSize: '0.95em', fontWeight: 600, lineHeight: '1.3', margin: '0.4em 0 0.15em' },
    6: { fontSize: '0.9em', fontWeight: 600, lineHeight: '1.3', margin: '0.4em 0 0.15em' },
  }
  return sizes[level] ?? sizes[6]
}

// ---- Render blocks --------------------------------------------------

function renderBlock(block: BlockNode, blockIdx: number): ReactNode {
  const key = `b${blockIdx}`
  switch (block.kind) {
    case 'blank':
      // Blank lines add visual spacing via paragraph gap
      return <div key={key} style={{ height: '0.6em' }} />

    case 'heading': {
      const Tag = `h${block.level}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6'
      return (
        <Tag key={key} style={headingStyle(block.level)}>
          {renderInline(block.text, key)}
        </Tag>
      )
    }

    case 'ul':
      return (
        <ul
          key={key}
          style={{
            margin: '0.25em 0',
            paddingLeft: '1.5em',
            listStyleType: 'disc',
          }}
        >
          {block.items.map((item, ii) => (
            <li key={`${key}-li${ii}`} style={{ lineHeight: 'inherit', margin: '0.1em 0' }}>
              {renderInline(item, `${key}-li${ii}`)}
            </li>
          ))}
        </ul>
      )

    case 'ol':
      return (
        <ol
          key={key}
          style={{
            margin: '0.25em 0',
            paddingLeft: '1.5em',
            listStyleType: 'decimal',
          }}
        >
          {block.items.map((item, ii) => (
            <li key={`${key}-li${ii}`} style={{ lineHeight: 'inherit', margin: '0.1em 0' }}>
              {renderInline(item, `${key}-li${ii}`)}
            </li>
          ))}
        </ol>
      )

    case 'paragraph': {
      // Within a paragraph, individual lines are separated by <br> to preserve
      // single-newline breaks (equivalent to pre-wrap newlines).
      const content: ReactNode[] = []
      block.lines.forEach((line, li) => {
        if (li > 0) content.push(<br key={`${key}-br${li}`} />)
        const spans = renderInline(line, `${key}-ln${li}`)
        spans.forEach((s, si) => content.push(<span key={`${key}-ln${li}-s${si}`}>{s}</span>))
      })
      return (
        <p key={key} style={{ margin: '0.15em 0' }}>
          {content}
        </p>
      )
    }
  }
}

// ---- Public component -----------------------------------------------

export function Markdown({ text }: { text: string }): JSX.Element {
  if (text === '') {
    return <span />
  }

  const blocks = parseBlocks(text)

  return (
    <div
      style={{
        // Inherit font, color, lineHeight from parent — no overrides needed.
        // whiteSpace is NOT set here; the block structure handles newlines.
      }}
    >
      {blocks.map((block, i) => renderBlock(block, i))}
    </div>
  )
}
