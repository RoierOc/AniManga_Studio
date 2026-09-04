/* «Falló» y «no había nada» NO pueden ser el mismo estado — la regla de oro del repo, que el
 * backend respeta y el frontend no respetaba.
 *
 * El fallo concreto: `media.load()` tenía `try`/`finally` SIN `catch`, así que cuando el backend
 * no respondía la lista se quedaba en `[]` y la vista anunciaba «Tu biblioteca está vacía · ve a
 * añadir series» — con la biblioteca llena. Es peor que un error a secas: manda al usuario a
 * arreglar algo que no está roto y le hace temer que ha perdido sus datos.
 *
 * Lo que se fija aquí es la CONDICIÓN QUE LA VISTA CONSULTA para elegir entre error y vacío.
 * Sin esto, el próximo `catch (_) { lista = [] }` vuelve a colar la mentira sin que nada chille.
 */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { useMediaStore } from './media'
import { useAnimeStore } from './anime'
import { useMangaStore } from './manga'
import { useWorkshopStore } from './workshop'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), del: vi.fn().mockResolvedValue({}) },
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

beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

describe('una carga que falla no puede parecer una biblioteca vacía', () => {
  it('media: deja loadError y la vista NO puede afirmar que está vacía', async () => {
    api.get.mockRejectedValue(new Error('Failed to fetch'))
    const s = useMediaStore()

    await s.load()

    expect(s.loadError).toBeTruthy()          // el fallo SOBREVIVE en el estado
    expect(s.loaded).toBe(false)              // y no se marca como cargada
    expect(s.loading).toBe(false)             // el finally sigue soltando el spinner
    expect(s.items).toEqual([])               // la lista sí queda vacía…
    // …pero `loadError` es lo que la vista mira ANTES del estado vacío, así que la
    // combinación "sin items + con error" significa error, nunca "no tienes nada".
  })

  it('media: una carga correcta limpia el error de un intento anterior', async () => {
    const s = useMediaStore()
    s.loadError = 'fallo viejo'
    api.get.mockResolvedValue({ series: [{ id: 1, kind: 'series', title: 'Arcane' }], movies: [] })

    await s.load()

    expect(s.loadError).toBe('')
    expect(s.loaded).toBe(true)
    expect(s.items).toHaveLength(1)
  })

  it('anime: la biblioteca distingue "falló" de "no has añadido nada"', async () => {
    api.get.mockRejectedValue(new Error('ECONNREFUSED'))
    const s = useAnimeStore()

    await s.loadLibrary(true)   // silent: ni siquiera hay toast que avise

    expect(s.loadError).toBeTruthy()
    expect(s.library).toEqual([])
  })

  it('anime: una búsqueda que revienta no es una búsqueda sin resultados', async () => {
    api.get.mockRejectedValue(new Error('Failed to fetch'))
    const s = useAnimeStore()
    s.searchQuery = 'frieren'

    await s.searchAnime()

    expect(s.searchError).toBeTruthy()
    expect(s.searchResults).toEqual([])
  })

  it('los historiales conservan la causa y no se convierten en listas vacías', async () => {
    api.get.mockRejectedValue(new Error('historial fuera de línea'))

    const anime = useAnimeStore()
    await anime.loadHistory()
    expect(anime.historyError).toBe('historial fuera de línea')
    expect(anime.historyLoaded).toBe(true)

    const manga = useMangaStore()
    await manga.loadHistory()
    expect(manga.historyError).toBe('historial fuera de línea')
    expect(manga.historyLoaded).toBe(true)
  })

  it('el Taller distingue una lista vacía de un fallo al listar importaciones', async () => {
    api.get.mockRejectedValue(new Error('Taller no disponible'))
    const s = useWorkshopStore()

    await s.loadList()

    expect(s.loadError).toBe('Taller no disponible')
    expect(s.loading).toBe(false)
    expect(s.items).toEqual([])
  })
})

describe('un servicio que falla al LISTAR también se dice', () => {
  it('offline incluye lo que reventó en /library, no sólo lo que no contesta al ping', async () => {
    // El caso que se colaba: Radarr responde a `system/status` (online) pero `movie` revienta.
    // El backend lo declaraba en `errors.radarr`… que no leía ninguna vista, así que la sección
    // de películas salía vacía como si no tuvieras ninguna.
    api.get.mockImplementation((u) => {
      if (u === '/api/media/status') return Promise.resolve({
        sonarr: { online: true }, radarr: { online: true },
      })
      if (u === '/api/media/library') return Promise.resolve({
        series: [{ id: 1, kind: 'series', title: 'A' }], movies: [],
        errors: { radarr: 'Connection refused' },
      })
      return Promise.resolve({})
    })
    const store = useMediaStore()
    await store.load()

    expect(store.loadError).toBe('')          // la carga no falló entera
    expect(store.offline).toContain('radarr') // pero la vista tiene qué decir
    expect(store.movies).toEqual([])
  })
})
