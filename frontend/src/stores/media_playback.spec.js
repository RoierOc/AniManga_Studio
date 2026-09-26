/* Tres fallos de Series/Películas que compartían raíz: el módulo REUSA el reproductor y los
 * flujos de anime, pero en tres costuras se desviaba de ellos, y las tres fallaban en silencio.
 *
 *  1. Los subtítulos SIDECAR se añadían con `sub-add` justo tras `loadfile`. `loadfile` es
 *     asíncrono, así que en un motor ya caliente —cambiar de episodio desde el panel de capítulos
 *     o con «siguiente»— el sidecar se pegaba al archivo saliente y moría con él. Abrir desde la
 *     ficha colaba porque crear el motor tarda ~1 s. Nadie lo notaba salvo el usuario, que veía
 *     un episodio sin subtítulos existiendo el fichero.
 *  2. El minuto de reanudación se leía del episodio que trajo Sonarr, no del que acaba de
 *     escribir el reproductor: salir y volver a entrar te devolvía al minuto de la sesión
 *     anterior. Anime no lo sufre porque muta el objeto REAL de su biblioteca.
 *  3. Liberar espacio no quitaba el torrent, así que el hardlink de qBittorrent dejaba los bytes
 *     puestos y Sonarr re-importaba el episodio. Aquí se fija que la UI no cante «liberado»
 *     cuando el torrent ha sobrevivido — decirlo bien es la mitad del arreglo.
 */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { send as nativeSend } from '@/lib/nativeBridge'
import { useMediaStore } from './media'
import { useAnimeStore } from './anime'
import { patchAnimePlaybackPrefs, readAnimePlaybackPrefs, trackIdentity } from '@/lib/animePlaybackPrefs'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn().mockResolvedValue({}), del: vi.fn().mockResolvedValue({}) },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn() }))

// El puente nativo se sustituye por un doble: `send` se espía y `onMessage` guarda el callback
// para poder simular a mano el primer latido de tiempo del archivo nuevo.
let nativeListener = null
vi.mock('@/lib/nativeBridge', () => ({
  isNative: () => true,
  send: vi.fn(),
  onMessage: (fn) => { nativeListener = fn },
}))

if (typeof globalThis.localStorage === 'undefined') {
  const m = new Map()
  globalThis.localStorage = {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k), clear: () => m.clear(),
  }
}

const RESOLVE = {
  ok: true, win_path: 'D:\\TV\\ep.mkv', resume_pos: 0, duration: 1400,
  audio_tracks: [{ index: 0, lang: 'eng' }],
  sub_tracks: [
    { index: 0, lang: 'eng', external: false },
    { index: 1, lang: 'spa', external: true, win_path: 'D:\\TV\\ep.spa.srt' },
  ],
  preferred_sub: 2,
}

const sent = (cmd) => nativeSend.mock.calls.filter(c => c[0] === cmd)

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  localStorage.removeItem('anime-playback-prefs-v1')
  // `playNative` pide de paso el estilo de subtítulos; sin un valor por defecto ese `await`
  // revienta y ensucia la salida con rechazos que no tienen que ver con lo que se prueba.
  api.get.mockResolvedValue({})
  api.post.mockResolvedValue({})
  nativeListener = null
})

