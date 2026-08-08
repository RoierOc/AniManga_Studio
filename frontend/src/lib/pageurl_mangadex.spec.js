/* Una página de MangaDex@Home NUNCA puede llegar al `<img>` como URL del CDN: el navegador no
 * puede reintentar el 404 de un nodo frío ni pedirla más pequeña, y la página se queda en blanco.
 * Es un fallo silencioso: la lista de páginas llega entera, el lector pinta los huecos, y sólo se
 * nota mirando. Hay DOS caminos que entregan estas URLs (`/chapter/<id>/pages`, ya proxyado, y
 * `/transplant/chapter_urls`, crudo) — por eso el guardia vive en `pageUrl`, que es por donde
 * pasa todo lo que el lector pinta. */
import { describe, it, expect } from 'vitest'
import { pageUrl } from './manga'

const CDN = 'https://cmdxd98sb0x3yprd.mangadex.network/data/c48a/x1-2108.png'

describe('pageUrl con páginas de MangaDex@Home', () => {
  it('las manda por el proxy y le pasa el ancho', () => {
    expect(pageUrl(CDN, 1024)).toBe(`/api/mangadex/page_proxy?u=${encodeURIComponent(CDN)}&w=1024`)
  })

  it('sin ancho (zoom / ajuste original) pide el original, pero sigue por el proxy', () => {
    expect(pageUrl(CDN)).toBe(`/api/mangadex/page_proxy?u=${encodeURIComponent(CDN)}`)
  })

  it('no toca las URLs de otras fuentes ni las locales', () => {
    const suwa = 'http://localhost:4567/api/v1/manga/1/chapter/2/page/0'
    expect(pageUrl(suwa, 1024)).toBe(suwa)
    expect(pageUrl('Obra/Cap 1/ch0001_001.jpg', 1024)).toBe('/uploads/Obra/Cap%201/ch0001_001.jpg?w=1024')
  })
})
