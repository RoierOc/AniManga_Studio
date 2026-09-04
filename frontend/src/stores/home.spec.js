// @vitest-environment happy-dom
/* La Portada mezcla CUATRO dominios en una fila. Lo que se puede romper en silencio es el
 * orden, porque las marcas de tiempo llegan en unidades distintas: anime y cine en segundos
 * epoch, manga y novelas en milisegundos de `Date.now()`. Sin normalizar, cualquier manga leído
 * hace un año gana a un episodio visto hace un minuto por un factor de 1000 — y la fila parece
 * "aleatoria" sin que nada falle a la vista.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

import { useHomeStore, _secs, _hace } from './home'
import { useAnimeStore } from './anime'
import { useMangaStore } from './manga'
import { useMediaStore } from './media'
import { useNovelsStore } from './novels'

const AHORA = Math.floor(Date.now() / 1000)
/* Mediodía de HOY en hora local. «Hace una hora» parece un instante de hoy, pero a las 00:30 cae en
   AYER y el test de «Emitido hoy» fallaba solo, una vez al día, sin que nada se hubiera roto.
   El caso que se quiere probar es «el mismo día de calendario», así que se escribe ASÍ. */
const HOY_MEDIODIA = (() => { const d = new Date(); d.setHours(12, 0, 0, 0); return Math.floor(d / 1000) })()

beforeEach(() => {
  // Los stores de anime/manga/novelas leen localStorage al construirse, sin guarda.
  if (!globalThis.localStorage) {
    globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  }
  setActivePinia(createPinia())
})

/* El manga sólo se pinta cuando ya se sabe qué obras ve la biblioteca ACTIVA (filtro de la
   Biblioteca Oculta, ver `_mangaVisible`). En los tests se declara a mano lo que es visible. */
function mangaVisible(home, ...titulos) {
  home._mangaVisible = new Set(titulos)
}

/** Un anime con un episodio a medias, en la forma que devuelve /api/anime/library. */
function anime({ id = 'a', title = 'Serie', at = AHORA, banner = 'https://image.tmdb.org/t/p/original/b.jpg' } = {}) {
  return {
    id, title, banner, cover: 'c.jpg', last_watched_at: at, last_ep: 4, total_episodes: 12,
    episodes: [
      { num: 3, ep_type: 'episode', in_local: true, watched: true, resume_pos: 0, duration: 1400 },
      { num: 4, ep_type: 'episode', in_local: true, watched: false, resume_pos: 700, duration: 1400 },
    ],
  }
}

