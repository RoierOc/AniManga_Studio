/* Enlazar A MANO una carpeta local con su serie. Existía en la app vieja y se quedó sin migrar,
 * así que una carpeta cuyo nombre no se parece al título ("konejeje", nombres de release con
 * etiquetas) no se podía enlazar de ninguna forma: la sugerencia automática fallaba y ahí
 * terminaba el camino.
 *
 * Lo que se fija aquí es el traductor entre AniList y `scan/match`: la búsqueda devuelve `al_id`
 * y el enlace espera `anilist_id`. Si esa correspondencia se rompe se enlaza el ID equivocado —
 * y eso no revienta, simplemente pone la serie que no es.
 */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { useAnimeStore } from './anime'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn().mockResolvedValue({}), del: vi.fn().mockResolvedValue({}) },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn() }))

if (typeof globalThis.localStorage === 'undefined') {
  const m = new Map()
  globalThis.localStorage = {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k), clear: () => m.clear(),
  }
}

describe('búsqueda manual de serie para una carpeta local', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

  it('usa el al_id de AniList al enlazar, no el id interno', async () => {
    const store = useAnimeStore()
    api.get.mockResolvedValue([{ al_id: 21202, id: 999, title: 'KONOSUBA', cover: 'c.jpg',
                                format: 'TV', season_label: 'Winter 2016', episodes: 10 }])
    const f = { folder: '/mnt/d/konejeje', name: 'konejeje', _q: 'konosuba', _results: [] }

    await store.searchScanFolder(f)
    expect(f._results[0]).toEqual({ id: 21202, title: 'KONOSUBA', cover: 'c.jpg',
                                    format: 'TV', season: 'Winter 2016', episodes: 10 })

    await store.matchFolder(f, f._results[0])
    expect(api.post).toHaveBeenCalledWith('/api/anime/scan/match',
      expect.objectContaining({ folder: '/mnt/d/konejeje', anilist_id: 21202 }))
  })

  it('deja sitio a las segundas temporadas', async () => {
    // AniList tiene una entrada POR TEMPORADA y las ordena por parecido del título: buscando
    // "kaguya" la 2ª caía en el puesto 7 y la 3ª en el 9. Con 6 resultados no salían NUNCA.
    const store = useAnimeStore()
    api.get.mockResolvedValue(Array.from({ length: 18 }, (_, i) =>
      ({ al_id: i, title: `Serie ${i}`, cover: '', season_label: `Fall ${2010 + i}` })))
    const f = { _q: 'kaguya', _results: [] }
    await store.searchScanFolder(f)
    expect(f._results.length).toBe(12)
    expect(f._results[8].season).toBe('Fall 2018')   // el año es lo que distingue temporadas
  })

  it('la fila se pone en verde SIN esperar a recargar nada', async () => {
    // Lo que se fija es que enlazar no dependa de `loadScanFolders`: esa recarga tarda y la fila
    // se quedaba diciendo "Sin coincidencia" con el enlace ya hecho.
    const store = useAnimeStore()
    let resolver
    api.post.mockReturnValue(new Promise(r => { resolver = r }))
    const f = { folder: '/mnt/e/Dororo', name: 'Dororo', _results: [], _q: 'dororo' }

    const p = store.matchFolder(f, { id: 20705, title: 'Dororo', cover: 'd.jpg' })
    expect(f.mapped_id).toBe('20705')          // ya, con la petición AÚN en vuelo
    expect(f.matched_title).toBe('Dororo')
    expect(f._results).toEqual([])
    resolver({}); await p
  })

  it('si el servidor rechaza el enlace, la fila se deshace', async () => {
    const store = useAnimeStore()
    api.post.mockRejectedValue(new Error('no existe esa carpeta'))
    const f = { folder: '/mnt/e/Dororo', name: 'Dororo', mapped_id: null, matched_title: '', _results: [] }
    await store.matchFolder(f, { id: 20705, title: 'Dororo', cover: 'd.jpg' })
    expect(f.mapped_id).toBe(null)
    expect(f.matched_title).toBe('')
  })

  it('no sale a la red con una letra suelta', async () => {
    const store = useAnimeStore()
    await store.searchScanFolder({ _q: 'k', _results: [] })
    expect(api.get).not.toHaveBeenCalled()
  })

  it('un fallo de la búsqueda no deja la fila girando para siempre', async () => {
    const store = useAnimeStore()
    api.get.mockRejectedValue(new Error('AniList caído'))
    const f = { _q: 'dororo', _results: [], _searching: false }
    await store.searchScanFolder(f)
    expect(f._searching).toBe(false)
  })
})
