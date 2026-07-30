import { describe, it, expect } from 'vitest'
import { parseRelease, matchLibrary } from './anime'

describe('parseRelease', () => {
  it('saca serie y episodio de un SxxExx', () => {
    const r = parseRelease('[ToonsHub] Chuhai Lips Canned Flavor of Married Women S01E03 1080p UNCENSORED OV WEB-DL AAC2.0 H.264 (Hitozuma no Kuchibiru wa Kan Chu-Hi no Aji)')
    expect(r.title).toBe('Chuhai Lips Canned Flavor of Married Women')
    expect(r.season).toBe(1); expect(r.episode).toBe(3); expect(r.quality).toBe('1080p')
  })
  it('entiende el guion de SubsPlease', () => {
    const r = parseRelease('[SubsPlease] Sakamoto Days - 05 (1080p) [A1B2C3D4].mkv')
    expect(r.title).toBe('Sakamoto Days'); expect(r.episode).toBe(5)
  })
  it('marca las tandas de temporada como batch, sin episodio inventado', () => {
    const r = parseRelease('[Judas] Zankyou no Terror (Terror in Resonance) (Season 1) [BD 1080p][HEVC x265 10bit][Dual-Audio][Eng-Subs]')
    expect(r.title).toBe('Zankyou no Terror')
    expect(r.season).toBe(1); expect(r.episode).toBe(null); expect(r.batch).toBe(true)
  })
  it('no confunde el año con un episodio', () => {
    const r = parseRelease('[EMBER] Chained Soldier (2024) (Season 1) (Uncensored) [BDRip] [1080p Dual Audio HEVC 10 bits DDP] (Mato Seihei no Slave) (Batch)')
    expect(r.title).toBe('Chained Soldier'); expect(r.episode).toBe(null); expect(r.batch).toBe(true)
  })
  it('nunca devuelve título vacío: cae al crudo', () => {
    expect(parseRelease('[Grupo] S01E01 1080p').title).toBe('[Grupo] S01E01 1080p')
  })
})

describe('matchLibrary', () => {
  const lib = [{ title: 'Sakamoto Days', cover: 'a' }, { title: 'Frieren', title_romaji: 'Sousou no Frieren', cover: 'b' }]
  it('empareja por romaji', () => expect(matchLibrary('Sousou no Frieren', lib)?.cover).toBe('b'))
  it('no inventa: sin coincidencia devuelve null', () => expect(matchLibrary('Otra cosa', lib)).toBe(null))
})
