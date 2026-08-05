/* Portada: la única superficie que CRUZA los cinco dominios.
 *
 * Por qué existe
 * ──────────────
 * El sidebar es un archivador (Manga / Anime / Series y Películas / General) y se aterrizaba en
 * `library`, una rejilla de manga. Ninguna vista mezclaba dominios, y las novelas ni siquiera
 * tenían entrada propia. Aquí se junta todo lo empezado en una sola fila ordenada por recencia.
 *
 * NO trae backend nuevo a propósito: los cuatro dominios ya calculan su "continuar" con sus
 * propias reglas, y duplicarlas en un endpoint sería tener dos verdades que se desincronizan.
 *   · anime  → `anime.continueWatching`  (ancla en `last_ep`, ver continue_anchor.spec.js)
 *   · manga  → `manga.recentlyRead()`    (progreso local por obra)
 *   · cine   → `media.continueItems`     (/api/media/continue, necesita Sonarr)
 *   · novela → `novels.progress`         (localStorage; no hay progreso de novelas en servidor)
 *
 * Las marcas de tiempo llegan en unidades distintas (anime y cine en SEGUNDOS epoch, manga y
 * novelas en MILISEGUNDOS de Date.now()). Mezclarlas sin normalizar ponía siempre el manga
 * primero por un factor de 1000, así que todo se normaliza a segundos en `_secs`.
 */
import { defineStore } from 'pinia'
import { useAnimeStore } from './anime'
import { useMangaStore } from './manga'
import { useMediaStore } from './media'
import { useNovelsStore } from './novels'
import { useUiStore } from './ui'
import { api } from '@/lib/api'

/* Cuánto silencio convierte «lo estoy leyendo» en «lo dejé a medias». Tres semanas: por debajo
 * aún es algo que ibas a retomar esta semana, y el riel de abandonadas se llenaba de ruido.
 * Es UNA constante y parte la portada en dos — por eso está aquí arriba y con nombre. */
export const OLVIDO_SECS = 21 * 86400

// Un `Date.now()` (ms) es ~1000x un epoch en segundos. El corte está en el año 2001 en ms.
export function _secs(ts) {
  const n = Number(ts) || 0
  return n > 1e11 ? Math.round(n / 1000) : n
}

/** Segundos → «hace 3 días» / «hoy». Corto: va debajo del título de una tarjeta. */
export function _hace(secs) {
  if (!secs) return ''
  const d = Math.floor(Date.now() / 1000) - secs
  if (d < 3600) return 'hace un momento'
  if (d < 86400) return `hace ${Math.floor(d / 3600)} h`
  const dias = Math.floor(d / 86400)
  if (dias === 1) return 'ayer'
  if (dias < 30) return `hace ${dias} días`
  const meses = Math.floor(dias / 30)
  return meses === 1 ? 'hace un mes' : `hace ${meses} meses`
}