describe('la fila «Continuar» cruza los dominios', () => {
  it('EL FALLO: ms y segundos mezclados ponían el manga siempre primero', () => {
    const home = useHomeStore()
    mangaVisible(home, 'Obra')
    useAnimeStore().library = [anime({ at: AHORA })]              // visto HACE UN MINUTO (segundos)
    // Leído hace un año, pero en MILISEGUNDOS: el número es ~1000x mayor.
    useMangaStore().progress = {
      'Obra': { lastChapter: '5', lastPage: 2, lastTotal: 20, ts: (AHORA - 365 * 86400) * 1000 },
    }
    const orden = home.continueAll.map(i => i.kind)
    expect(orden[0]).toBe('anime')
  })

  it('junta los cuatro dominios y ordena por recencia real', () => {
    const home = useHomeStore()
    mangaVisible(home, 'Obra')
    useAnimeStore().library = [anime({ at: AHORA - 300 })]
    useMangaStore().progress = { 'Obra': { lastChapter: '5', lastPage: 1, lastTotal: 10, ts: (AHORA - 100) * 1000 } }
    useMediaStore().continueItems = [
      { series_id: 1, episode_id: 9, title: 'Peli', season: 1, num: 9, pos: 300, duration: 3000, at: AHORA - 200 },
    ]
    useNovelsStore().progress = {
      n1: { title: 'Novela', chapterIndex: 3, total: 40, at: (AHORA - 50) * 1000 },
    }
    expect(home.continueAll.map(i => i.kind)).toEqual(['novela', 'manga', 'cine', 'anime'])
  })

  /* La Biblioteca Oculta existe para que ciertas obras no aparezcan. `manga.progress` guarda el
     progreso de TODAS y no distingue de qué raíz es cada una — el que separa las dos es el
     backend. Sin filtro, la Portada listaba obras ocultas POR SU NOMBRE en la fila (detectado en
     vivo: sus portadas daban 404, pero el título ya se había pintado). */
  it('PRIVACIDAD: una obra que no está en la biblioteca activa no sale', () => {
    const home = useHomeStore()
    mangaVisible(home, 'Visible')
    useMangaStore().progress = {
      'Visible': { lastChapter: '1', lastPage: 0, lastTotal: 10, ts: AHORA * 1000 },
      'Oculta':  { lastChapter: '9', lastPage: 0, lastTotal: 10, ts: AHORA * 1000 },
    }
    expect(home.continueAll.map(i => i.title)).toEqual(['Visible'])
  })

  it('PRIVACIDAD: mientras no se sepa qué biblioteca está activa, no se pinta manga', () => {
    // `null` = aún sin respuesta, o la petición falló. Asumir "todas visibles" filtraría al
    // revés y enseñaría justo lo que no debe: el lado seguro es no enseñar nada.
    const home = useHomeStore()
    useMangaStore().progress = { 'Obra': { lastChapter: '1', lastPage: 0, lastTotal: 10, ts: AHORA * 1000 } }
    expect(home._mangaVisible).toBe(null)
    expect(home.continueAll).toEqual([])
  })

  it('descarta lo que no tiene título o fecha en vez de pintar tarjetas huecas', () => {
    const home = useHomeStore()
    useNovelsStore().progress = { n1: { title: '', at: AHORA * 1000 }, n2: { title: 'Buena', at: 0 } }
    expect(home.continueAll).toEqual([])
  })
})

describe('el hero exige arte ancho', () => {
  it('el manga y las novelas NO van al hero: no tienen banner que estirar', () => {
    const home = useHomeStore()
    mangaVisible(home, 'Obra')
    useMangaStore().progress = { 'Obra': { lastChapter: '5', lastPage: 1, lastTotal: 10, ts: AHORA * 1000 } }
    useNovelsStore().progress = { n1: { title: 'Novela', chapterIndex: 1, total: 9, at: AHORA * 1000 } }
    expect(home.continueAll.length).toBe(2)
    expect(home.heroItems).toEqual([])       // hay fila, no hay hero
  })

  it('un anime con banner sí manda el hero', () => {
    const home = useHomeStore()
    useAnimeStore().library = [anime()]
    expect(home.heroItems.length).toBe(1)
    expect(home.heroCurrent.title).toBe('Serie')
  })

  it('el carrusel da la vuelta sin salirse del array', () => {
    const home = useHomeStore()
    useAnimeStore().library = [anime({ id: 'a', at: AHORA }), anime({ id: 'b', title: 'Otra', at: AHORA - 10 })]
    expect(home.heroItems.length).toBe(2)
    home.nextHero(1); expect(home.heroIndex).toBe(1)
    home.nextHero(1); expect(home.heroIndex).toBe(0)
    home.nextHero(-1); expect(home.heroIndex).toBe(1)
  })
})

describe('progreso y etiquetas', () => {
  it('el % del episodio sale de la posición sobre la duración', () => {
    const home = useHomeStore()
    useAnimeStore().library = [anime()]           // 700 de 1400
    expect(home.continueAll[0].pct).toBe(50)
    expect(home.continueAll[0].restante).toBe(12) // (1400-700)/60
  })

  it('sin duración conocida no se inventa un porcentaje', () => {
    const home = useHomeStore()
    const a = anime()
    a.episodes[1].duration = 0
    useAnimeStore().library = [a]
    expect(home.continueAll[0].pct).toBe(0)
  })
})

describe('_secs normaliza las dos unidades', () => {
  it('deja los segundos como están y divide los milisegundos', () => {
    expect(_secs(AHORA)).toBe(AHORA)
    expect(_secs(AHORA * 1000)).toBe(AHORA)
    expect(_secs(0)).toBe(0)
    expect(_secs(undefined)).toBe(0)
  })
})

