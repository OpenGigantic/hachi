// ============================================================
// Hachi Web App — MemoryGardener domain regression tests (T-08 A3)
//
// Mirrors approvals.test.ts's BB-106 pattern: a decision POST must be awaited
// FIRST, and the store only mutated once the gateway confirms it — a failed
// or network-dropped decision must never remove the card from the list while
// pretending the .og files were actually changed.
// ============================================================

import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()
const apiSend = vi.fn()

// Canonical mock block (see store.test.ts / approvals.test.ts): store.ts
// statically imports chat.ts's live functions, and chat.ts imports ../sse and
// ../../notifications at module scope — all three need mocking or importing
// ../../store/store (via this domain module) pulls in real network/browser
// APIs.
vi.mock('../client', () => ({
  USE_MOCKS: false,
  apiGet: (...args: unknown[]) => apiGet(...args),
  apiSend: (...args: unknown[]) => apiSend(...args),
  registerReconnectCallback: vi.fn(),
  isNetworkError: () => false,
  API_BASE: '/api',
  authHeaders: () => ({}),
  ApiError: class ApiError extends Error {
    status: number
    constructor(message: string, status = 500) {
      super(message)
      this.name = 'ApiError'
      this.status = status
    }
  },
}))

vi.mock('../sse', () => ({
  streamSSE: vi.fn(),
}))

vi.mock('../../notifications', () => ({
  ensureNotificationPermission: vi.fn(),
  notifyApprovalPending: vi.fn(),
}))

import { useStore } from '../../store/store'
import { loadGardenerDiffs, decideGardenerDiff } from './memoryGardener'

interface RawProposal {
  id?: string
  kind?: string
  name_a?: string
  name_b?: string
  reason?: string
  confidence?: number
  file_path_a?: string
  file_path_b?: string
  auto_applied?: boolean
}

function mockPending(items: RawProposal[]): void {
  apiGet.mockImplementation((path: string) => {
    if (path === '/memory/diffs') return Promise.resolve({ pending_review: items })
    return Promise.resolve(undefined)
  })
}

beforeEach(() => {
  apiGet.mockReset()
  apiSend.mockReset()
  mockPending([])
  useStore.setState({
    gardenerDiffs: [],
    toasts: [],
  } as unknown as Parameters<typeof useStore.setState>[0])
})

describe('loadGardenerDiffs', () => {
  it('adapts the gateway snake_case shape into the store camelCase shape', async () => {
    mockPending([
      {
        id: 'abc123',
        kind: 'merge_similar',
        name_a: 'topic_alpha',
        name_b: 'topic_beta',
        reason: 'Jaccard similarity 0.91 ≥ 0.70',
        confidence: 0.91,
        file_path_a: '/mem/a.og',
        file_path_b: '/mem/b.og',
        auto_applied: false,
      },
    ])

    await loadGardenerDiffs()

    const diffs = useStore.getState().gardenerDiffs
    expect(diffs).toHaveLength(1)
    expect(diffs[0]).toEqual({
      id: 'abc123',
      kind: 'merge_similar',
      nameA: 'topic_alpha',
      nameB: 'topic_beta',
      reason: 'Jaccard similarity 0.91 ≥ 0.70',
      confidence: 0.91,
      filePathA: '/mem/a.og',
      filePathB: '/mem/b.og',
      autoApplied: false,
    })
  })

  it('clears the store to empty on the first-load failure (never fabricates data)', async () => {
    apiGet.mockRejectedValue(new Error('network down'))

    await loadGardenerDiffs()

    expect(useStore.getState().gardenerDiffs).toEqual([])
  })
})

describe('decideGardenerDiff — non-optimistic write-back (T-08 A3 / BB-106 pattern)', () => {
  it('approve: removes the proposal from the store only after the POST succeeds', async () => {
    mockPending([{ id: 'p1', kind: 'expire_stale', name_a: 'stale_fact' }])
    await loadGardenerDiffs()
    expect(useStore.getState().gardenerDiffs).toHaveLength(1)

    apiSend.mockResolvedValue({ ok: true, id: 'p1', action: 'approve' })

    await decideGardenerDiff('p1', 'approve')

    expect(apiSend).toHaveBeenCalledWith('POST', '/memory/diffs/p1', { action: 'approve' })
    expect(useStore.getState().gardenerDiffs).toHaveLength(0)
  })

  it('reject: removes the proposal from the store only after the POST succeeds', async () => {
    mockPending([{ id: 'p2', kind: 'merge_similar', name_a: 'a', name_b: 'b' }])
    await loadGardenerDiffs()

    apiSend.mockResolvedValue({ ok: true, id: 'p2', action: 'reject' })

    await decideGardenerDiff('p2', 'reject')

    expect(apiSend).toHaveBeenCalledWith('POST', '/memory/diffs/p2', { action: 'reject' })
    expect(useStore.getState().gardenerDiffs).toHaveLength(0)
  })

  it('keeps the proposal and warns when the decision POST fails (BB-106)', async () => {
    mockPending([{ id: 'p3', kind: 'expire_stale', name_a: 'stale_fact' }])
    await loadGardenerDiffs()

    apiSend.mockRejectedValue(new Error('500 Internal Server Error'))

    await decideGardenerDiff('p3', 'approve')

    // The .og file was never confirmed changed — the card must NOT vanish.
    expect(useStore.getState().gardenerDiffs).toHaveLength(1)
    expect(useStore.getState().toasts.some((t) => t.tone === 'blocked')).toBe(true)
  })

  it('does not remove a DIFFERENT proposal than the one decided', async () => {
    mockPending([
      { id: 'keep-me', kind: 'expire_stale', name_a: 'a' },
      { id: 'decide-me', kind: 'expire_stale', name_a: 'b' },
    ])
    await loadGardenerDiffs()

    apiSend.mockResolvedValue({ ok: true, id: 'decide-me', action: 'approve' })

    await decideGardenerDiff('decide-me', 'approve')

    const remaining = useStore.getState().gardenerDiffs.map((p) => p.id)
    expect(remaining).toEqual(['keep-me'])
  })
})
