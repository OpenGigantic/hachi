import { describe, it, expect, afterEach } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { KennelConsent } from './KennelConsent'
import { useStore } from '../../lib/store/store'

// Regression guard for a Rules-of-Hooks violation: KennelConsent used to early-return
// the browse panel (kennelBrowseOpen=true) *before* its Escape-key useEffect, so the
// number of hooks it called differed between the browse-mode render and the
// single-item-consent render. React detects this across a mount->update of the same
// component instance and throws ("Rendered fewer hooks than expected"), crashing the
// overlay tree. If this regresses, the assertions below throw instead of passing.
describe('KennelConsent — Rules of Hooks across browse <-> consent transitions', () => {
  afterEach(() => {
    cleanup()
    useStore.setState({ kennelConsent: null, kennelBrowseOpen: false })
  })

  it('does not crash when transitioning from browse mode to single-item consent', () => {
    useStore.setState({ kennelBrowseOpen: true, kennelConsent: null })
    const { rerender } = render(<KennelConsent />)

    useStore.setState({
      kennelBrowseOpen: false,
      kennelConsent: { id: 'github', icon: 'skills', name: 'GitHub', desc: 'Code hosting' },
    })
    expect(() => rerender(<KennelConsent />)).not.toThrow()
  })

  it('does not crash when transitioning from single-item consent back to browse mode', () => {
    useStore.setState({
      kennelBrowseOpen: false,
      kennelConsent: { id: 'github', icon: 'skills', name: 'GitHub', desc: 'Code hosting' },
    })
    const { rerender } = render(<KennelConsent />)

    useStore.setState({ kennelBrowseOpen: true, kennelConsent: null })
    expect(() => rerender(<KennelConsent />)).not.toThrow()
  })
})
