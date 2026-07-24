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
