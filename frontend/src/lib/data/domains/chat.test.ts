import { describe, expect, it } from 'vitest'

import { buildTurnKey, reconstructMessagesFromEvents } from './chat'

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