describe('preferencias del reproductor nativo por anime', () => {
  it('restaura audio, subtítulo y tier aunque las pistas cambien de orden', async () => {
    const store = useAnimeStore()
    const audio = { lang: 'eng', title: 'English' }
    const subtitle = { lang: 'spa', title: 'Español latino', external: true,
      win_path: 'D:\\TV\\ep.spa.srt' }
    patchAnimePlaybackPrefs(42, {
      audioTrack: trackIdentity(audio), subTrack: trackIdentity(subtitle), tier: 'max_ref_ul_ul',
    })
    api.post.mockResolvedValue({
      ...RESOLVE,
      audio_tracks: [{ lang: 'jpn', title: 'Japanese' }, audio],
      sub_tracks: [{ lang: 'eng', title: 'English' }, subtitle],
      preferred_sub: 1,
    })

    await store.playNative({ id: 42, title: 'Anime' },
      { num: 1, in_local: true, local_path: '/anime/ep.mkv' })

    expect(store.nativePlayer).toMatchObject({ aid: 2, sid: 2, tier: 'max_ref_ul_ul' })
    expect(sent('track')).toContainEqual(['track', { aid: '2' }])
    nativeListener({ event: 'time', pos: 1, duration: 1400, paused: false })
    expect(sent('track').at(-1)).toEqual(['track', { sid: '2' }])
  })

  it('guarda selecciones por anime y no las mezcla con imagen real', () => {
    const store = useAnimeStore()
    store.nativePlayer = {
      animePrefId: 42, isLive: false,
      audioTracks: [{ lang: 'eng', title: 'English' }],
      subTracks: [{ lang: 'spa', title: 'Español' }],
    }

    store.setNativeAudio(0)
    store.setNativeSub(-1)
    store.setNative4kTier('maximo')

    expect(readAnimePlaybackPrefs(42)).toEqual({
      audioTrack: 'embedded:en|english', subTrack: 'off', tier: 'maximo',
    })
    expect(store.native4kTier).toBe('maximo')

    store.nativePlayer = { isLive: true, animePrefId: null,
      audioTracks: [{ lang: 'jpn', title: 'Japanese' }], subTracks: [{ lang: 'eng', title: 'English' }] }
    store.setNativeAudio(0)
    store.setNativeSub(0)
    store.setNative4kTier('live_lite')
    expect(readAnimePlaybackPrefs(42)).toEqual({
      audioTrack: 'embedded:en|english', subTrack: 'off', tier: 'maximo',
    })
    expect(store.nativeLiveTier).toBe('live_lite')
  })
})

describe('los sidecar se cuelgan del archivo NUEVO, no del saliente', () => {
  it('no se envía `subadd` hasta que mpv confirma que el archivo está sonando', async () => {
    const store = useAnimeStore()
    api.post.mockResolvedValue(RESOLVE)

    await store.playNative({ id: null, title: 'The Bear' },
      { num: 5, in_local: true, local_path: '/mnt/d/TV/ep.mkv' }, 0,
      { progressKey: 'series:1:55' })

    // El `loadfile` ya salió; el sidecar NO puede haber salido con él.
    expect(sent('loadfile')).toHaveLength(1)
    expect(sent('subadd')).toHaveLength(0)

    nativeListener({ event: 'time', pos: 1, duration: 1400, paused: false })

    expect(sent('subadd')).toHaveLength(1)
    expect(sent('subadd')[0][1].path).toBe('D:\\TV\\ep.spa.srt')
    // Y la pista se re-fija DESPUÉS de existir: si no, el sid apunta a una pista que aún no está.
    expect(sent('track').at(-1)[1].sid).toBe('2')
  })

  it('se envía UNA vez, no en cada latido', async () => {
    const store = useAnimeStore()
    api.post.mockResolvedValue(RESOLVE)
    await store.playNative({ id: null, title: 'x' },
      { num: 1, in_local: true, local_path: '/a.mkv' }, 0, { progressKey: 'series:1:1' })

    nativeListener({ event: 'time', pos: 1, duration: 1400, paused: false })
    nativeListener({ event: 'time', pos: 2, duration: 1400, paused: false })
    nativeListener({ event: 'time', pos: 3, duration: 1400, paused: false })

    expect(sent('subadd')).toHaveLength(1)
  })

  it('cerrar antes del primer latido no cuelga esos subs del episodio siguiente', async () => {
    const store = useAnimeStore()
    api.post.mockResolvedValue(RESOLVE)
    await store.playNative({ id: null, title: 'x' },
      { num: 1, in_local: true, local_path: '/a.mkv' }, 0, { progressKey: 'series:1:1' })

    store.closeNative()
    nativeListener({ event: 'time', pos: 1, duration: 1400, paused: false })

    expect(sent('subadd')).toHaveLength(0)
  })

  it('restaura el cursor del sistema al cerrar el reproductor nativo', () => {
    const store = useAnimeStore()
    store.nativePlayer = { anime: { id: 'anime-1' }, ep: { num: 1 }, pos: 0, duration: 0 }

    store.closeNative()

    expect(sent('cursor').at(-1)).toEqual(['cursor', { hide: false }])
  })

  it('sin sidecar no se aplaza nada (el camino de anime queda intacto)', async () => {
    const store = useAnimeStore()
    api.post.mockResolvedValue({
      ...RESOLVE,
      sub_tracks: [{ index: 0, lang: 'spa', external: false }],
      preferred_sub: 1,
    })
    await store.playNative({ id: 'a1', title: 'anime' },
      { num: 3, in_local: true, local_path: '/a.mkv' }, 0)

    expect(sent('track')).toHaveLength(1)        // se fija ya: las incrustadas existen al cargar
    nativeListener({ event: 'time', pos: 1, duration: 1400, paused: false })
    expect(sent('subadd')).toHaveLength(0)
  })
})

