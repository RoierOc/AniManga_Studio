import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { useNovelsStore } from './novels'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn() },
}))
vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({ toast: vi.fn() }),
}))

const values = new Map()
vi.stubGlobal('localStorage', {
  getItem: (key) => values.has(key) ? values.get(key) : null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
  clear: () => values.clear(),
})

describe('estados del lector de novelas', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    values.clear()
    api.get.mockReset()
    api.post.mockReset()
  })

  it('conserva la biblioteca anterior y declara el fallo al refrescar', async () => {
    const s = useNovelsStore()
    s.library = [{ id: 'n1', title: 'Novela' }]
    api.get.mockRejectedValue(new Error('sidecar offline'))

    await s.loadLibrary()

    expect(s.library).toEqual([{ id: 'n1', title: 'Novela' }])
    expect(s.libraryError).toBe('sidecar offline')
  })

  it('expone el fallo de la ficha para que la vista pueda reintentar', async () => {
    const s = useNovelsStore()
    api.post.mockRejectedValue(new Error('timeout de fuente'))

    await s.openNovel('skynovels', '/obra', 'Obra')

    expect(s.novel).toBeNull()
    expect(s.novelError).toBe('timeout de fuente')
    expect(s.novelLoading).toBe(false)
  })

  it('recuerda qué capítulo falló y limpia el estado al reintentarlo', async () => {
    const s = useNovelsStore()
    s.novel = { title: 'Obra', pluginId: 'src', path: '/obra', chapters: [{ path: '/cap-1', name: 'Uno' }] }
    s.reader = { novelId: 'n1', title: 'Obra', pluginId: 'src', chapterIndex: -1, chapterName: '' }
    api.post
      .mockRejectedValueOnce(new Error('capítulo temporalmente inaccesible'))
      .mockResolvedValueOnce({ html: '<p>Texto</p>', text: 'Texto', words: 1, minutes: 1 })

    await s.goChapter(0)
    expect(s.chapterError).toBe('capítulo temporalmente inaccesible')
    expect(s.chapterErrorIndex).toBe(0)

    await s.goChapter(0)
    expect(s.chapterError).toBe('')
    expect(s.chapterErrorIndex).toBeNull()
    expect(s.reader.chapterIndex).toBe(0)
  })
})