export const useHomeStore = defineStore('home', {
  state: () => ({
    // Índice del hero que se está mostrando (el carrusel rota solo).
    heroIndex: 0,
    _loading: false,
    /* Títulos de manga de la biblioteca ACTIVA.
     *
     * ⚠️ No es una optimización, es un filtro de privacidad. `manga.progress` guarda el progreso
     * de TODAS las obras leídas, incluidas las de la Biblioteca Oculta, y no distingue de cuál
     * es cada una — el que separa las dos raíces es el backend, según el modo activo. Sin este
     * filtro la Portada listaba obras ocultas por su nombre en la fila «Continuar» (detectado en
     * vivo: 6 portadas daban 404 porque el thumb no existe en la raíz normal… pero el TÍTULO ya
     * se había pintado, que es justo lo que la Biblioteca Oculta existe para evitar).
     * `null` = aún no se sabe; hasta entonces no se pinta manga, que es el lado seguro. */
    _mangaVisible: null,

    /* ── Rieles ──────────────────────────────────────────────────────────
     * Cada riel se carga por su cuenta y guarda su propio error. Es la regla de la casa
     * («falló» ≠ «no había») aplicada a una portada: un riel que revienta enseña ErrorState,
     * no desaparece fingiendo que no tenías capítulos nuevos. */
    novedades: [],            // /api/mangadex/updates
    novedadesError: '',
    novedadesCargando: false,

    /* Recordatorio de «lo dejaste a medias»: título → últimas páginas leídas. Se pide al pasar
     * el ratón por la tarjeta, UNA vez por obra, y sólo para manga (es lo único con páginas). */
    recaps: {},
  }),

  getters: {
    /** Anime empezado, en la forma común. */
    _deAnime() {
      return useAnimeStore().continueWatching.map(({ anime, ep }) => ({
        key: `anime:${anime.id}`,
        kind: 'anime',
        title: anime.title,
        // El arte ancho es lo que permite que esto sea un hero y no una ficha.
        art: anime.banner || anime.cover_xl || anime.cover || '',
        poster: anime.cover_xl || anime.cover || '',
        logo: anime.logo || '',
        label: `Episodio ${ep.num}`,
        meta: anime.total_episodes ? `${ep.num} de ${anime.total_episodes}` : '',
        genres: anime.genres || [],
        synopsis: anime.synopsis || '',
        // `resume_pos`/`duration` en segundos → % de ese episodio.
        pct: ep.duration ? Math.min(100, Math.round((ep.resume_pos || 0) / ep.duration * 100)) : 0,
        restante: ep.duration && ep.resume_pos ? Math.round((ep.duration - ep.resume_pos) / 60) : 0,
        ts: _secs(anime.last_watched_at),
        raw: { anime, ep },
      }))
    },

    /* El manga NO necesita cargar nada: `recentlyRead` sale del progreso en localStorage y la
       portada la sirve `/api/library/thumb/<título>` sin pasar por la biblioteca. Por eso la
       Portada pinta manga al instante aunque el resto aún esté llegando. */
    _deManga() {
      const manga = useMangaStore()
      const visibles = this._mangaVisible
      if (!visibles) return []      // aún sin saber qué biblioteca está activa → no se pinta nada
      return manga.recentlyRead(24).filter(it => visibles.has(it.title)).slice(0, 12).map(it => ({
        key: `manga:${it.title}`,
        kind: 'manga',
        title: it.title,
        art: '',                       // el manga no tiene arte ancho: nunca va al hero
        // Sin `?w=`: ese parámetro es el micro-thumb del blur-up (28 px). Aquí se quiere la
        // portada entera — regla de resolución máxima.
        poster: `/api/library/thumb/${encodeURIComponent(it.title)}`,
        label: `Capítulo ${it.lastChapter}`,
        meta: it.total ? `pág. ${it.lastPage + 1} de ${it.total}` : '',
        pct: it.pct,
        ts: _secs(it.ts),
        raw: it,
      }))
    },

    _deCine() {
      return (useMediaStore().continueItems || []).map(it => ({
        key: it.kind === 'movie' ? `cine:mv:${it.movie_id}` : `cine:${it.series_id}:${it.episode_id}`,
        kind: 'cine',
        title: it.title,
        art: it.still || it.banner || '',
        poster: it.poster || '',
        // Una película no tiene temporada ni episodio; forzarla a «T undefined · E undefined» era
        // lo que pasaba al empezar a mezclarlas en el mismo riel.
        label: it.kind === 'movie' ? 'Película' : `T${it.season} · E${it.num}`,
        meta: it.episode_title || '',
        pct: it.duration ? Math.min(100, Math.round((it.pos || 0) / it.duration * 100)) : 0,
        restante: it.duration && it.pos ? Math.round((it.duration - it.pos) / 60) : 0,
        ts: _secs(it.at),
        raw: it,
      }))
    },

    _deNovelas() {
      const novels = useNovelsStore()
      // `saveProgress` no guarda portada, así que se busca en la biblioteca de novelas por id.
      // Si aún no ha cargado, la tarjeta cae al placeholder con la inicial: sin portada, pero
      // presente — que es mejor que desaparecer de la fila.
      const cover = (id) => (novels.library || []).find(n => String(n.id) === String(id))?.cover || ''
      return Object.entries(novels.progress || {})
        .filter(([, p]) => p && p.title)
        .map(([id, p]) => ({
          key: `novela:${id}`,
          kind: 'novela',
          title: p.title,
          art: '',
          poster: cover(id),
          label: p.chapterName || `Capítulo ${(p.chapterIndex || 0) + 1}`,
          meta: p.total ? `${(p.chapterIndex || 0) + 1} de ${p.total}` : '',
          pct: p.total ? Math.min(100, Math.round(((p.chapterIndex || 0) + 1) / p.total * 100)) : 0,
          ts: _secs(p.at),
          raw: { id, ...p },
        }))
    },

    /** La fila «Continuar»: los cuatro dominios en una, por recencia. */
    continueAll() {
      return [...this._deAnime, ...this._deManga, ...this._deCine, ...this._deNovelas]
        .filter(it => it.title && it.ts)
        .sort((a, b) => b.ts - a.ts)
        .slice(0, 16)
    },

    /* El hero exige arte ANCHO: una portada 2:3 estirada a pantalla completa se ve fatal, y es
       justo lo que delata una imagen mal servida. El manga y las novelas no tienen banner, así
       que viven en la fila y no en el hero — no es un descarte, es que no hay arte que poner. */
    heroItems() {
      return this.continueAll.filter(it => it.art).slice(0, 5)
    },

    heroCurrent() {
      const items = this.heroItems
      return items.length ? items[this.heroIndex % items.length] : null
    },

    /* ── Rieles ──────────────────────────────────────────────────────────────────────────
     * Todos devuelven la MISMA forma que `continueAll` (`{key, kind, title, poster, label,
     * meta, pct, ts, raw}`), así que la vista los pinta con el mismo mapeo y el mismo riel.
     * Ninguno trae backend nuevo salvo `novedades`, que ya tenía endpoint. */

    /** Episodios de tu biblioteca que salieron HOY. Sale de `anime.airing`, igual que el calendario. */
    emitidoHoy() {
      const anime = useAnimeStore()
      const hoy = new Date().toDateString()
      const out = []
      for (const a of anime.library || []) {
        const inf = anime.airing?.[a.al_id]
        if (!inf?.last_episode || !inf?.last_aired_at) continue
        if (new Date(inf.last_aired_at * 1000).toDateString() !== hoy) continue
        const ep = (a.episodes || []).find(e => e.num === inf.last_episode)
        // El estado del ARCHIVO, no sólo el número: un riel que te ofrece un episodio que aún
        // no está descargado y no lo dice es un riel que miente.
        const estado = ep?.in_local ? 'listo'
          : ep?.in_qbt ? `descargando ${Math.round(ep.progress || 0)} %`
          : 'sin descargar'
        out.push({
          key: `hoy:${a.id}:${inf.last_episode}`,
          kind: 'anime',
          title: a.title,
          // Riel ancho: aquí manda el fotograma, no la portada.
          art: a.banner || a.cover_xl || a.cover || '',
          poster: a.banner || a.cover_xl || a.cover || '',
          label: `Episodio ${inf.last_episode}`,
          meta: estado,
          pct: 0,
          ts: inf.last_aired_at,
          raw: { anime: a, ep: ep || { num: inf.last_episode } },
        })
      }
      return out.sort((a, b) => b.ts - a.ts).slice(0, 12)
    },

    /** Capítulos nuevos en obras que ya tienes empezadas (`/api/mangadex/updates`). */
    capitulosNuevos() {
      const visibles = this._mangaVisible
      if (!visibles) return []          // mismo filtro de privacidad que `_deManga`
      return (this.novedades || [])
        .filter(u => visibles.has(u.title))
        .map(u => ({
          key: `nuevo:${u.manga_id}`,
          kind: 'manga',
          title: u.title,
          art: '',
          poster: u.cover || `/api/library/thumb/${encodeURIComponent(u.title)}`,
          label: u.new_count === 1 ? `Cap. ${u.new_chapters[0]}` : `+${u.new_count}`,
          meta: u.new_count === 1
            ? `1 capítulo nuevo`
            : `caps. ${u.new_chapters[0]} – ${u.new_chapters[u.new_chapters.length - 1]}`,
          pct: 0,
          ts: 0,
          raw: { title: u.title, ...u },
        }))
        .slice(0, 16)
    },

    /* «Continuar» y «Lo dejaste a medias» son el MISMO material partido por el tiempo, y la
     * partición tiene que ser limpia: una obra que sale en las dos filas convierte la portada en
     * un espejo roto, y además hacía que la segunda no apareciera nunca (lo cazó el test: con
     * pocas obras en curso, `continueAll` se las llevaba todas).
     *
     * Si no has tocado NADA en tres semanas, «Continuar» queda vacío y sólo se pinta el riel de
     * abandonadas. Es lo correcto: no estás continuando nada, estás volviendo. */
    continuarReciente() {
      const ahora = Math.floor(Date.now() / 1000)
      return this.continueAll.filter(it => ahora - it.ts <= OLVIDO_SECS)
    },

    dejadoAMedias() {
      const ahora = Math.floor(Date.now() / 1000)
      return [...this._deAnime, ...this._deManga, ...this._deCine, ...this._deNovelas]
        .filter(it => it.title && it.ts && ahora - it.ts > OLVIDO_SECS)
        .sort((a, b) => b.ts - a.ts)      // lo más reciente primero: es lo más fácil de retomar
        .slice(0, 12)
        .map(it => ({ ...it, key: `medias:${it.key}` }))
    },

    /** ¿Hay algo que enseñar? Distinto de «aún cargando» — ver `loading`. */
    isEmpty() {
      return !this._loading && this.continueAll.length === 0
    },
    loading: (s) => s._loading,
  },

  actions: {
    /* Carga lo que la Portada necesita y aún no esté en memoria.
     *
     * Se aterriza aquí, así que normalmente NINGÚN store está cargado. Van en paralelo y cada
     * uno pinta en cuanto llega: `media.init()` habla con Sonarr y puede tardar o fallar, y la
     * fila no debe esperarlo. Un fallo de Sonarr deja la sección de cine vacía, no la Portada. */
    async init() {
      this._loading = true
      const anime = useAnimeStore(), media = useMediaStore(), novels = useNovelsStore()
      const tareas = [
        anime.library.length ? null : anime.loadLibrary?.(),
        media.continueItems.length ? null : media.init?.(),
        novels.library?.length ? null : novels.loadLibrary?.(),
        this._cargarMangaVisible(),
        // Los rieles nuevos entran en la MISMA tanda paralela: ninguno bloquea a los demás, y
        // el hero + «Continuar» siguen pintando en el primer frame con lo que ya hay en memoria.
        Object.keys(anime.airing || {}).length ? null : anime.loadAiring?.(),
        this.cargarNovedades(),
      ].filter(Boolean)
      // `allSettled`, no `all`: que Sonarr esté caído no puede tumbar la vista entera. Cada
      // dominio pinta cuando llega; manga y novelas ya están (localStorage) desde el primer frame.
      await Promise.allSettled(tareas)
      this._loading = false
    },

    /* Qué obras de manga puede ver el modo de biblioteca ACTIVO. Ver `_mangaVisible`.
     * Si la petición falla no se asume "todas visibles": se deja `null`, o sea sin manga en la
     * fila. Es la aplicación de «falló ≠ no había» al lado que no puede equivocarse. */
    async _cargarMangaVisible() {
      try {
        const obras = (await api.get('/api/library')) || []
        this._mangaVisible = new Set(obras.map(o => o.name || o.id).filter(Boolean))
      } catch {
        this._mangaVisible = null
      }
    },

    /* Capítulos nuevos. El endpoint consulta MangaDex obra por obra (caché de 10 min en el
     * backend), así que puede tardar: por eso va aparte y la portada no lo espera.
     * Un fallo se GUARDA — no se convierte en «no hay novedades», que es la mentira más fácil
     * de contar aquí. */
    async cargarNovedades() {
      this.novedadesCargando = true
      this.novedadesError = ''
      try {
        const d = await api.get('/api/mangadex/updates')
        this.novedades = Array.isArray(d) ? d : []
      } catch (e) {
        this.novedadesError = e?.message || 'No se pudieron consultar los capítulos nuevos'
      } finally {
        this.novedadesCargando = false
      }
    },

    /* Las últimas páginas leídas de una obra, para el recordatorio del riel «lo dejaste a
     * medias». Se pide al pasar el ratón (no al cargar la portada: serían 12 peticiones por
     * nada) y se memoriza por obra, incluido el resultado VACÍO — si no, cada pasada del ratón
     * volvería a pedir lo que ya sabemos que no está. */
    async pedirRecap(item) {
      const t = item?.raw?.title
      if (!t || item.kind !== 'manga' || this.recaps[t] !== undefined) return
      this.recaps[t] = []                       // marca «pedido» antes del await
      try {
        const d = await api.post('/api/reader/read_chapter',
                                 { title: t, chapter: String(item.raw.lastChapter) })
        const pags = Array.isArray(d?.pages) ? d.pages : []
        if (!pags.length) return
        // Las TRES anteriores a donde te quedaste: son las que te devuelven el hilo.
        const i = Math.max(0, Math.min(pags.length, (item.raw.lastPage || 0) + 1))
        this.recaps[t] = pags.slice(Math.max(0, i - 3), i)
      } catch (_) {
        /* sin recordatorio; la tarjeta sigue siendo perfectamente usable */
      }
    },

    nextHero(step = 1) {
      const n = this.heroItems.length
      if (n) this.heroIndex = (this.heroIndex + step + n) % n
    },

    /** Reanuda un elemento en su propio dominio, con las reglas de ese dominio. */
    resume(item) {
      const ui = useUiStore()
      if (item.kind === 'anime') {
        useAnimeStore().play(item.raw.anime, item.raw.ep)
      } else if (item.kind === 'manga') {
        useMangaStore().resumeManga({ id: item.raw.title, name: item.raw.title })
      } else if (item.kind === 'cine') {
        useMediaStore().playContinue(item.raw)
      } else if (item.kind === 'novela') {
        const n = useNovelsStore()
        n.openReader({ id: item.raw.id, title: item.raw.title,
                       novel: { pluginId: item.raw.pluginId, path: item.raw.path } },
                     item.raw.chapterIndex)
        ui.pushNav()
      }
    },

    /* Abre la ficha (no reproduce): el gesto principal de la tarjeta y del hero.
     *
     * ⚠️ `goto` va SIN empujar historial: la ficha empuja el suyo, y con las dos un solo clic
     * dejaba DOS entradas. El primer «atrás» (o el botón lateral del ratón) aterrizaba entonces
     * en Mi Anime / Mi Biblioteca — una sección por la que no habías pasado — en vez de volver al
     * Inicio, que es exactamente lo que parecía "no funciona el atrás". Además esa entrada
     * intermedia se llevaba el sello del scroll, así que la posición del Inicio se perdía. */
    openDetail(item) {
      const ui = useUiStore()
      if (item.kind === 'anime') {
        ui.goto('anime', { push: false }); useAnimeStore().openDetail(item.raw.anime)
      } else if (item.kind === 'manga') {
        useMangaStore().open({ id: item.raw.title, name: item.raw.title })
      } else if (item.kind === 'cine') {
        const media = useMediaStore()
        // Hay que pasar el ítem REAL de la biblioteca, no uno reconstruido a mano: la ficha lee
        // de él campos (temporadas, rutas, imágenes) que el registro de progreso no tiene.
        const real = (media.all || []).find(x => x.kind === 'series' && x.id === item.raw.series_id)
        if (real) { ui.goto('media', { push: false }); media.openDetail(real) }
      } else if (item.kind === 'novela') {
        useNovelsStore().openDetail({ id: item.raw.id, title: item.raw.title,
                                      novel: { pluginId: item.raw.pluginId, path: item.raw.path } })
      }
    },
  },
})
