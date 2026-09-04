import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { useUiStore } from './ui'
import { useMangaStore } from './manga'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue({}), post: vi.fn().mockResolvedValue({}), del: vi.fn().mockResolvedValue({}) },
}))
vi.mock('@/lib/sse', () => ({ onStatus: vi.fn() }))

if (typeof globalThis.localStorage === 'undefined') {
  const values = new Map()
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
    clear: () => values.clear(),
  }
}

describe('borrado de selección en la biblioteca de manga', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('confirma una vez, excluye novelas y hace una sola ronda tras el deshacer', async () => {
    vi.useFakeTimers()
    try {
      const store = useMangaStore()
      const ui = useUiStore()
      const pending = store.deleteMangasBatch([
        { id: 'A', name: 'Obra A', trackedId: 'md-a' },
        { id: 'N', name: 'Novela', kind: 'novel', trackedId: 'novel-n' },
        { id: 'B', name: 'Obra B' },
      ])

      expect(ui.confirmDlg.title).toBe('¿Quitar 2 mangas?')
      ui.resolveConfirm(true)
      expect(await pending).toBe(true)
      expect(store.pendingDelete).toEqual(['A', 'B'])

      await vi.advanceTimersByTimeAsync(6000)
      expect(api.del).toHaveBeenCalledTimes(2)
      expect(api.del).toHaveBeenNthCalledWith(1, '/api/download/delete_manga', {
        body: { title: 'A', trackedId: 'md-a' },
      })
      expect(api.del).toHaveBeenNthCalledWith(2, '/api/download/delete_manga', {
        body: { title: 'B', trackedId: null },
      })
      expect(store.pendingDelete).toEqual([])
    } finally {
      vi.useRealTimers()
    }
  })
})
