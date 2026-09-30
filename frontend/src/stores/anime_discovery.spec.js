import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn().mockResolvedValue({}), del: vi.fn() },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn() }))

const storage = new Map()
vi.stubGlobal('localStorage', {
  getItem: key => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: key => storage.delete(key),
  clear: () => storage.clear(),
})

import { api } from '@/lib/api'
import { useAnimeStore } from './anime'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((ok, no) => { resolve = ok; reject = no })
  return { promise, resolve, reject }
}

let store

beforeEach(() => {
  storage.clear()
  setActivePinia(createPinia())
  vi.clearAllMocks()
  api.get.mockResolvedValue({ results: [] })
  store = useAnimeStore()
})

afterEach(() => vi.restoreAllMocks())

describe('solicitudes de descubrimiento de anime', () => {
  it('ignora una temporada anterior que responde después de la seleccionada', async () => {
    const winter = deferred()
    const spring = deferred()
    api.get.mockReturnValueOnce(winter.promise).mockReturnValueOnce(spring.promise)

    store.season = 'WINTER'
    store.year = 2026
    const oldRequest = store.loadSeasonal()
    store.season = 'SPRING'
    const currentRequest = store.loadSeasonal()

    spring.resolve({ season: 'SPRING', year: 2026, results: [{ al_id: 2, title: 'Spring' }] })
    await currentRequest
    winter.resolve({ season: 'WINTER', year: 2026, results: [{ al_id: 1, title: 'Winter' }] })
    await oldRequest

    expect(store.seasonal).toEqual([{ al_id: 2, title: 'Spring' }])
  })

  it('comparte la misma petición de temporada mientras sigue en vuelo', async () => {
    const pending = deferred()
    api.get.mockReturnValueOnce(pending.promise)

    const first = store.loadSeasonal()
    const duplicate = store.loadSeasonal()

    expect(api.get).toHaveBeenCalledTimes(1)
    pending.resolve({ season: 'SUMMER', year: 2026, results: [] })
    await Promise.all([first, duplicate])
  })

  it('no mezcla resultados de explorar de filtros anteriores', async () => {
    const popular = deferred()
    const trending = deferred()
    api.get.mockReturnValueOnce(popular.promise).mockReturnValueOnce(trending.promise)

    store.exploreSort = 'popularity'
    const oldRequest = store.loadExplore()
    store.exploreSort = 'trending'
    const currentRequest = store.loadExplore()

    trending.resolve({ results: [{ al_id: 22, title: 'Tendencia' }], hasNextPage: false })
    await currentRequest
    popular.resolve({ results: [{ al_id: 11, title: 'Popular' }], hasNextPage: true })
    await oldRequest

    expect(store.explore).toEqual([{ al_id: 22, title: 'Tendencia' }])
    expect(store.exploreHasNext).toBe(false)
  })

  it('conserva el calendario anterior y registra el error si falla airing', async () => {
    store.airing = { 7: { next_episode: 3 } }
    api.get.mockRejectedValueOnce(new Error('AniList no responde'))

    await store.loadAiring()

    expect(store.airing).toEqual({ 7: { next_episode: 3 } })
    expect(store.airingError).toContain('AniList no responde')
  })

  it('indica si a la biblioteca le falta metadato que todavía está procesando el backfill', () => {
    const complete = {
      banner: 'banner.jpg', genres: ['Drama'], total_episodes: 12, cover_xl: 'cover.jpg',
      synopsis: 'Sinopsis', gen_tags: [],
    }
    store.library = [complete]
    expect(store.needsAnimeMetadataBackfill?.()).toBe(false)

    store.library = [{ ...complete, banner: '' }]
    expect(store.needsAnimeMetadataBackfill?.()).toBe(true)
  })

  it('no fija en caché la respuesta vacía antes de que el backfill TMDB de AniList termine', async () => {
    const anime = { id: '42', al_id: 42, title: 'Serie añadida desde AniList' }
    const still = 'https://image.tmdb.org/t/p/original/episode-1.jpg'
    api.get.mockResolvedValueOnce({ source: null, meta: {} })
      .mockResolvedValueOnce({ source: 'tmdb', meta: { 1: { still } } })

    await store.loadEpMeta(anime)
    expect(store.epMeta['42']).toBeUndefined()

    await store.loadEpMeta({ ...anime, tmdb_id: 123 })

    expect(api.get).toHaveBeenNthCalledWith(1, '/api/anime/episode_meta/42')
    expect(api.get).toHaveBeenNthCalledWith(2, '/api/anime/episode_meta/42')
    expect(store.epMeta['42'][1].still).toBe(still)
  })

  it('reintenta con la ficha enriquecida si llega mientras se usa el fallback MAL', async () => {
    const pending = deferred()
    const anime = { id: '43', al_id: 43, mal_id: 430, title: 'Serie AniList en enriquecimiento' }
    const still = 'https://image.tmdb.org/t/p/original/episode-2.jpg'
    api.get.mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce({ titles: { 2: { title: 'Título provisional MAL' } } })
      .mockResolvedValueOnce({ source: 'tmdb', meta: { 2: { still } } })

    const first = store.loadEpMeta(anime)
    const enriched = store.loadEpMeta({ ...anime, tmdb_id: 456 })
    pending.resolve({ source: null, meta: {} })
    await Promise.all([first, enriched])

    expect(api.get).toHaveBeenCalledTimes(3)
    expect(store.epMeta['43'][2].still).toBe(still)
  })

  it('actualiza el fallback de títulos MAL cuando llega el vínculo TMDB de AniList', async () => {
    const anime = { id: '44', al_id: 44, mal_id: 440 }
    const still = 'https://image.tmdb.org/t/p/original/episode-3.jpg'
    api.get.mockResolvedValueOnce({ source: null, meta: {} })
      .mockResolvedValueOnce({ titles: { 1: { title: 'Título de MAL' } } })
      .mockResolvedValueOnce({ source: 'tmdb', meta: { 1: { title: 'Título TMDB', still } } })

    await store.loadEpMeta(anime)
    expect(store.epMeta['44'][1].title).toBe('Título de MAL')

    await store.loadEpMeta({ ...anime, tmdb_id: 789 })

    expect(store.epMeta['44'][1]).toEqual({ title: 'Título TMDB', still })
    expect(api.get).toHaveBeenCalledTimes(3)
  })
})
