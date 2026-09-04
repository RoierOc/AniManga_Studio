import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { useUiStore } from './ui'
import { useAnimeStore } from './anime'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue([]), post: vi.fn().mockResolvedValue({}), del: vi.fn().mockResolvedValue({}) },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn() }))

if (typeof globalThis.localStorage === 'undefined') {
  const values = new Map()
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
    clear: () => values.clear(),
  }
}

describe('liberación por lote de Mi Anime', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('confirma una vez, libera cada serie y recarga una sola vez', async () => {
    vi.useFakeTimers()
    try {
      const store = useAnimeStore()
      const ui = useUiStore()
      store.loadLibrary = vi.fn().mockResolvedValue(undefined)
      const pending = store.clearEpisodesBatch([
        { id: 'a/1', title: 'Serie A' },
        { id: 'b', title: 'Serie B' },
        { id: 'novel', title: 'Duplicada' },
        { id: 'a/1', title: 'Duplicada' },
      ])

      expect(ui.confirmDlg.title).toBe('¿Liberar 3 series?')
      ui.resolveConfirm(true)
      expect(await pending).toBe(true)
      expect(api.post).toHaveBeenCalledTimes(3)
      expect(api.post).toHaveBeenNthCalledWith(1, '/api/anime/library/a%2F1/clear_episodes', {
        remove_from_qbt: true, delete_files: true,
      })
      expect(store.loadLibrary).toHaveBeenCalledOnce()
    } finally {
      vi.useRealTimers()
    }
  })

  it('pausa una selección de torrents y refresca sólo al final', async () => {
    const store = useAnimeStore()
    store.loadQbt = vi.fn().mockResolvedValue(undefined)
    store.loadLibrary = vi.fn().mockResolvedValue(undefined)

    const result = await store.qbtActionBatch('pause', ['h1', 'h1', 'h2'])

    expect(result).toEqual({ total: 2, successful: 2, failed: 0 })
    expect(api.post).toHaveBeenCalledTimes(2)
    expect(api.post).toHaveBeenNthCalledWith(1, '/api/anime/qbt/action', {
      action: 'pause', hash: 'h1', delete_files: false,
    })
    expect(store.loadQbt).toHaveBeenCalledOnce()
    expect(store.loadLibrary).toHaveBeenCalledOnce()
  })
})
