/* La nota de una tarjeta de MangaDex venía en DOS formas distintas y una de ellas pintaba `★ NaN`
 * en todas las tarjetas de la vista: `/api/anilist/scores` devuelve un objeto por id
 * (`{score, genres, popularity}`) mientras que la búsqueda guarda el número pelado. El componente
 * hace `(score / 10).toFixed(1)`, así que el objeto salía como NaN — y `v-if="score"` no lo filtra
 * porque un objeto es truthy.
 *
 * Correr:  cd frontend && pnpm test
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useMangadexStore } from '@/stores/mangadex'

describe('mangadex · score()', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('acepta la forma del endpoint (objeto) y devuelve el número', () => {
    const s = useMangadexStore()
    s.scores = { 1706: { al_id: 31706, score: 92, genres: ['Action'], popularity: 103218 } }
    expect(s.score({ mal_id: 1706 })).toBe(92)
  })

  it('acepta la forma de la búsqueda (número pelado)', () => {
    const s = useMangadexStore()
    s.scores = { 13: 91 }
    expect(s.score({ mal_id: 13 })).toBe(91)
  })

  it('nunca devuelve algo que se pinte como NaN', () => {
    const s = useMangadexStore()
    s.scores = { 1: {}, 2: null, 3: 'ochenta', 4: NaN }
    for (const id of [1, 2, 3, 4]) {
      const v = s.score({ mal_id: id })
      expect(v).toBeNull()
      expect(String((v / 10).toFixed?.(1) ?? '')).not.toBe('NaN')
    }
  })

  it('sin mal_id no hay nota', () => {
    const s = useMangadexStore()
    expect(s.score({})).toBeNull()
  })
})
