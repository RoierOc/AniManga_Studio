/* El store de media tuvo un fallo que tumbaba TODA la biblioteca con
 * "s.search.trim is not a function": el estado `search` (texto) y una acción `search()` se
 * llamaban igual, y en Pinia la acción queda por encima → `store.search` pasaba a ser una función.
 *
 * Es invisible al escribirlo y solo revienta en tiempo de ejecución, así que se fija aquí.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useMediaStore } from './media'
import { useTagsStore } from './tags'

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

    // Sin historial ni progreso, las tres están «sin empezar» y ninguna vista: los contadores de
    // visionado se DERIVAN, así que su estado por defecto tiene que ser el neutro y no un hueco.
    expect(s.counts).toEqual({ all: 3, series: 2, movies: 1, missing: 1,
                              watching: 0, seen: 0, unseen: 3 })

    s.sort = 'title'
    expect(s.items.map(x => x.title)).toEqual(['Arcane', 'Breaking Bad', 'Dune'])

    // La búsqueda es sobre el CAMPO de texto: este era exactamente el camino que reventaba.
    s.search = 'bad'
    expect(s.items.map(x => x.title)).toEqual(['Breaking Bad'])

    s.search = ''
    s.filter = 'missing'
    expect(s.items.map(x => x.title)).toEqual(['Breaking Bad'])
  })

  /* El estado de visionado no lo marca el usuario: se DERIVA de dos listas que pueden decir cosas
     distintas de la misma obra. Ese cruce es justo donde se cuela el fallo. */
  describe('estado de visionado derivado', () => {
    const conBiblioteca = () => {
      const s = useMediaStore()
      s.series = [
        { id: 2, kind: 'series', title: 'Breaking Bad', have: 5, total: 62 },
        { id: 9, kind: 'series', title: 'Arcane', have: 9, total: 9 },
        { id: 4, kind: 'series', title: 'The Bear', have: 0, total: 46 },
      ]
      s.movies = [{ id: 1, kind: 'movie', title: 'Dune', have: 1, total: 1 }]
      return s
    }

    it('«viendo» gana a «vista» cuando la obra está en las dos listas', () => {
      const s = conBiblioteca()
      // Viste 8 episodios de Breaking Bad Y vas por el 9: las dos cosas son ciertas a la vez.
      s.watched = [{ kind: 'series', series_id: 2, watched: true }]
      s.continueItems = [{ series_id: 2, episode_id: 30 }]
      expect(s.watchState['series:2']).toBe('watching')
      expect(s.counts.watching).toBe(1)
      expect(s.counts.seen).toBe(0)
    })

    it('«sin empezar» exige tenerlo EN DISCO', () => {
      const s = conBiblioteca()
      // The Bear tiene 0 episodios descargados: no es un plan para esta noche.
      expect(s.items.map(x => x.title)).toContain('The Bear')
      s.filter = 'unseen'
      expect(s.items.map(x => x.title)).toEqual(['Arcane', 'Breaking Bad', 'Dune'].filter(
        t => s.items.map(x => x.title).includes(t)))
      expect(s.items.map(x => x.title)).not.toContain('The Bear')
    })

    it('el progreso EN VIVO de una película pisa al historial', () => {
      const s = conBiblioteca()
      s.watched = [{ kind: 'movie', movie_id: 1, watched: true }]
      expect(s.watchState['movie:1']).toBe('seen')
      // La vuelves a poner: deja de estar «vista» en cuanto el reproductor dice dónde vas.
      s.progressByKey['movie:1'] = { pos: 300, duration: 9000, watched: false }
      expect(s.watchState['movie:1']).toBe('watching')
    })

    it('una etiqueta filtra por la identidad <kind>:<id>, no por el id suelto', () => {
      const s = conBiblioteca()
      const tags = useTagsStore()
      tags.media = { 'movie:1': ['sci-fi'], 'series:9': ['sci-fi'] }
      s.filter = 'tag:sci-fi'
      // Si el filtro usara sólo el id, la serie 1 (que no existe) o la película 9 se colarían.
      expect(s.items.map(x => x.title).sort()).toEqual(['Arcane', 'Dune'])
    })
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
