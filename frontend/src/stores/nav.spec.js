/* El historial de navegación estuvo roto DESDE SIEMPRE y en silencio.
 *
 * `history.pushState` serializa su estado con *structured clone*, que **lanza** al encontrarse un
 * proxy reactivo de Vue. `snapshot()` devuelve varios (`modeHome` es estado del store,
 * `manga.source_meta` es un objeto anidado del store), así que `pushState` fallaba en cada
 * navegación… dentro de un `catch {}` vacío. Nunca hubo entradas en el historial: ni los botones
 * laterales del ratón, ni Alt+←, ni unos botones en pantalla hacían nada.
 *
 * Lo pérfido: `persist()` guardaba el MISMO snapshot con `JSON.stringify` y funcionaba, así que
 * "recordar la vista al recargar" iba bien y parecía que la navegación estaba sana.
 *
 * Este test fija el contrato: lo que va al historial tiene que sobrevivir a structuredClone.
 */
import { describe, it, expect } from 'vitest'
import { reactive, ref } from 'vue'
import { plainState } from './ui'

describe('estado del historial · structured clone', () => {
  it('EL FALLO: un objeto reactivo de Vue NO se puede meter en pushState', () => {
    expect(() => structuredClone(reactive({ a: 1 }))).toThrow()
    expect(() => structuredClone(reactive({ m: { source_meta: { sourceId: 7 } } }))).toThrow()
  })

  it('plainState() lo deja clonable', () => {
    const snap = {
      view: 'library',
      modeHome: reactive({ anime: 'library', cine: 'media' }),          // estado del store
      manga: { id: 'x', source_meta: reactive({ sourceId: 7, mangaId: 'a' }) },
    }
    expect(() => structuredClone(snap)).toThrow()                        // como estaba
    expect(() => structuredClone(plainState(snap))).not.toThrow()        // como queda
  })

  it('conserva el contenido, no sólo la clonabilidad', () => {
    const out = plainState({
      view: 'anime',
      sub: 'library',
      modeHome: reactive({ anime: 'library', cine: 'media' }),
      manga: { id: 'Amayo', source_meta: reactive({ sourceId: 3 }) },
    })
    expect(out.view).toBe('anime')
    expect(out.sub).toBe('library')
    expect(out.modeHome).toEqual({ anime: 'library', cine: 'media' })
    expect(out.manga.source_meta).toEqual({ sourceId: 3 })
  })

  it('también aplana el .value de un ref anidado', () => {
    const snap = { manga: ref({ id: 1 }).value }
    expect(() => structuredClone(plainState(snap))).not.toThrow()
  })

  it('no revienta con lo no serializable: devuelve {} en vez de propagar', () => {
    const circular = {}
    circular.self = circular
    expect(plainState(circular)).toEqual({})
  })
})

/* Segunda tanda: el historial existía pero le faltaban PÁGINAS.
 *
 * Un sitio que cambia lo que ves y NO deja entrada rompe el botón de atrás dos veces: no puedes
 * deshacer ese paso, y el siguiente "atrás" te saca un escalón de más — que es exactamente el
 * síntoma reportado ("me manda a vistas que no tienen nada que ver"). Y al revés: empujar una
 * entrada cuyo snapshot NO recuerda lo que abriste crea un duplicado invisible, así que "atrás"
 * parece no hacer nada y el segundo salta dos.
 *
 * Este test fija la lista de lo que tiene que viajar. Es una lista, no una abstracción: la app no
 * tiene router, el estado ES la URL, y lo único que falla es olvidarse de un campo.
 */
describe('snapshot del historial · qué páginas registra', () => {
  const CAMPOS = ['view', 'sub', 'animeDetail', 'preview', 'manga', 'reader', 'mediaDetail',
                  'novel', 'novelReader', 'tabs', 'mode']

  it('lleva todas las pantallas que el usuario percibe como una página', async () => {
    const { setActivePinia, createPinia } = await import('pinia')
    setActivePinia(createPinia())
    const { useUiStore } = await import('./ui')
    const snap = useUiStore().snapshot()
    for (const k of CAMPOS) expect(Object.keys(snap)).toContain(k)
  })

  it('sobrevive a structuredClone con todo relleno (es lo que hace pushState)', () => {
    const snap = {
      view: 'library', sub: 'seasonal', animeDetail: 12,
      preview: { al_id: 1, title: 'X' }, manga: { id: 'a', source_meta: { url: 'u' } },
      reader: { title: 'a', chapter: '3', source: 'auto', kind: 'manga' },
      mediaDetail: { id: 7, kind: 'series', title: 'R' },
      novel: { novelId: 'n', title: 'N' }, novelReader: { novelId: 'n', chapterIndex: 2 },
      tabs: { lib: 'local', exp: 'sources', set: 'salud', media: 'discover' }, mode: 'anime',
    }
    expect(() => structuredClone(plainState(snap))).not.toThrow()
    expect(plainState(snap).tabs.lib).toBe('local')
  })

  it('setTab registra el cambio de pestaña, y no hace nada si ya estabas en ella', async () => {
    const { setActivePinia, createPinia } = await import('pinia')
    setActivePinia(createPinia())
    const { useUiStore } = await import('./ui')
    const ui = useUiStore()
    let empujes = 0
    ui.pushNav = () => { empujes++ }
    ui.setTab('lib', 'local')
    expect(ui.tabs.lib).toBe('local')
    expect(empujes).toBe(1)
    ui.setTab('lib', 'local')          // misma pestaña → ni entrada duplicada ni ruido
    expect(empujes).toBe(1)
  })
})
