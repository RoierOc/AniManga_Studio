import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiGet, apiPost, openReaderRaw, toast } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  openReaderRaw: vi.fn(),
  toast: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ api: { get: apiGet, post: apiPost } }))
vi.mock('./manga', () => ({ useMangaStore: () => ({ openReaderRaw }) }))
vi.mock('./ui', () => ({ useUiStore: () => ({ toast }) }))

import { useCbzStore } from './cbz'

describe('progreso de la biblioteca CBZ/CBR', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    apiPost.mockResolvedValue({ success: true })
  })

  it('reanuda el tomo incompleto por su nombre exacto de archivo', async () => {
    const store = useCbzStore()
    store.current = { title: 'Solo Leveling' }
    apiGet.mockResolvedValue({ pages: ['/page/0', '/page/1', '/page/2'] })

    await store.readVolume({ name: 'Tomo 1.cbz', page_count: 3, progress_page: 1, read: false })

    expect(openReaderRaw).toHaveBeenCalledWith(
      'Solo Leveling', ['/page/0', '/page/1', '/page/2'], 'Tomo 1', 'Tomo 1.cbz', 1,
    )
  })

  it('reabre un tomo leído desde la portada y persiste cambios de página', async () => {
    const store = useCbzStore()
    store.current = { title: 'Solo Leveling' }
    apiGet.mockResolvedValue({ pages: ['/page/0', '/page/1', '/page/2'] })

    await store.readVolume({ name: 'Tomo 1.cbz', page_count: 3, progress_page: 2, read: true })
    store.queueProgress('Solo Leveling', 'Tomo 1.cbz', 2, 3, true)
    await store.flushProgress()

    expect(openReaderRaw).toHaveBeenCalledWith(
      'Solo Leveling', ['/page/0', '/page/1', '/page/2'], 'Tomo 1', 'Tomo 1.cbz', 0,
    )
    expect(apiPost).toHaveBeenCalledWith('/api/cbz/progress', {
      manga: 'Solo Leveling', volume: 'Tomo 1.cbz', page: 2, read: true,
      page_count: 3,
    })
  })
})