describe('_hace', () => {
  it('da texto corto y humano', () => {
    expect(_hace(0)).toBe('')
    expect(_hace(AHORA - 60)).toBe('hace un momento')
    expect(_hace(AHORA - 7200)).toBe('hace 2 h')
    expect(_hace(AHORA - 86400 * 1.2)).toBe('ayer')
    expect(_hace(AHORA - 86400 * 5)).toBe('hace 5 días')
    expect(_hace(AHORA - 86400 * 60)).toBe('hace 2 meses')
  })
})

describe('init no se cae si un dominio falla', () => {
  it('Sonarr caído deja la Portada en pie con el resto', async () => {
    const home = useHomeStore()
    const media = useMediaStore()
    const anime = useAnimeStore()
    vi.spyOn(media, 'init').mockRejectedValue(new Error('Sonarr no responde'))
    vi.spyOn(anime, 'loadLibrary').mockResolvedValue(undefined)
    vi.spyOn(useNovelsStore(), 'loadLibrary').mockResolvedValue(undefined)
    // Sin esto la petición real de `/api/library` se queda colgada en happy-dom (no hay
    // servidor) y el test expira a los 5 s en vez de comprobar lo que pretende.
    vi.spyOn(home, '_cargarMangaVisible').mockResolvedValue(undefined)
    vi.spyOn(home, 'cargarNovedades').mockResolvedValue(undefined)
    vi.spyOn(anime, 'loadAiring').mockResolvedValue(undefined)
    await expect(home.init()).resolves.toBeUndefined()
    expect(home.loading).toBe(false)
  })

  it('si no hay contenido y una carga falla, la Portada expone el error', async () => {
    const home = useHomeStore()
    const media = useMediaStore()
    const animeStore = useAnimeStore()
    vi.spyOn(animeStore, 'loadLibrary').mockImplementation(async () => {
      animeStore.loadError = 'AniList no responde'
    })
    vi.spyOn(media, 'init').mockResolvedValue(undefined)
    vi.spyOn(useNovelsStore(), 'loadLibrary').mockResolvedValue(undefined)
    vi.spyOn(home, '_cargarMangaVisible').mockResolvedValue(undefined)
    vi.spyOn(home, 'cargarNovedades').mockResolvedValue(undefined)
    vi.spyOn(animeStore, 'loadAiring').mockResolvedValue(undefined)

    await home.init()

    expect(home.loadError).toBe('AniList no responde')
  })

  it('si queda contenido válido, un fallo de otro dominio no tapa la Portada', async () => {
    const home = useHomeStore()
    const media = useMediaStore()
    const animeStore = useAnimeStore()
    animeStore.library = [anime()]
    vi.spyOn(media, 'init').mockImplementation(async () => {
      media.loadError = 'Sonarr no responde'
    })
    vi.spyOn(useNovelsStore(), 'loadLibrary').mockResolvedValue(undefined)
    vi.spyOn(home, '_cargarMangaVisible').mockResolvedValue(undefined)
    vi.spyOn(home, 'cargarNovedades').mockResolvedValue(undefined)
    vi.spyOn(animeStore, 'loadAiring').mockResolvedValue(undefined)

    await home.init()

    expect(home.continueAll.length).toBeGreaterThan(0)
    expect(home.loadError).toBe('')
  })

  it('no recicla un error viejo de Cine cuando su biblioteca ya cargó correctamente', async () => {
    const home = useHomeStore()
    const media = useMediaStore()
    const animeStore = useAnimeStore()
    media.loaded = true
    media.loadError = 'fallo anterior'
    vi.spyOn(animeStore, 'loadLibrary').mockResolvedValue(undefined)
    vi.spyOn(media, 'init').mockResolvedValue(undefined)
    vi.spyOn(useNovelsStore(), 'loadLibrary').mockResolvedValue(undefined)
    vi.spyOn(home, '_cargarMangaVisible').mockResolvedValue(undefined)
    vi.spyOn(home, 'cargarNovedades').mockResolvedValue(undefined)
    vi.spyOn(animeStore, 'loadAiring').mockResolvedValue(undefined)

    await home.init()

    expect(home.loadError).toBe('')
  })
})

