// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

class FakeEventSource {
  static instances = []

  constructor(url) {
    this.url = url
    this.readyState = 0
    this.closed = false
    FakeEventSource.instances.push(this)
  }

  close() {
    this.closed = true
    this.readyState = 2
  }
}

describe('reintento del canal de Actividad', () => {
  let previousEventSource
  let sse

  beforeEach(async () => {
    previousEventSource = globalThis.EventSource
    FakeEventSource.instances = []
    globalThis.EventSource = FakeEventSource
    vi.resetModules()
    sse = await import('./sse')
  })

  afterEach(() => {
    if (previousEventSource === undefined) delete globalThis.EventSource
    else globalThis.EventSource = previousEventSource
  })

  it('cierra la conexión vieja y abre una nueva sin duplicar suscriptores', () => {
    const listener = vi.fn()
    const unsubscribe = sse.onStatus(listener)
    const first = FakeEventSource.instances[0]

    sse.retrySse()

    expect(first.closed).toBe(true)
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.instances[1].url).toBe('/api/status/stream')
    expect(sse.sseState.value).toBe('connecting')
    unsubscribe()
  })

  it('marca error de snapshot como desconectado y acepta el siguiente latido válido', () => {
    sse.onStatus(vi.fn())
    const source = FakeEventSource.instances[0]
    source.onopen()
    expect(sse.sseState.value).toBe('live')

    source.onmessage({ data: '{"_error":true}' })
    expect(sse.sseState.value).toBe('down')

    source.onmessage({ data: '{"hb":1}' })
    expect(sse.sseState.value).toBe('live')
  })
})
