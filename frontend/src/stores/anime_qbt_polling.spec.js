import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn().mockResolvedValue({}), del: vi.fn() },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn() }))

if (typeof globalThis.localStorage === 'undefined') {
  const m = new Map()
  globalThis.localStorage = {
    getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
    removeItem: k => m.delete(k), clear: () => m.clear(),
  }
}

import { useAnimeStore } from './anime'

let store

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  vi.clearAllMocks()
  api.get.mockResolvedValue([])
  store = useAnimeStore()
})

afterEach(() => {
  store.stopQbtPolling()
  vi.useRealTimers()
})

describe('sondeo compartido de qBittorrent', () => {
  it('mantiene un solo ciclo para Descargas y lo detiene al salir', async () => {
    await store.startQbtPolling()
    await store.startQbtPolling()
    expect(api.get).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(5000)
    expect(api.get).toHaveBeenCalledTimes(2)

    store.stopQbtPolling()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('el progreso por archivo refresca el resumen compacto', async () => {
    store.library = [{ id: 'anime', episodes: [{
      num: 1, in_qbt: true, progress: 20, info_hash: 'batch', file_index: 4,
    }] }]
    api.get.mockResolvedValueOnce([{ id: 'anime', episodes: [{
      num: 1, in_qbt: true, progress: 25,
    }] }])

    await store._applyQbtProgress([{ hash: 'batch', progress: 25 }])

    expect(api.get).toHaveBeenCalledWith('/api/anime/library?summary=1')
    expect(store.library[0].episodes[0].progress).toBe(25)
  })

  it('al completar un torrent pide el resumen y completa datos si aparece una ruta nueva', async () => {
    store.library = [{ id: 'anime', episodes: [{
      num: 1, in_qbt: true, progress: 90, info_hash: 'hash',
    }] }]
    api.get
      .mockResolvedValueOnce([{ id: 'anime', episodes: [{ num: 1, in_local: true, progress: 100 }] }])
      .mockResolvedValueOnce([{ id: 'anime', episodes: [{
        num: 1, in_local: true, progress: 100, local_path: '/anime/ep01.mkv',
      }] }])

    await store._applyQbtProgress([{ hash: 'hash', progress: 100 }])

    expect(api.get.mock.calls.map(([path]) => path)).toEqual([
      '/api/anime/library?summary=1', '/api/anime/library',
    ])
    expect(store.library[0].episodes[0].local_path).toBe('/anime/ep01.mkv')
  })

  it('no duplica una petición de lista que todavía está en vuelo', async () => {
    let finish
    api.get.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const first = store.loadQbt()
    const second = store.loadQbt()

    expect(api.get).toHaveBeenCalledTimes(1)
    finish([{ hash: 'one' }])
    await Promise.all([first, second])
    expect(store.qbtTorrents).toEqual([{ hash: 'one' }])
  })
})