/* ── Rieles (agosto 2026) ────────────────────────────────────────────────────────────────
 * Lo que puede romperse en silencio aquí no es el pintado, son los CORTES: qué entra en cada
 * riel y qué no. Un riel mal filtrado no da error — enseña la obra equivocada, o la misma dos
 * veces, y eso sólo se ve mirando la portada con datos reales.
 */
describe('riel «Lo dejaste a medias»', () => {
  const DIA = 86400

  it('sólo entra lo abandonado: 21 días es el corte', () => {
    const home = useHomeStore()
    mangaVisible(home, 'Vieja', 'Reciente')
    useMangaStore().progress = {
      Vieja:    { lastChapter: '10', lastPage: 3, lastTotal: 20, ts: (AHORA - 30 * DIA) * 1000 },
      Reciente: { lastChapter: '4',  lastPage: 1, lastTotal: 20, ts: (AHORA - 2 * DIA) * 1000 },
    }
    expect(home.dejadoAMedias.map(it => it.title)).toEqual(['Vieja'])
    // …y la partición es limpia: lo viejo YA NO cuenta como «continuar».
    expect(home.continuarReciente.map(it => it.title)).toEqual(['Reciente'])
  })

  it('EL FALLO: una obra no puede salir en las DOS filas', () => {
    const home = useHomeStore()
    mangaVisible(home, 'Vieja')
    useMangaStore().progress = {
      Vieja: { lastChapter: '10', lastPage: 3, lastTotal: 20, ts: (AHORA - 30 * DIA) * 1000 },
    }
    // Antes «Continuar» se llevaba TODO lo empezado sin mirar la fecha, así que con pocas obras
    // el riel de abandonadas no aparecía nunca y la fila de arriba decía que seguías leyendo
    // algo que llevabas un mes sin tocar.
    expect(home.dejadoAMedias.map(it => it.title)).toEqual(['Vieja'])
    expect(home.continuarReciente).toHaveLength(0)
    // Pero la obra SIGUE en el fondo común: la Portada no está vacía ni pierde su hero.
    expect(home.continueAll).toHaveLength(1)
    expect(home.isEmpty).toBe(false)
  })

  it('respeta la Biblioteca Oculta: sin saber qué es visible, no pinta manga', () => {
    const home = useHomeStore()
    home._mangaVisible = null
    useMangaStore().progress = {
      Secreta: { lastChapter: '3', lastPage: 1, lastTotal: 10, ts: (AHORA - 60 * DIA) * 1000 },
    }
    expect(home.dejadoAMedias).toHaveLength(0)
  })
})

describe('riel «Emitido hoy»', () => {
  it('sólo el episodio de HOY, y dice si el archivo está o no', () => {
    const home = useHomeStore()
    const a = useAnimeStore()
    a.library = [
      { id: 'x', al_id: 1, title: 'Hoy', cover: 'c.jpg', episodes: [{ num: 5, in_local: true }] },
      { id: 'y', al_id: 2, title: 'Ayer', cover: 'c.jpg', episodes: [{ num: 9, in_local: true }] },
    ]
    a.airing = {
      1: { last_episode: 5, last_aired_at: Math.min(HOY_MEDIODIA, AHORA - 60) },
      2: { last_episode: 9, last_aired_at: AHORA - 3 * 86400 },
    }
    expect(home.emitidoHoy.map(it => it.title)).toEqual(['Hoy'])
    expect(home.emitidoHoy[0].meta).toBe('listo')
  })

  it('un episodio que aún no está descargado lo DICE (no se ofrece como si estuviera)', () => {
    const home = useHomeStore()
    const a = useAnimeStore()
    a.library = [{ id: 'x', al_id: 1, title: 'Hoy', cover: 'c.jpg', episodes: [] }]
    a.airing = { 1: { last_episode: 5, last_aired_at: Math.min(HOY_MEDIODIA, AHORA - 60) } }
    expect(home.emitidoHoy[0].meta).toBe('sin descargar')
  })
})
