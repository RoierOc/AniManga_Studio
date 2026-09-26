import { describe, expect, it } from 'vitest'
import { animeThumb, imgProxy, imgThumb, setImageRevisions } from './img'

const cover = 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx42-AbCd.jpg'

describe('versionado de imágenes Anime', () => {
  it('añade la revisión solo a la URL de proxy, sin cambiar la URL de origen', () => {
    expect(imgProxy(cover, 160, 7)).toBe(`/api/img?u=${encodeURIComponent(cover)}&w=320&v=7`)
    expect(imgProxy(cover)).toBe(`/api/img?u=${encodeURIComponent(cover)}`)
  })

  it('versiona también el blur-up para que no se quede con la portada anterior', () => {
    expect(imgThumb(cover, 28, 7)).toBe(`/api/img?u=${encodeURIComponent(cover)}&w=28&v=7`)
  })

  it('aplica la revisión de la biblioteca a cualquier consumidor de esa portada', () => {
    setImageRevisions([{ id: '42', cover, cover_rev: 9 }])
    expect(imgProxy(cover)).toBe(`/api/img?u=${encodeURIComponent(cover)}&v=9`)
    expect(imgThumb(cover)).toBe(`/api/img?u=${encodeURIComponent(cover)}&w=28&v=9`)
    expect(animeThumb('42', 1, 'episode', 's01e001')).toBe('/api/anime/thumb/42/1?v=960&episode_key=s01e001&r=9')
    setImageRevisions([])
  })
})

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
