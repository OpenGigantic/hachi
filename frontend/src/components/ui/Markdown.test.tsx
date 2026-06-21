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

// Security regression tests — HACHI-001 (DOM XSS via unsanitized link hrefs).
// These guard against javascript:, data:, and other dangerous URL schemes being
// rendered as live <a href> attributes. If any of these fail, the XSS fix has
// regressed and users of this renderer are at risk.
describe('Markdown — XSS / link sanitization (HACHI-001)', () => {
  it('strips javascript: href — produces no <a with javascript: href', () => {
    const html = renderToString(<Markdown text="[x](javascript:alert(1))" />)
    // Must not contain an anchor pointing to a javascript: URL
    expect(html).not.toMatch(/<a[^>]+href=["']javascript:/i)
    // The link text "x" must still be visible
    expect(html).toContain('x')
  })

  it('strips javascript: href with no parens in payload', () => {
    // Variant without parens — still a javascript: URL
    const html = renderToString(<Markdown text="[x](javascript:location='evil')" />)
    expect(html).not.toMatch(/<a[^>]+href=["']javascript:/i)
    expect(html).toContain('x')
  })

  it('strips javascript: href with leading whitespace (bypass attempt)', () => {
    // Browsers ignore leading whitespace before the scheme
    const html = renderToString(<Markdown text="[x](  javascript:alert(1))" />)
    expect(html).not.toMatch(/<a[^>]+href=["']javascript:/i)
    expect(html).toContain('x')
  })

  it('strips data: href (inline script/image injection)', () => {
    const html = renderToString(
      <Markdown text="[x](data:text/html,<script>alert(1)</script>)" />
    )
    expect(html).not.toMatch(/<a[^>]+href=["']data:/i)
    expect(html).toContain('x')
  })

  it('strips vbscript: href', () => {
    const html = renderToString(<Markdown text="[x](vbscript:MsgBox(1))" />)
    expect(html).not.toMatch(/<a[^>]+href=["']vbscript:/i)
    expect(html).toContain('x')
  })

  it('allows https: href with correct attributes', () => {
    const html = renderToString(<Markdown text="[ok](https://example.com)" />)
    expect(html).toMatch(/<a[^>]+href="https:\/\/example\.com"/)
    expect(html).toMatch(/target="_blank"/)
    expect(html).toMatch(/rel="[^"]*noopener/)
    expect(html).toContain('ok')
  })

  it('allows http: href', () => {
    const html = renderToString(<Markdown text="[link](http://example.com)" />)
    expect(html).toMatch(/<a[^>]+href="http:\/\/example\.com"/)
  })

  it('allows mailto: href', () => {
    const html = renderToString(<Markdown text="[email](mailto:user@example.com)" />)
    expect(html).toMatch(/<a[^>]+href="mailto:user@example\.com"/)
  })

  it('allows relative / href', () => {
    const html = renderToString(<Markdown text="[home](/home)" />)
    expect(html).toMatch(/<a[^>]+href="\/home"/)
  })

  it('allows relative # href (same-page anchor)', () => {
    const html = renderToString(<Markdown text="[top](#top)" />)
    expect(html).toMatch(/<a[^>]+href="#top"/)
  })

  it('raw <img onerror> in text stays escaped — no live onerror attribute', () => {
    // The renderer should HTML-escape raw angle brackets in text nodes;
    // React does this automatically for string children.
    const html = renderToString(<Markdown text="<img src=x onerror=alert(1)>" />)
    // Must not contain an actual <img element (with real angle brackets and attrs)
    expect(html).not.toMatch(/<img\s[^>]*onerror/)
    // The text content should still be present in some escaped form
    expect(html).toContain('&lt;img')
  })
})
