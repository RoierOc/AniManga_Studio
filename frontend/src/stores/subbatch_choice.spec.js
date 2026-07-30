/* Elegir la fuente episodio a episodio en el lote de subtítulos.
 *
 * El lote decidía solo (premade→IA) y no había forma de decirle "de este episodio quiero ESTA
 * fuente". Ahora se reutiliza el mismo selector del clic derecho → Traducir en modo `onPick`.
 * Lo que se fija aquí es la costura: que la elección viaje al backend PEGADA a su episodio y no a
 * otro, y que reinyectar mande `force`. Un cruce ahí traduciría la pista de otro capítulo, y el
 * fallo sería mudo (sale un subtítulo, pero el que no es).
 */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const post = vi.fn().mockResolvedValue({ batch_id: 'b1' })
vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue({}), post: (...a) => post(...a), del: vi.fn() },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn(), onStatus: vi.fn() }))

if (typeof globalThis.localStorage === 'undefined') {
  const m = new Map()
  globalThis.localStorage = {
    getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
    removeItem: k => m.delete(k), clear: () => m.clear(),
  }
}

import { useSubBatchStore } from './subbatch'
import { useAnimeStore } from './anime'

const ITEMS = [
  { episode: 1, season: 1, path: '/v/ep1.mkv', titles: ['Show'] },
  { episode: 2, season: 1, path: '/v/ep2.mkv', titles: ['Show'] },
]

function seed() {
  const s = useSubBatchStore()
  s.items = ITEMS
  s.episodes = ITEMS.map(i => ({ ...i, file_present: true, es_status: 'sidecar', has_es: true, source_langs: ['eng'] }))
  s.selected = new Set()
  return s
}

describe('lote de subtítulos: fuente elegida a mano', () => {
  beforeEach(() => { setActivePinia(createPinia()); post.mockClear().mockResolvedValue({ batch_id: 'b1' }) })

  it('la elección viaja PEGADA a su episodio, no a los demás', async () => {
    const s = seed()
    s.choices = { 2: { kind: 'track', sub_index: 3 } }
    s.selected = new Set([1, 2])

    await s.start()
    const enviados = post.mock.calls[0][1].items
    expect(enviados.find(i => i.episode === 1).choice).toBeUndefined()
    expect(enviados.find(i => i.episode === 2).choice).toEqual({ kind: 'track', sub_index: 3 })
  })

  it('reinyectar manda force (sin él el worker los saltaría por "ya tiene español")', async () => {
    const s = seed()
    s.selectRedo()
    expect(s.force).toBe(true)
    expect([...s.selected]).toEqual([1, 2])
    await s.start()
    expect(post.mock.calls[0][1].force).toBe(true)
  })

  it('elegir fuente selecciona ese episodio y sólo ese', async () => {
    const s = seed()
    const anime = useAnimeStore()
    // El selector real sale a la red; aquí se simula que el usuario pulsa una fuente.
    anime.translateSubs = vi.fn(async (a, ep, opts) => opts.onPick({ kind: 'premade', info: { source: 'subdivx' } }))

    await s.pickSource(2)
    expect(s.choices[2]).toEqual({ kind: 'premade', info: { source: 'subdivx' } })
    expect([...s.selected]).toEqual([2])
    // y se le pasa el episodio correcto al selector
    expect(anime.translateSubs.mock.calls[0][1]).toMatchObject({ num: 2, local_path: '/v/ep2.mkv' })
  })

  it('quitar la elección devuelve el episodio a la política del lote', () => {
    const s = seed()
    s.choices = { 1: { kind: 'premade' }, 2: { kind: 'track', sub_index: 0 } }
    s.clearChoice(1)
    expect(s.choices).toEqual({ 2: { kind: 'track', sub_index: 0 } })
  })
})
