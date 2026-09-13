/* El hero de Mi Anime: el 40 % de las sesiones lo llena TU biblioteca, al azar.
 *
 * Se prueba la costura que puede fallar en silencio: que el dado apagado deja la cadena de
 * siempre intacta, que encendido no cuela obras sin arte (serían un rectángulo gris) y que sin
 * biblioteca con arte NO devuelve vacío — un hero vacío no se distingue de «aún cargando».
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// El entorno de los tests es `node`: sin esto, el store revienta al leer `anime-sub` al crearse.
const _mem = {}
vi.stubGlobal('localStorage', {
  getItem: (k) => (k in _mem ? _mem[k] : null),
  setItem: (k, v) => { _mem[k] = String(v) },
  removeItem: (k) => { delete _mem[k] },
})

import { useAnimeStore } from './anime'

const conArte = (id, extra = {}) => ({
  al_id: id, id, title: `Serie ${id}`, banner: `b${id}.jpg`, episodes: [], ...extra,
})

describe('heroItems · biblioteca al azar', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('con el dado apagado sigue la cadena de siempre (temporada)', () => {
    const s = useAnimeStore()
    s.heroBiblioteca = false
    s.library = [conArte(1)]
    s.seasonal = [{ al_id: 99, title: 'De temporada', popularity: 10 }]
    expect(s.heroItems.map(i => i.kind)).toContain('seasonal')
    expect(s.heroItems.some(i => i.kind === 'library')).toBe(false)
  })

  it('con el dado encendido saca la biblioteca, y sólo la que tiene arte', () => {
    const s = useAnimeStore()
    s.heroBiblioteca = true
    s.library = [conArte(1), { al_id: 2, id: 2, title: 'Sin arte', episodes: [] }, conArte(3)]
    const items = s.heroItems
    expect(items.every(i => i.kind === 'library')).toBe(true)
    expect(items.map(i => i.anime.al_id).sort()).toEqual([1, 3])
  })

  it('sin biblioteca con arte cae a la cadena, no al vacío', () => {
    const s = useAnimeStore()
    s.heroBiblioteca = true
    s.library = [{ al_id: 2, id: 2, title: 'Sin arte', episodes: [] }]
    s.seasonal = [{ al_id: 99, title: 'De temporada', popularity: 10 }]
    expect(s.heroItems.map(i => i.kind)).toEqual(['seasonal'])
  })

  it('el dado se vuelve a tirar en cada entrada, y mueve también la ventana', () => {
    const s = useAnimeStore()
    const semillas = new Set()
    for (let i = 0; i < 50; i++) { s.tirarDadoHero(); semillas.add(s.recSeed) }
    // Con 50 tiradas, una sola semilla significaría que no se está re-tirando nada.
    expect(semillas.size).toBeGreaterThan(1)
    expect(typeof s.heroBiblioteca).toBe('boolean')
  })

  it('marca hasFile sólo cuando el episodio está en disco', () => {
    const s = useAnimeStore()
    s.heroBiblioteca = true
    s.library = [conArte(1, { episodes: [{ num: 1, in_local: true }] }), conArte(2)]
    const porId = Object.fromEntries(s.heroItems.map(i => [i.anime.al_id, i.hasFile]))
    expect(porId).toEqual({ 1: true, 2: false })
  })
})
