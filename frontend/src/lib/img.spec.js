import { describe, expect, it } from 'vitest'
import { imgProxy } from './img'

describe('imgProxy · selector de portadas', () => {
  it('cachea también las portadas de MyAnimeList', () => {
    const url = 'https://cdn.myanimelist.net/images/anime/1/12345l.jpg'
    expect(imgProxy(url, 160)).toBe(`/api/img?u=${encodeURIComponent(url)}&w=320`)
  })

  it('no envía una fuente de extensiones arbitraria al proxy', () => {
    const url = 'https://covers.extension.local/series/poster.webp'
    expect(imgProxy(url, 160)).toBe(url)
  })
})
