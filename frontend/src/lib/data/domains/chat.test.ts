import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn();
const apiSend = vi.fn();

// Canonical mock block (see approvals.test.ts / store.test.ts). chat.ts
// statically imports useStore, ../client and ../sse at module scope (plus
// ./approvals, ./spend and ../../notifications, which themselves import
// ../client) — the "live-session reset" describe block below imports chat.ts's
// live functions, which pulls in the whole graph. All three need mocking or
// the import performs real network/storage calls.
vi.mock('../client', () => ({
  USE_MOCKS: false,
  apiGet: (...args: unknown[]) => apiGet(...args),
  apiSend: (...args: unknown[]) => apiSend(...args),
  registerReconnectCallback: vi.fn(),
  isNetworkError: () => false,
  API_BASE: '/api',
  authHeaders: () => ({}),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
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

import { buildTurnKey, extractInjectedAttachments, reconstructMessagesFromEvents } from './chat'

describe('reconstructMessagesFromEvents', () => {
  it('reconstructs text and tool state in event order', () => {
    const result = reconstructMessagesFromEvents([
      { sequence: 1, event_type: 'session.turn.started', payload: { user_text: 'hello' } },
      { sequence: 2, event_type: 'text.stream.delta', payload: { text: 'world' } },
      { sequence: 3, event_type: 'tool.call.started', payload: { name: 'search' } },
      {
        sequence: 4,
        event_type: 'tool.call.completed',
        payload: { name: 'search', result_preview: 'found it' },
      },
      { sequence: 5, event_type: 'session.chat.completed', payload: {} },
    ])

    expect(result.lastSeq).toBe(5)
    expect(result.messages).toHaveLength(2)
    expect(result.messages[0].text).toBe('hello')
    expect(result.messages[1].text).toBe('world')
    expect(result.messages[1].cards[0]).toMatchObject({
      key: 'tool-search',
      status: 'done',
      detail: 'found it',
    })
  })

  it('replays 10,000 turns without quadratic message scans', () => {
    const events: Array<{
      sequence: number
      event_type: string
      payload: Record<string, unknown>
    }> = []
    let sequence = 0
    for (let turn = 0; turn < 10_000; turn += 1) {
      events.push({
        sequence: ++sequence,
        event_type: 'session.turn.started',
        payload: { user_text: `turn ${turn}` },
      })
      for (let delta = 0; delta < 10; delta += 1) {
        events.push({
          sequence: ++sequence,
          event_type: 'text.stream.delta',
          payload: { text: 'x' },
        })
      }
      events.push({ sequence: ++sequence, event_type: 'session.chat.completed', payload: {} })
    }

    const started = performance.now()
    const result = reconstructMessagesFromEvents(events)
    const elapsedMs = performance.now() - started

    expect(result.messages).toHaveLength(20_000)
    expect(result.messages.at(-1)?.text).toBe('xxxxxxxxxx')
    expect(elapsedMs).toBeLessThan(800)
  })
})

describe('extractInjectedAttachments — strip tool-protocol injection (BB-84)', () => {
  const SAMPLE =
    'explain the image\n\n[Attached image: hachi logo.png]\n(Use the view_image tool with path: C:\\Users\\foo\\blobs\\f89b)'

  it('strips injection block and returns name', () => {
    const result = extractInjectedAttachments(SAMPLE)
    expect(result.text).toBe('explain the image')
    expect(result.attachments).toEqual([{ name: 'hachi logo.png' }])
  })

  it('passes through text with no injection unchanged', () => {
    const result = extractInjectedAttachments('just a normal message')
    expect(result.text).toBe('just a normal message')
    expect(result.attachments).toHaveLength(0)
  })

  it('extracts two attachments from one message', () => {
    const two =
      'caption\n\n[Attached image: a.png]\n(Use the view_image tool with path: /tmp/a)' +
      '\n\n[Attached file: b.pdf]\n(Use the read_file tool with path: /tmp/b)'
    const result = extractInjectedAttachments(two)
    expect(result.text).toBe('caption')
    expect(result.attachments).toEqual([{ name: 'a.png' }, { name: 'b.pdf' }])
  })
})

describe('buildTurnKey', () => {
  // BB-12: _id in store.ts resets to 100 on every page load. Since the gateway
  // session UUID is preserved across refreshes (sessionStorage), the key
  // `turn-102` from a prior load would match the new turn and replay the old
  // response instantly. The nonce breaks this collision.
  it('produces different keys for the same hachiMsgId with different page-load nonces', () => {
    const key1 = buildTurnKey('aaaa', 102)
    const key2 = buildTurnKey('bbbb', 102)
    expect(key1).not.toBe(key2)
  })

  it('produces the same key for the same nonce and id (retry behaviour preserved)', () => {
    expect(buildTurnKey('aaaa', 102)).toBe(buildTurnKey('aaaa', 102))
  })
})

// ============================================================
// T-65 (CF-16): chat.ts kept 17 module-level mutables as a runtime kernel
// (the live session id, SSE cursor, streaming delta buffers, rAF flush
// handles and per-turn cost baselines). The ~10-line reset sequence was
// duplicated by hand across four functions (loadConversationHistory's
// "no sessions left" guard, switchSessionLive, deleteSessionLive,
// newSessionLive) instead of living in one place.
// ============================================================
describe('live-session reset (T-65 / CF-16)', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiSend.mockReset()
    // Permissive defaults: every gateway call the reset paths touch
    // (GET /sessions/{id}/events, GET /sessions, DELETE /sessions/{id})
    // resolves cleanly so the reset block under test actually runs instead
    // of short-circuiting through a catch — see the spec's note on
    // deleteSessionLive's apiSend("DELETE", ...) needing to resolve.
    apiGet.mockResolvedValue([])
    apiSend.mockResolvedValue({})
  })

  it('every session-reset path leaves identical kernel state', async () => {
    // Regression: the ~10-line reset sequence (abortSse, resetDeltaBuffer,
    // _liveSessionId, persistSessionId, _cursor, _hachiMsgId, _textLineActive,
    // _lastKnownCumCost, _costCumAtTurnStart, _spentAtTurnStart) was copied
    // by hand into FOUR functions. A fifth caller — or an edit applied to
    // three of the four — would silently leave a stale cursor and replay a
    // previous session's events into a new one.
    const chat = await import('./chat')

    await chat.switchSessionLive('session-a')
    chat.newSessionLive()
    const afterNew = chat.getKernelSnapshot()

    await chat.switchSessionLive('session-b')
    await chat.deleteSessionLive('session-b')
    const afterDelete = chat.getKernelSnapshot()

    expect(afterDelete).toEqual(afterNew)
    expect(afterNew.liveSessionId).toBeNull()
    expect(afterNew.cursor).toBe(0)
    expect(afterNew.hachiMsgId).toBeNull()
    expect(afterNew.textLineActive).toBe(false)
  })
})
