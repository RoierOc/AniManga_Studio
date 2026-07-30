/* El progreso de lectura ahora tiene copia durable, y eso abre una trampa nueva.
 *
 * El backend FUNDE (une los capítulos leídos) para que sincronizar no borre nunca lo leído en
 * otra máquina. Consecuencia: quitar algo —desmarcar un capítulo, borrar una obra— ya no basta
 * con hacerlo en local, porque la siguiente sincronización lo resucitaría. Hay que declararlo.
 *
 * Esto es exactamente la clase de fallo que no se ve al probarlo a mano (todo funciona… hasta que
 * recargas), así que se fija aquí.
 */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const post = vi.fn().mockResolvedValue({})
vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue({}), post: (...a) => post(...a), del: vi.fn().mockResolvedValue({}) },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn(), onStatus: vi.fn() }))

if (typeof globalThis.localStorage === 'undefined') {
  const m = new Map()
  globalThis.localStorage = {
    getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
    removeItem: k => m.delete(k), clear: () => m.clear(),
  }
}

import { useMangaStore } from './manga'

const olvidos = () => post.mock.calls.filter(c => c[0] === '/api/reader/progress/forget').map(c => c[1])

describe('progreso de lectura con copia durable', () => {
  beforeEach(() => { setActivePinia(createPinia()); post.mockClear() })

  it('desmarcar un capítulo DECLARA el olvido (o la fusión lo volvería a marcar)', () => {
    const s = useMangaStore()
    s.current = { id: 'Obra' }
    s.progress = { Obra: { read: { 3: true } } }

    s.toggleChapterRead('3')
    expect(s.progress.Obra.read['3']).toBeUndefined()
    expect(olvidos()).toEqual([{ chapters: { Obra: ['3'] } }])
  })

  it('MARCAR uno no declara ningún olvido: eso lo lleva la fusión normal', () => {
    const s = useMangaStore()
    s.current = { id: 'Obra' }
    s.progress = { Obra: { read: {} } }

    s.toggleChapterRead('4')
    expect(s.progress.Obra.read['4']).toBe(true)
    expect(olvidos()).toEqual([])
  })

  it('borrar el capítulo de disco olvida su marca en la copia', () => {
    const s = useMangaStore()
    s.progress = { Obra: { read: { 9: true }, lastChapter: '9', lastPage: 2, ts: 5 } }

    s._forgetChapterProgress('Obra', '9')
    expect(s.progress.Obra.lastChapter).toBeUndefined()   // no ofrecer reanudar lo que ya no está
    expect(olvidos()).toEqual([{ chapters: { Obra: ['9'] } }])
  })

  it('al reconciliar manda lo local y se queda con lo fundido que devuelve el backend', async () => {
    const s = useMangaStore()
    s.progress = { Obra: { read: { 1: true }, ts: 1 } }
    post.mockResolvedValueOnce({ Obra: { read: { 1: true, 2: true }, ts: 9 } })

    await s._hydrateProgress()
    // lo que se MANDÓ es lo local de antes de fundir (`s.progress` ya es el resultado)
    expect(post.mock.calls[0][0]).toBe('/api/reader/progress')
    expect(post.mock.calls[0][1].Obra.read).toEqual({ 1: true })
    expect(s.progress.Obra.read).toEqual({ 1: true, 2: true })
    expect(JSON.parse(localStorage.getItem('manga-progress-v1')).Obra.ts).toBe(9)
  })

  it('si el backend no responde, la copia local sigue mandando', async () => {
    const s = useMangaStore()
    s.progress = { Obra: { read: { 1: true } } }
    post.mockRejectedValueOnce(new Error('sin backend'))

    await s._hydrateProgress()
    expect(s.progress.Obra.read).toEqual({ 1: true })
  })

  it('pasar de página no dispara una petición por página', async () => {
    vi.useFakeTimers()
    const s = useMangaStore()
    s.progress = {}
    for (let i = 0; i < 20; i++) s._persistProgress()
    expect(post).not.toHaveBeenCalled()          // agrupado
    vi.advanceTimersByTime(2100)
    expect(post.mock.calls.filter(c => c[0] === '/api/reader/progress')).toHaveLength(1)
    vi.useRealTimers()
  })
})
