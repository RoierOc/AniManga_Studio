/* Doble página con DESFASE (la "portada suelta" de MangaDex).
 *
 * Casi todos los tomos abren con una portada que va sola; sin desfase, el emparejamiento 0-1,
 * 2-3… deja el resto del capítulo con las dobles páginas partidas por la mitad. Lo que se rompe
 * al tocar esto no es visible de un vistazo: el PASO al avanzar. Con la primera página sola, un
 * paso fijo de 2 se salta la página 1 sin que nada lo diga — el fallo silencioso clásico.
 */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMangaStore } from './manga'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue({}), post: vi.fn(), del: vi.fn().mockResolvedValue({}) },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn() }))

if (typeof globalThis.localStorage === 'undefined') {
  const m = new Map()
  globalThis.localStorage = {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    clear: () => m.clear(),
  }
}

function reader(pages = 10) {
  const s = useMangaStore()
  s.reader = { kind: 'manga', title: 'T', chapter: '1' }
  s.pages = Array.from({ length: pages }, (_, i) => `p${i}.jpg`)
  s.mode = 'paged'
  s.compareMode = false
  s.spread = true
  s.spreadOffset = 0
  s.page = 0
  return s
}

describe('doble página con desfase', () => {
  beforeEach(() => { setActivePinia(createPinia()) })

  it('sin desfase empareja par-impar', () => {
    const s = reader()
    expect(s.spreadPair).toEqual([0, 1])
    s.nextPage()
    expect(s.spreadPair).toEqual([2, 3])
  })

  it('con desfase la primera página va SOLA', () => {
    const s = reader()
    s.toggleSpreadOffset()
    expect(s.spreadPair).toEqual([0])
  })

  it('con desfase avanzar desde la portada NO se salta la página 1', () => {
    const s = reader()
    s.toggleSpreadOffset()
    s.nextPage()
    expect(s.spreadPair).toEqual([1, 2])   // con paso fijo de 2 saldría [2,3] y la 1 se perdería
  })

  it('volver atrás desde el primer par lleva a la portada, no a negativo', () => {
    const s = reader()
    s.toggleSpreadOffset()
    s.page = 1
    s.prevPage()
    expect(s.page).toBe(0)
    expect(s.spreadPair).toEqual([0])
  })

  it('activar el desfase a media lectura realinea la página actual', () => {
    const s = reader()
    s.page = 4                    // par: inicio de par sin desfase
    s.toggleSpreadOffset()        // con desfase los pares empiezan en impar
    expect(s.page).toBe(3)
    expect(s.spreadPair).toEqual([3, 4])
  })

  it('la última página impar no inventa una pareja fuera de rango', () => {
    const s = reader(5)           // páginas 0..4
    s.page = 4
    expect(s.spreadPair).toEqual([4])
  })

  it('sin doble página el desfase no altera nada', () => {
    const s = reader()
    s.spread = false
    s.spreadOffset = 1
    s.page = 3
    expect(s.spreadPair).toEqual([3])
    s.nextPage()
    expect(s.page).toBe(4)        // paso de 1, como siempre
  })
})
