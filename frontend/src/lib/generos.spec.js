/* El filtro por género comparte ranura con los estados y las etiquetas (`gen:` / `tag:`). Esa
 * convivencia es justo lo que puede romperse en silencio: un prefijo mal leído no da error, sólo
 * enseña la biblioteca entera como si no hubieras filtrado — o la deja vacía sin decir por qué. */
import { describe, it, expect } from 'vitest'
import { opcionesGenero, generoActivo, filtroDeGenero, conGenero, GEN_PREFIX } from './generos'

const BIBLIO = [
  { name: 'A', genres: ['Action', 'Drama'] },
  { name: 'B', genres: ['Drama'] },
  { name: 'C', genres: ['Drama', 'Sci-Fi & Fantasy'] },
  { name: 'D', genres: [] },
  { name: 'E' },                                   // sin el campo: no puede reventar
]

describe('opciones', () => {
  it('cuenta, traduce y ordena por cantidad', () => {
    expect(opcionesGenero(BIBLIO)).toEqual([
      { value: '', label: 'Todos los géneros' },
      { value: 'Drama', label: 'Drama', hint: '3' },
      { value: 'Action', label: 'Acción', hint: '1' },
      { value: 'Sci-Fi & Fantasy', label: 'Ciencia ficción y fantasía', hint: '1' },
    ])
  })

  it('con menos de dos géneros no hay nada que elegir: el control se esconde', () => {
    expect(opcionesGenero([])).toEqual([])
    expect(opcionesGenero([{ genres: ['Drama'] }, { genres: ['Drama'] }])).toEqual([])
  })

  it('el valor guardado es el ORIGINAL, no la traducción', () => {
    // Es la clave con la que hablan AniList y TMDB; traducirla rompería el filtrado.
    expect(opcionesGenero(BIBLIO).map(o => o.value)).toContain('Sci-Fi & Fantasy')
  })
})

describe('ranura de filtro compartida', () => {
  it('ida y vuelta', () => {
    expect(filtroDeGenero('Action')).toBe(`${GEN_PREFIX}Action`)
    expect(generoActivo(filtroDeGenero('Action'))).toBe('Action')
    expect(filtroDeGenero('')).toBe('all')
  })

  it('no confunde un estado ni una etiqueta con un género', () => {
    for (const f of ['all', 'reading', 'tag:favoritos', '', undefined]) {
      expect(generoActivo(f)).toBe('')
    }
  })
})

describe('coincidencia', () => {
  it('es exacta: «Fantasy» no arrastra a «Sci-Fi & Fantasy»', () => {
    expect(conGenero(BIBLIO, 'Fantasy')).toEqual([])
    expect(conGenero(BIBLIO, 'Drama').map(x => x.name)).toEqual(['A', 'B', 'C'])
  })

  it('sin género elegido devuelve la lista entera', () => {
    expect(conGenero(BIBLIO, '')).toHaveLength(5)
  })
})
