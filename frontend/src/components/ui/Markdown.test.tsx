import { describe, it, expect } from 'vitest'
import { renderToString } from 'react-dom/server'
import { Markdown } from './Markdown'

// Regression guard for the streaming-crash root cause (2026-06-21):
// `parseInline` recurses for bold/italic/link children. It previously shared a
// single module-level `g`-flag RegExp, so a nested call reset the shared
// `lastIndex` and the outer loop re-matched the same token forever — an infinite
// loop that froze the main thread (RESULT_CODE_HUNG) on ANY assistant reply
// containing emphasis or a link. If this regresses, these renders never return.
describe('Markdown — recursion / inline parsing', () => {
  it('renders bold inside a numbered-list line without hanging', () => {
    const html = renderToString(
      <Markdown text="1. **GitHub** — your personal repos, organization, or a specific repository?" />
    )
    expect(html).toContain('GitHub')
    expect(html).toContain('<strong')
  })

  it('handles emphasis, inline code, and a link together', () => {
    const html = renderToString(
      <Markdown text="see `gateway.py` and *note* the [docs](http://x.co) plus **bold**" />
    )
    expect(html).toContain('<code')
    expect(html).toContain('<em')
    expect(html).toContain('<a ')
    expect(html).toContain('<strong')
  })

  it('handles many emphasis tokens on one line', () => {
    const html = renderToString(<Markdown text="**a** **b** **c** _d_ _e_ trailing" />)
    expect(html).toContain('trailing')
  })

  it('renders plain text unchanged', () => {
    const html = renderToString(<Markdown text="just plain text, no markdown" />)
    expect(html).toContain('just plain text, no markdown')
  })
})
