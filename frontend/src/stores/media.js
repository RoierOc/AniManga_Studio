/* Store de Series y Películas (Sonarr/Radarr).
 *
 * Existe por el mismo motivo que el de anime: hasta ahora todo este estado vivía DENTRO de
 * `MediaLibrary.vue`, así que cambiar de pestaña lo perdía (biblioteca recargada, búsqueda
 * borrada, filtros olvidados). Aquí sobrevive a la navegación y las vistas quedan tontas.
 *
 * Sin cola propia: las descargas se ven en la vista Descargas (General), que lista los torrents
 * de qBittorrent — el mismo cliente para anime, series y películas.
 */
import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'
import { formatBytes } from '@/lib/format'
import { imgProxy } from '@/lib/img'

const SUB_KEY = 'media-sub'

// Con guarda como en `ui.js`: fuera del navegador (tests) no hay localStorage, y un store que
// revienta al construirse tumba la vista entera.
function _readSub() {
  try { return localStorage.getItem(SUB_KEY) || 'library' } catch { return 'library' }
}
function _writeSub(v) {
  try { localStorage.setItem(SUB_KEY, v) } catch { /* sin almacenamiento: no es un fallo */ }
}

export const useMediaStore = defineStore('media', {
  state: () => ({
    sub: _readSub(),
    status: {},
    series: [],
    movies: [],
    errors: {},          // {sonarr|radarr: mensaje} — "falló" viaja aparte del array vacío
    loading: false,
    loaded: false,

    detail: null,        // serie/película abierta
    picker: null,        // selector de torrent abierto (ver ReleasePicker)
    continueItems: [],
    // Progreso EN VIVO por clave (`series:<id>:<epId>` | `movie:<id>`), escrito por el
    // reproductor mientras reproduce. Pisa lo que trajo Sonarr hasta la siguiente recarga.
    progressByKey: {},

    // filtros de la biblioteca
    filter: 'all',       // all | series | movies | missing
    sort: 'recent',      // recent | title | progress
    search: '',

    // alta
    results: null,       // null = sin buscar; [] = buscado y vacío
    searchErr: '',
    searching: false,
    adding: '',

    // descubrir
    discover: [],
    discoverList: 'trending',
    discoverKind: 'series',
    discoverErr: '',
    discoverSkipped: 0,   // animes apartados: se dice, para que la lista corta no confunda
    discoverLoading: false,
    discoverPage: 1,
    discoverTotalPages: 1,
    discoverLoadingMore: false,
    discoverGenre: '',        // id de género TMDB, '' = todos
    genres: { series: [], movie: [] },
  }),

  getters: {
    // Un servicio caído se DICE; nunca se disfraza de biblioteca vacía.
    offline: (s) => Object.entries(s.status).filter(([, v]) => v && !v.online).map(([k]) => k),

    all: (s) => [...s.series, ...s.movies],
    discoverHasMore: (s) => s.discoverPage < s.discoverTotalPages,

    items(s) {
      let list = this.all
      if (s.filter === 'series') list = s.series
      else if (s.filter === 'movies') list = s.movies
      else if (s.filter === 'missing') list = list.filter(x => x.have < x.total)

      const q = s.search.trim().toLowerCase()
      if (q) list = list.filter(x => (x.title || '').toLowerCase().includes(q))

      return [...list].sort((a, b) => {
        if (s.sort === 'title') return (a.title || '').localeCompare(b.title || '')
        if (s.sort === 'progress') {
          return (b.have / (b.total || 1)) - (a.have / (a.total || 1))
        }
        return (b.id || 0) - (a.id || 0)   // el id de Sonarr crece: id mayor = añadido después
      })
    },

    counts(s) {
      return {
        all: s.series.length + s.movies.length,
        series: s.series.length,
        movies: s.movies.length,
        missing: this.all.filter(x => x.have < x.total).length,
      }
    },

    // Estadísticas del modo Cine — el equivalente al panel de anime, pero calculado en cliente con
    // lo que ya trae la biblioteca (have/total/size/seasons de Sonarr/Radarr): ni un endpoint nuevo.
    stats(s) {
      const sum = (arr, f) => arr.reduce((n, x) => n + (f(x) || 0), 0)
      const epsHave = sum(s.series, x => x.have)
      const epsTotal = sum(s.series, x => x.total)
      return {
        series: s.series.length,
        movies: s.movies.length,
        seasons: sum(s.series, x => x.seasons),
        epsHave,
        epsMissing: Math.max(0, epsTotal - epsHave),
        moviesHave: sum(s.movies, x => x.have),   // have∈{0,1} en película
        watching: s.continueItems.length,
        sizeSeries: sum(s.series, x => x.size),
        sizeMovies: sum(s.movies, x => x.size),
        sizeTotal: sum(this.all, x => x.size),
      }
    },

    // Destacados del hero: lo que tiene arte ancho, priorizando lo que estás viendo.
    heroItems(s) {
      const started = new Set(s.continueItems.map(c => c.series_id))
      const withArt = this.all.filter(x => x.banner)
      const pick = [
        ...withArt.filter(x => x.kind === 'series' && started.has(x.id)),
        ...withArt.filter(x => !(x.kind === 'series' && started.has(x.id))),
      ].slice(0, 5)
      return pick.map(x => ({
        id: `${x.kind}:${x.id}`,
        raw: x,
        art: x.banner,
        artFallback: x.poster,
        overline: started.has(x.id) ? 'SIGUE VIENDO' : (x.kind === 'movie' ? 'PELÍCULA' : 'EN TU BIBLIOTECA'),
        title: x.title,
        meta: [
          x.have < x.total ? `${x.have} de ${x.total} episodios` : (x.kind === 'movie' ? 'Película' : 'Completa'),
          x.year ? String(x.year) : '',
          x.status || '',
        ].filter(Boolean),
      }))
    },
  },

  actions: {
    setSub(v) { this.sub = v; _writeSub(v) },

    async init() {
      if (!this.loaded) await this.load()
      this.loadContinue()
    },

    async load() {
      this.loading = true
      try {
        const [st, lib] = await Promise.all([
          api.get('/api/media/status'),
          api.get('/api/media/library'),
        ])
        this.status = st
        this.series = lib.series || []
        this.movies = lib.movies || []
        this.errors = lib.errors || {}
        this.loaded = true
      } finally { this.loading = false }
    },

    async loadContinue() {
      try { this.continueItems = (await api.get('/api/media/continue')).items || [] } catch { /* no crítico */ }
    },

    /* Quita de la biblioteca. Borrar los ficheros es una decisión APARTE y explícita: dejar de
     * seguir una serie y liberar 40 GB no son lo mismo, y solo una de las dos es reversible. */
    async removeFromLibrary(item, { deleteFiles = false } = {}) {
      const ui = useUiStore()
      try {
        await api.del(`/api/media/library/${item.kind}/${item.id}`, { body: { delete_files: deleteFiles } })
        ui.toast(deleteFiles ? `${item.title} y sus archivos eliminados` : `${item.title} quitada de la biblioteca`, 'ok')
        if (this.detail?.id === item.id && this.detail?.kind === item.kind) this.closeDetail()
        await this.load()
        this.loadContinue()
      } catch (e) {
        ui.toast(`No se pudo quitar: ${e?.body || e?.message || ''}`, 'error')
      }
    },

    openDetail(item) { this.detail = item },

    /* Abre el selector de torrents directamente desde la tarjeta, como el panel de anime:
     * en una película busca la película; en una serie, la PRIMERA temporada incompleta —
     * que es casi siempre lo que quieres bajar, y evita un clic extra para elegirla. */
    openPicker(item) {
      this.picker = item.kind === 'movie'
        ? { kind: 'movie', id: item.id, label: item.title, query: `${item.title} ${item.year || ''}`.trim() }
        : { kind: 'season', id: item.id, season: 1, label: `${item.title} · Temporada 1`,
            query: `${item.title} S01` }
    },
    closePicker() { this.picker = null },
    closeDetail() { this.detail = null },

    // OJO con el nombre: si la acción se llama `search` IGUAL que el campo de estado, Pinia deja
    // la acción encima y `store.search` pasa a ser una función → `search.trim()` revienta la
    // biblioteca entera con "s.search.trim is not a function". Estado y acción, nombres distintos.
    async runSearch(term) {
      const q = (term ?? this.search).trim()
      if (!q) { this.results = null; return }
      this.searching = true; this.searchErr = ''
      try {
        const kind = this.filter === 'movies' ? 'movie' : 'series'
        this.results = (await api.get(`/api/media/search?q=${encodeURIComponent(q)}&kind=${kind}`)).results || []
      } catch (e) {
        // Un fallo NO se pinta como "sin resultados": confundirlos hace creer que la serie no
        // existe cuando lo que pasa es que Sonarr está caído.
        this.searchErr = e?.body || e?.message || 'no se pudo buscar'
        this.results = null
      } finally { this.searching = false }
    },

    async add(extId, kind) {
      const ui = useUiStore()
      this.adding = extId
      try {
        const d = await api.post('/api/media/add', { kind, id: extId })
        ui.toast(d.already ? `${d.title} ya estaba en tu biblioteca` : `${d.title} añadida ✓`, 'ok')
        await this.load()
        return true
      } catch (e) {
        ui.toast(`No se pudo añadir: ${e?.body || e?.message || ''}`, 'error')
        return false
      } finally { this.adding = '' }
    },

    _discoverUrl(page) {
      const g = this.discoverGenre ? `&genre=${this.discoverGenre}` : ''
      return `/api/media/discover?kind=${this.discoverKind}&list=${this.discoverList}&page=${page}${g}`
    },

    // Géneros para los pills de Descubrir. Una vez por tipo (los cachea el backend igualmente).
    async loadGenres() {
      const k = this.discoverKind
      if (this.genres[k]?.length) return
      try { this.genres[k] = (await api.get(`/api/media/genres?kind=${k}`)) || [] } catch (_) { /* pills vacíos */ }
    },

    async loadDiscover() {
      this.discoverLoading = true; this.discoverErr = ''
      this.discoverPage = 1; this.discoverTotalPages = 1; this.discover = []
      this.loadGenres()
      try {
        const d = await api.get(this._discoverUrl(1))
        this.discover = d.results || []
        this.discoverSkipped = d.skipped_anime || 0
        this.discoverTotalPages = d.total_pages || 1
      } catch (e) {
        this.discoverErr = e?.body || e?.message || 'no se pudo cargar'
        this.discover = []
      } finally { this.discoverLoading = false }
    },

    // Página siguiente de TMDB, anexada. Dedup por tmdb_id: como el filtro de anime se aplica en
    // el backend, una página puede venir con menos de 20 y el solape entre listas es posible.
    async loadMoreDiscover() {
      if (this.discoverLoadingMore || this.discoverLoading) return
      if (this.discoverPage >= this.discoverTotalPages) return
      this.discoverLoadingMore = true
      const next = this.discoverPage + 1
      try {
        const d = await api.get(this._discoverUrl(next))
        const seen = new Set(this.discover.map(x => x.tmdb_id))
        this.discover = [...this.discover, ...(d.results || []).filter(x => !seen.has(x.tmdb_id))]
        this.discoverPage = d.page || next
        this.discoverTotalPages = d.total_pages || this.discoverTotalPages
        this.discoverSkipped += d.skipped_anime || 0
      } catch (_) { /* un fallo de página no borra lo ya cargado; el centinela reintenta al scrollear */
      } finally { this.discoverLoadingMore = false }
    },

    /* Reproduce en el player NATIVO, el mismo de anime. El progreso se guarda en el almacén de
     * ESTE dominio vía `progressKey` — sin eso, una serie occidental escribiría dentro de la
     * biblioteca de anime. */
    playEpisode(item, ep, eps = []) {
      const anime = useAnimeStore()
      // El panel de episodios del reproductor necesita la lista YA normalizada, y cada entrada
      // con su propio progressKey: así cambiar de episodio desde dentro del player sigue
      // guardando el minuto en ESTE dominio y no en la biblioteca de anime.
      const playlist = eps
        .filter(e => e.season === ep.season)
        .map(e => this._epForPlayer(item, e))
      anime.playNative(
        { id: null, title: item.title, cover: item.poster },
        this._epForPlayer(item, ep),
        ep.pos || 0,
        {
          progressKey: `series:${item.id}:${ep.id}`,
          playlist: playlist.length ? playlist : null,
          onProgress: p => this.notePlayback(p),
        },
      )
    },

    _epForPlayer(item, e) {
      return {
        num: e.num, season: e.season, in_local: !!e.has_file, local_path: e.path,
        // `still` es una URL remota (TVDB vía Sonarr): pasa por el proxy o el WebView la bloquea.
        title: e.title, thumb: e.still ? imgProxy(e.still) : '', pos: e.pos || 0,
        label: `T${String(e.season).padStart(2, '0')}E${String(e.num).padStart(2, '0')}`,
        progressKey: `series:${item.id}:${e.id}`,
      }
    },

    /* Progreso EN VIVO desde el reproductor (cada ~5 s y al cerrar). Se parchea el estado local
     * en el acto — sin esperar a recargar nada — para que al salir del episodio la ficha y
     * "Seguir viendo" muestren ya el minuto exacto. El backend recibe lo mismo en paralelo y es
     * quien manda; esto sólo evita el hueco visual. */
    notePlayback({ key, pos, duration, ended }) {
      // Mismo criterio que el backend: visto sólo si de verdad llegó al final.
      const watched = !!ended || (duration > 120 && (duration - pos) <= 120)
      this.progressByKey = {
        ...this.progressByKey,
        [key]: { pos: watched ? 0 : Math.floor(pos), duration, watched },
      }
      const cw = this.continueItems.find(c => `series:${c.series_id}:${c.episode_id}` === key)
      if (cw) { cw.pos = watched ? 0 : Math.floor(pos); cw.duration = duration || cw.duration; cw.at = Math.floor(Date.now() / 1000) }
      // Al terminar un episodio, el siguiente lo decide el backend: ahí sí toca releer.
      if (watched) this.loadContinue()
    },

    async playContinue(cw) {
      const ui = useUiStore()
      if (!cw.has_file) { ui.toast('Ese episodio aún no está descargado', 'info'); return }
      try {
        const eps = (await api.get(`/api/media/series/${cw.series_id}/episodes`)).episodes || []
        const ep = eps.find(e => e.id === cw.episode_id)
        if (!ep?.path) { ui.toast('No se encontró el archivo', 'error'); return }
        const item = this.series.find(s => s.id === cw.series_id) || { id: cw.series_id, title: cw.title, poster: cw.poster }
        this.playEpisode(item, ep, eps)
      } catch (e) {
        ui.toast(`No se pudo reproducir: ${e?.body || e?.message || ''}`, 'error')
      }
    },

    /* Subtítulos: se reusa TAL CUAL la maquinaria de anime (busca en OpenSubtitles/Subdl/Subdivx
       y sólo traduce con IA si no hay nada hecho). Lo único propio de aquí es la identidad:
       títulos y TEMPORADA, que en anime siempre es 1 y en una serie occidental no. */
    openSubs(item, ep) {
      return useAnimeStore().translateSubs(
        { id: `media:${item.kind || 'series'}:${item.id}`, title: item.title },
        {
          num: ep.num, local_path: ep.path, in_local: true,
          season: ep.season || 1,
          titles: [item.title, item.original_title].filter(Boolean),
        },
      )
    },

    /* "Borrar eps" de anime: libera disco SIN perder la serie ni el progreso. */
    async freeSpace(item, { season = null } = {}) {
      const ui = useUiStore()
      try {
        const qs = season != null ? `?season=${season}` : ''
        const d = await api.del(`/api/media/series/${item.id}/files${qs}`)
        if (!d.deleted) { ui.toast('No había archivos que borrar', 'info'); return d }
        const msg = `✓ ${d.deleted} archivo(s) borrados · ${formatBytes(d.freed)} liberados`
        // Que algunos fallen no puede pasar por un éxito: se dice cuántos y se avisa en ámbar.
        ui.toast(d.failed ? `${msg} — ${d.failed} no se pudieron borrar` : msg, d.failed ? 'warn' : 'ok')
        await this.load(true)
        return d
      } catch (e) {
        ui.toast(`No se pudo liberar espacio: ${e?.body || e?.message || ''}`, 'error')
        return null
      }
    },

    async movieSubs(item) {
      const ui = useUiStore()
      let f
      try {
        f = await api.get(`/api/media/movie/${item.id}/file`)
      } catch (e) {
        // "Aún no descargada" y "Radarr falló" NO son lo mismo: un fallo que parece un vacío
        // deja al usuario buscando subtítulos de algo que sí está en disco.
        ui.toast(e?.status === 404 ? 'Todavía no está descargada' : 'No se pudo consultar el archivo', 'error')
        return
      }
      // La película es "episodio 0": sin número, la búsqueda va por obra y no por S/E.
      return useAnimeStore().translateSubs(
        { id: `media:movie:${item.id}`, title: item.title },
        { num: 0, local_path: f.path, in_local: true, titles: [item.title, item.original_title].filter(Boolean) },
      )
    },

    async playMovie(item) {
      const ui = useUiStore()
      const anime = useAnimeStore()
      try {
        const f = await api.get(`/api/media/movie/${item.id}/file`)
        anime.playNative(
          { id: null, title: item.title, cover: item.poster },
          { num: 1, in_local: true, local_path: f.path, title: item.title },
          f.pos || 0,
          { progressKey: `movie:${item.id}`, onProgress: p => this.notePlayback(p) },
        )
      } catch (e) {
        // El backend distingue "aún no descargada" de "Radarr falló": se respeta.
        ui.toast(e?.status === 404 ? 'Todavía no está descargada' : 'No se pudo reproducir', 'error')
      }
    },
  },
})
