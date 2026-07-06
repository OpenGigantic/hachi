import { describe, it, expect } from 'vitest'
import { renderToString } from 'react-dom/server'
import { Icon } from './Icon'

// Regression guard: an unregistered icon `name` silently renders an EMPTY
// <svg> (registry[name] ?? '' falls back to '""'), with no console warning
// or visual error — the icon is just invisible. L2 audit finding: two real
// call sites referenced icon names that were never added to the registry —
// SpendSection.tsx's "Breakdown" heading used 'bar-chart' (now a registered
// icon below), and memory.tsx's recall button used 'loader' for its loading
// state (now uses the existing 'refresh' name, matching the loading-state
// convention already used elsewhere, e.g. conversation.tsx's upload button).
describe('Icon registry — no silently-blank icons (BB-36)', () => {
  it('renders real markup (not an empty <svg/>) for "bar-chart" (SpendSection.tsx Breakdown heading)', () => {
    const html = renderToString(<Icon name="bar-chart" size={17} />)
    expect(html).toMatch(/<(path|circle|line|polygon|rect)/)
  })

  it('falls back to an empty <svg/> for a genuinely unregistered name (documents the failure mode)', () => {
    // 'loader' was never registered (that was the bug) — confirms the
    // silent-blank fallback this suite guards against, without depending
    // on a specific route file's source text.
    const html = renderToString(<Icon name="loader" size={15} />)
    expect(html).not.toMatch(/<(path|circle|line|polygon|rect)/)
  })

  it('every registered icon renders real markup (no empty-string entries)', () => {
    const names = [
      'home', 'chat', 'work', 'leash', 'shield', 'memory', 'skills', 'plug',
      'settings', 'search', 'bar-chart', 'refresh', 'dollar',
    ]
    for (const name of names) {
      const html = renderToString(<Icon name={name} size={16} />)
      expect(html, `icon "${name}" rendered blank`).toMatch(/<(path|circle|line|polygon|rect)/)
    }
  })
})
