/* Buscar MIENTRAS se escribe abre una carrera que no existía cuando había que pulsar Enter:
 * cada pulsación lanza una petición, y la red no garantiza el orden de llegada. El caso malo es
 * el normal — escribes "naruto", la respuesta de "nar" tarda más y llega la ÚLTIMA, así que
 * pisa a la buena y te quedas mirando resultados que no corresponden a lo que pone en la caja.
 *
 * No falla nunca en local (todo responde en 2 ms) y sí en cuanto hay latencia, así que es
 * exactamente el tipo de fallo silencioso que este repo fija con un test.
 */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { useMediaStore } from './media'
import { useAnimeStore } from './anime'

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

const defer = () => { let r; const p = new Promise(res => { r = res }); return { p, resolve: r } }

beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

describe('una respuesta que llega tarde no puede pisar a la buena', () => {
  it('media: gana la última consulta, no la última respuesta', async () => {
    const lenta = defer(), rapida = defer()
    api.get.mockReturnValueOnce(lenta.p).mockReturnValueOnce(rapida.p)
    const s = useMediaStore()

    const p1 = s.runSearch('bre')          // se lanza primera…
    const p2 = s.runSearch('breaking')     // …pero el usuario sigue escribiendo

    rapida.resolve({ results: [{ ext_id: 2, title: 'Breaking Bad' }] })
    await p2
    lenta.resolve({ results: [{ ext_id: 1, title: 'RESULTADO VIEJO' }] })
    await p1

    expect(s.results.map(r => r.title)).toEqual(['Breaking Bad'])
    expect(s.searching).toBe(false)        // y el spinner queda apagado, no colgado
  })

  it('media: un fallo que llega tarde no borra los resultados buenos', async () => {
    const lenta = defer(), rapida = defer()
    api.get.mockReturnValueOnce(lenta.p).mockReturnValueOnce(rapida.p)
    const s = useMediaStore()

    const p1 = s.runSearch('bre')
    const p2 = s.runSearch('breaking')

    rapida.resolve({ results: [{ ext_id: 2, title: 'Breaking Bad' }] })
    await p2
    lenta.resolve(Promise.reject(new Error('timeout')))
    await p1.catch(() => {})

    expect(s.results.map(r => r.title)).toEqual(['Breaking Bad'])
    expect(s.searchErr).toBe('')           // el error de la vieja no se pinta
  })

  it('anime: gana la última consulta', async () => {
    const lenta = defer(), rapida = defer()
    api.get.mockReturnValueOnce(lenta.p).mockReturnValueOnce(rapida.p)
    const s = useAnimeStore()

    s.searchQuery = 'nar'
    const p1 = s.searchAnime()
    s.searchQuery = 'naruto'
    const p2 = s.searchAnime()

    rapida.resolve([{ al_id: 20, title: 'Naruto' }])
    await p2
    lenta.resolve([{ al_id: 99, title: 'RESULTADO VIEJO' }])
    await p1

    expect(s.searchResults.map(r => r.title)).toEqual(['Naruto'])
    expect(s.searchLoading).toBe(false)
  })

  it('borrar la consulta limpia los resultados en vez de dejarlos colgados', async () => {
    const s = useAnimeStore()
    s.searchResults = [{ al_id: 1, title: 'viejo' }]
    s.searchQuery = 'a'                    // por debajo del mínimo de 2 letras

    await s.searchAnime()

    expect(s.searchResults).toEqual([])
    expect(s.searchLoading).toBe(false)
  })
})
