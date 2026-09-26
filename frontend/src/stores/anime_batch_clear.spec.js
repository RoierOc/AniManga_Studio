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

  it('mantiene marcado el torrent agregado tras el refresco tardío de biblioteca', async () => {
    vi.useFakeTimers()
    try {
      const store = useAnimeStore()
      store.qbt.connected = true
      store.torrentAnime = { al_id: 42, title: 'Serie de prueba' }
      store.loadLibrary = vi.fn().mockResolvedValue(undefined)
      api.post.mockResolvedValueOnce({ ok: true }).mockResolvedValue({})

      await store.addToQbt({ episode: 3, info_hash: 'abc123', title: 'Serie de prueba 03' })
      expect(store.isAdded('abc123')).toBe(true)

      await vi.advanceTimersByTimeAsync(3000)

      expect(store.isAdded('abc123')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('restablece y recarga las imágenes de la ficha seleccionada', async () => {
    const store = useAnimeStore()
    const anime = { id: '42', title: 'Serie A' }
    store.library = [anime]
    store.epMeta['42'] = { 1: { still: 'old-still' } }
    store.loadLibrary = vi.fn().mockResolvedValue(undefined)
    store.loadEpMeta = vi.fn().mockResolvedValue(undefined)
    api.post.mockResolvedValue({ ok: true })

    await store.resetAnimeCovers(anime)

    expect(api.post).toHaveBeenCalledWith('/api/anime/library/42/reset_cover', {})
    expect(store.loadLibrary).toHaveBeenCalledOnce()
    expect(store.loadLibrary).toHaveBeenCalledWith(true)
    expect(store.epMeta['42']).toBeUndefined()
    expect(store.loadEpMeta).toHaveBeenCalledWith(anime)
  })

  it('quita el marcador optimista cuando se elimina ese torrent desde la app', async () => {
    const store = useAnimeStore()
    store.addedHashes = ['abc123', 'otro']
    store.loadQbt = vi.fn().mockResolvedValue(undefined)
    store.loadLibrary = vi.fn().mockResolvedValue(undefined)

    await store.qbtAction('delete', 'ABC123')

    expect(store.addedHashes).toEqual(['otro'])
  })
})
