/* El store de media tuvo un fallo que tumbaba TODA la biblioteca con
 * "s.search.trim is not a function": el estado `search` (texto) y una acción `search()` se
 * llamaban igual, y en Pinia la acción queda por encima → `store.search` pasaba a ser una función.
 *
 * Es invisible al escribirlo y solo revienta en tiempo de ejecución, así que se fija aquí.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useMediaStore } from './media'

describe('store de media', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('ningún nombre de acción pisa un campo de estado', () => {
    const s = useMediaStore()
    // Si una acción se llamara igual que un campo, aquí dejaría de ser el tipo esperado.
    expect(typeof s.search).toBe('string')
    expect(typeof s.filter).toBe('string')
    expect(typeof s.runSearch).toBe('function')
  })

  it('filtra y ordena la biblioteca sin romperse', () => {
    const s = useMediaStore()
    s.series = [
      { id: 2, kind: 'series', title: 'Breaking Bad', have: 5, total: 62 },
      { id: 9, kind: 'series', title: 'Arcane', have: 9, total: 9 },
    ]
    s.movies = [{ id: 1, kind: 'movie', title: 'Dune', have: 1, total: 1 }]

    expect(s.counts).toEqual({ all: 3, series: 2, movies: 1, missing: 1 })

    s.sort = 'title'
    expect(s.items.map(x => x.title)).toEqual(['Arcane', 'Breaking Bad', 'Dune'])

    // La búsqueda es sobre el CAMPO de texto: este era exactamente el camino que reventaba.
    s.search = 'bad'
    expect(s.items.map(x => x.title)).toEqual(['Breaking Bad'])

    s.search = ''
    s.filter = 'missing'
    expect(s.items.map(x => x.title)).toEqual(['Breaking Bad'])
  })

  // El minuto al salir del episodio: es la costura que hacía que la ficha mostrara el progreso
  // VIEJO hasta recargar. Y "visto" tiene que exigir haber llegado al final de verdad.
  it('notePlayback parchea el progreso en vivo y sólo marca visto al final', () => {
    const s = useMediaStore()
    s.continueItems = [{ series_id: 2, episode_id: 77, pos: 0, duration: 0, at: 0 }]

    s.notePlayback({ key: 'series:2:77', pos: 610.4, duration: 2700, ended: false })
    expect(s.progressByKey['series:2:77']).toEqual({ pos: 610, duration: 2700, watched: false })
    expect(s.continueItems[0].pos).toBe(610)

    // Abandonar faltando <2 min = visto → la posición se limpia (no reanudar en el final).
    s.notePlayback({ key: 'series:2:77', pos: 2650, duration: 2700, ended: false })
    expect(s.progressByKey['series:2:77']).toEqual({ pos: 0, duration: 2700, watched: true })
  })
})