describe('reanudar usa el minuto que dejó el reproductor, no el que trajo Sonarr', () => {
  const item = { id: 7, title: 'The Bear', poster: '' }
  const eps = [
    { id: 55, num: 5, season: 3, has_file: true, path: '/mnt/d/ep5.mkv', pos: 0, title: 'E5' },
    { id: 56, num: 6, season: 3, has_file: true, path: '/mnt/d/ep6.mkv', pos: 0, title: 'E6' },
  ]

  it('la posición viva gana a la de la API', () => {
    const store = useMediaStore()
    // Acabas de salir del episodio en el minuto 620; la lista de Sonarr sigue diciendo 0.
    store.notePlayback({ key: 'series:7:55', pos: 620, duration: 1400, ended: false })

    const norm = store._epForPlayer(item, eps[0])

    expect(norm.pos).toBe(620)
    // …y el resto de la lista, que no se ha tocado, conserva lo que dijo la API.
    expect(store._epForPlayer(item, eps[1]).pos).toBe(0)
  })

  it('el episodio terminado reanuda desde 0, no desde el último segundo', () => {
    const store = useMediaStore()
    store.notePlayback({ key: 'series:7:55', pos: 1399, duration: 1400, ended: true })

    expect(store._epForPlayer(item, eps[0]).pos).toBe(0)
  })

  it('sin progreso vivo se respeta el de la API (no se pisa con 0)', () => {
    const store = useMediaStore()
    expect(store._epForPlayer(item, { ...eps[0], pos: 300 }).pos).toBe(300)
  })

  it('la lista que recibe el panel del reproductor lleva ya el minuto vivo', () => {
    const store = useMediaStore()
    const anime = useAnimeStore()
    store.notePlayback({ key: 'series:7:56', pos: 90, duration: 1400, ended: false })
    const spy = vi.spyOn(anime, 'playNative').mockImplementation(() => {})

    store.playEpisode(item, eps[0], eps)

    const playlist = spy.mock.calls[0][3].playlist
    expect(playlist.find(e => e.num === 6).pos).toBe(90)
  })
})

describe('liberar espacio no puede cantar victoria si el torrent sigue vivo', () => {
  const item = { id: 7, kind: 'series', title: 'The Bear' }

  it('un torrent que sobrevive se avisa, no se calla', async () => {
    const store = useMediaStore()
    const ui = store.$__ui || null   // el toast se captura por el store de ui real
    void ui
    api.del.mockResolvedValue({ ok: false, deleted: 3, failed: 0, freed: 1e9,
                                torrents: 0, torrents_failed: 2, torrents_error: '' })
    api.get.mockResolvedValue({ series: [], movies: [] })

    const d = await store.freeSpace(item)

    // El contrato con la UI: el fallo del torrent viaja APARTE del de los ficheros. Si se
    // fundieran en un solo booleano, «no se liberó el espacio» se leería como «todo bien».
    expect(d.torrents_failed).toBe(2)
    expect(d.failed).toBe(0)
  })

  it('una película usa su propia ruta (antes no había ninguna)', async () => {
    const store = useMediaStore()
    api.del.mockResolvedValue({ ok: true, deleted: 1, failed: 0, freed: 5e9,
                                torrents: 1, torrents_failed: 0, torrents_error: '' })
    api.get.mockResolvedValue({ series: [], movies: [] })

    await store.freeSpace({ id: 12, kind: 'movie', title: 'Dune' })

    expect(api.del).toHaveBeenCalledWith('/api/media/movie/12/file')
  })
})
