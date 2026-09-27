import { describe, it, expect } from 'vitest'
import { currentSeason, isCurrentSeason, shiftSeason, batchInfo, isEpisodePlayable, animeEpLabel, nextEpisodeAfter } from './anime'

describe('shiftSeason', () => {
  it('retrocede y avanza dentro del mismo año', () => {
    expect(shiftSeason({ season: 'SUMMER', year: 2026 }, -1)).toEqual({ season: 'SPRING', year: 2026 })
    expect(shiftSeason({ season: 'SUMMER', year: 2026 }, 1)).toEqual({ season: 'FALL', year: 2026 })
  })

  // EL FALLO que evita: cruzar enero hacia atrás con ifs sobre el nombre deja el año en 2026.
  it('cruza el año en los dos sentidos', () => {
    expect(shiftSeason({ season: 'WINTER', year: 2026 }, -1)).toEqual({ season: 'FALL', year: 2025 })
    expect(shiftSeason({ season: 'FALL', year: 2025 }, 1)).toEqual({ season: 'WINTER', year: 2026 })
  })

  it('encadena saltos: cuatro atrás es el mismo trimestre del año anterior', () => {
    const cs = { season: 'SPRING', year: 2026 }
    expect(shiftSeason(cs, -4)).toEqual({ season: 'SPRING', year: 2025 })
    expect(shiftSeason(shiftSeason(cs, -7), 7)).toEqual(cs)
  })

  it('el filtro sigue al desplazamiento, no a la fecha de hoy', () => {
    const pasada = shiftSeason(currentSeason(new Date('2026-08-04')), -3)  // SUMMER 2026 → FALL 2025
    expect(pasada).toEqual({ season: 'FALL', year: 2025 })
    const a = { season: 'FALL', season_year: 2025 }
    expect(isCurrentSeason(a, pasada)).toBe(true)
    expect(isCurrentSeason(a, currentSeason(new Date('2026-08-04')))).toBe(false)
  })
})


describe('batch granular', () => {
  it('no propaga un batch parcial a episodios sin archivo seleccionado', () => {
    const episodes = [
      { num: 0, in_qbt: true, progress: 50 },
      { num: 1, season: 2, file_index: 8, in_qbt: true, progress: 100 },
      { num: 2, in_qbt: false, progress: 0 },
    ]
    const batch = batchInfo(episodes)
    expect(batch.hasSelection).toBe(true)
    expect(isEpisodePlayable(episodes[1], batch)).toBe(true)
    expect(isEpisodePlayable(episodes[2], batch)).toBe(false)
  })

  it('identifica visualmente la temporada cuando se repite el episodio', () => {
    expect(animeEpLabel({}, { num: 1, season: 2 })).toBe('T2 · Episodio 1')
  })
})

describe('siguiente episodio', () => {
  it('ordena por temporada y omite los que aún no se pueden reproducir', () => {
    const current = { num: 1, season: 2, in_local: true }
    const next = { num: 3, season: 2, in_local: true }
    const episodes = [
      next,
      { num: 2, season: 2, in_local: false },
      current,
      { num: 2, season: 1, in_local: true },
    ]

    expect(nextEpisodeAfter(episodes, current, e => e.in_local)).toBe(next)
  })
})
