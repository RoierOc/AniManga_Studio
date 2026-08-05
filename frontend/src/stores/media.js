/* Store de Series y Películas (Sonarr/Radarr).
 *
 * Existe por el mismo motivo que el de anime: hasta ahora todo este estado vivía DENTRO de
 * `MediaLibrary.vue`, así que cambiar de pestaña lo perdía (biblioteca recargada, búsqueda
 * borrada, filtros olvidados). Aquí sobrevive a la navegación y las vistas quedan tontas.
 *
 * La cola SÍ es propia (`queue`), y no es una duplicación de la vista de torrents de anime: lo que
 * baja aquí lo pide Sonarr/Radarr, así que el estado que importa es el suyo («importando», «falta
 * un fichero»), no el del torrent. Un torrent al 100 % con el import fallando es un caso real que
 * qBittorrent da por terminado.
 */
import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'
import { useTagsStore } from '@/stores/tags'
import { formatBytes, mediaStatusLabel } from '@/lib/format'
import { imgProxy } from '@/lib/img'
import { vtGo } from '@/lib/vt'


export const useMediaStore = defineStore('media', {
  state: () => ({
    status: {},
    series: [],
    movies: [],
    errors: {},          // {sonarr|radarr: mensaje} — "falló" viaja aparte del array vacío
    loading: false,
    loaded: false,
    loadError: '',       // la CARGA entera falló (≠ `errors`, que son fallos por indexer)
    _searchReqId: 0,     // anti-carrera al buscar mientras se escribe
    _searchTimer: 0,

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

    // agenda / cola / historial — las tres pestañas que Cine no tenía
    agenda: [], agendaErrors: {}, agendaLoading: false, agendaError: '', agendaLoaded: false,
    queue: [], queueErrors: {}, queueLoading: false, queueError: '',
    watched: [], grabs: [], historyLoading: false, historyError: '', historyLoaded: false,

    artLoaded: false,     // arte de TMDB ya aplicado sobre lo que dieron Sonarr/Radarr

    // «Para ti» (TMDB sobre tu biblioteca)
    forYou: [], forYouError: '', forYouLoading: false, forYouLoaded: false,
  }),

  getters: {
    /* La pestaña vive en `ui.tabs.media`, no aquí. Era un `ref` + localStorage propio, y por eso
       atrás/adelante devolvía la sección con la pestaña equivocada: el historial no la veía.
       Ahí la ve, la restaura, y de paso desaparece el localStorage a mano. */
    sub: () => useUiStore().tabs.media,

    // Un servicio caído se DICE; nunca se disfraza de biblioteca vacía.
    // Dos fuentes, porque son dos preguntas distintas: `status` es el ping (`system/status`) y
    // `errors` es lo que falló al pedir la lista. Un Sonarr que contesta al ping pero revienta
    // al listar sólo aparecía en `errors`… que no leía nadie: biblioteca vacía en silencio.
    offline: (s) => [...new Set([
      ...Object.entries(s.status).filter(([, v]) => v && !v.online).map(([k]) => k),
      ...Object.keys(s.errors),
    ])],

    all: (s) => [...s.series, ...s.movies],
    discoverHasMore: (s) => s.discoverPage < s.discoverTotalPages,

    /* Estado de visionado por obra, DERIVADO de lo que ya sabemos. No hay un estado manual como en
       anime (Sonarr no lo tiene y pedirte que marques cosas a mano es peaje): lo empezado sale de
       «Seguir viendo» y lo terminado, del historial. La clave es `<kind>:<id>`, la identidad
       canónica de este dominio.

       Ojo con el orden: una obra puede estar en las dos listas (viste 8 episodios y vas por el 9).
       «Viendo» gana, porque es lo que estás haciendo AHORA. */
    watchState(s) {
      const st = {}
      for (const w of s.watched) {
        if (!w.watched) continue
        st[w.kind === 'movie' ? `movie:${w.movie_id}` : `series:${w.series_id}`] = 'seen'
      }
      for (const c of s.continueItems) {
        st[c.kind === 'movie' ? `movie:${c.movie_id}` : `series:${c.series_id}`] = 'watching'
      }
      for (const [k, p] of Object.entries(s.progressByKey)) {
        // El progreso EN VIVO manda sobre el historial cargado al entrar: si acabas de empezar una
        // película, la biblioteca no debería seguir diciendo que la tienes vista.
        const [kind, id] = k.split(':')
        if (kind === 'movie') st[`movie:${id}`] = p.watched ? 'seen' : (p.pos ? 'watching' : st[`movie:${id}`])
      }
      return st
    },

    items(s) {
      let list = this.all
      if (s.filter === 'series') list = s.series
      else if (s.filter === 'movies') list = s.movies
      else if (s.filter === 'missing') list = list.filter(x => x.have < x.total)
      else if (s.filter.startsWith('tag:')) {
        const t = s.filter.slice(4)
        const tags = useTagsStore()
        list = list.filter(x => tags.forWork('media', `${x.kind}:${x.id}`).includes(t))
      }
      else if (s.filter === 'watching' || s.filter === 'seen') {
        list = list.filter(x => this.watchState[`${x.kind}:${x.id}`] === s.filter)
      }
      else if (s.filter === 'unseen') {
        // Sin empezar, pero DESCARGADO: ofrecer lo que no está en disco sería una lista de deseos,
        // no algo que puedas poner esta noche.
        list = list.filter(x => x.have > 0 && !this.watchState[`${x.kind}:${x.id}`])
      }

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
      const est = this.watchState
      const conEstado = (v) => this.all.filter(x => est[`${x.kind}:${x.id}`] === v).length
      return {
        all: s.series.length + s.movies.length,
        series: s.series.length,
        movies: s.movies.length,
        missing: this.all.filter(x => x.have < x.total).length,
        watching: conEstado('watching'),
        seen: conEstado('seen'),
        unseen: this.all.filter(x => x.have > 0 && !est[`${x.kind}:${x.id}`]).length,
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
      // Por id de serie, para poder colgar del hero el episodio EXACTO por el que vas. El hero
      // decía «SIGUE VIENDO» y no llevaba ni episodio, ni barra, ni forma de reanudar: su botón
      // abría la ficha. El dato estaba aquí al lado, sólo que no se pasaba.
      const enCurso = new Map(s.continueItems.filter(c => c.kind !== 'movie').map(c => [c.series_id, c]))
      const withArt = this.all.filter(x => x.banner)
      const pick = [
        ...withArt.filter(x => x.kind === 'series' && enCurso.has(x.id)),
        ...withArt.filter(x => !(x.kind === 'series' && enCurso.has(x.id))),
      ].slice(0, 5)
      return pick.map(x => {
        const cont = x.kind === 'series' ? enCurso.get(x.id) : null
        return {
          id: `${x.kind}:${x.id}`,
          raw: x,
          cont,
          art: x.banner,
          artFallback: x.poster,
          // El rótulo del título en PNG transparente, como en Mi Anime. Si no hay, `MediaHero`
          // cae al título en texto: nunca queda un hueco.
          logo: x.logo || '',
          overline: cont ? 'SIGUE VIENDO' : (x.kind === 'movie' ? 'PELÍCULA' : 'EN TU BIBLIOTECA'),
          title: x.title,
          progress: cont && cont.duration ? Math.min(100, (cont.pos / cont.duration) * 100) : 0,
          // Ojo al duplicado: cuando la serie está completa, «Completa» y el estado (`ended` →
          // «Terminada») dicen lo mismo. Se queda uno.
          meta: (() => {
            const completa = x.have >= x.total
            const est = mediaStatusLabel(x.status)
            return [
              // Si vas por un episodio concreto, ESO es lo que quieres leer primero; el recuento
              // de la biblioteca pasa a segundo plano.
              cont ? `T${cont.season} · Episodio ${cont.num}` : '',
              completa ? (x.kind === 'movie' ? 'Película' : '') : `${x.have} de ${x.total} episodios`,
              x.year ? String(x.year) : '',
              est,
            ].filter(Boolean)
          })(),
        }
      })
    },
  },

  actions: {
    setSub(v) { useUiStore().setTab('media', v) },

    async init() {
      if (!this.loaded) await this.load()
      this.loadContinue()
      // El historial alimenta los filtros «Viendo/Vistas/Sin empezar» de la biblioteca, así que
      // se pide al entrar y no al abrir su pestaña. MEDIDO: 40 ms.
      if (!this.historyLoaded) this.loadHistory()
      this.loadArt()
      useTagsStore().load()
    },

    /* Arte de TMDB por encima del de Sonarr/Radarr. Va DETRÁS de la primera pintura a propósito:
       la rejilla sale con el arte de Sonarr y se afina sola. MEDIDO: el fondo de una serie pasa de
       1920×1080 a 3840×2160 (el hero se pinta a sangre: a 2560 el viejo se estiraba ×1,33) y
       aparece el logo, que ninguna de las dos gestoras tiene. */
    async loadArt() {
      if (this.artLoaded) return
      try {
        const d = await api.get('/api/media/art')
        const art = d.art || {}
        if (!Object.keys(art).length) return
        const aplicar = (x) => Object.assign(x, art[`${x.kind}:${x.id}`] || {})
        this.series.forEach(aplicar)
        this.movies.forEach(aplicar)
        this.artLoaded = true
      } catch (_) { /* la biblioteca ya se ve; esto sólo la mejora */ }
    },

    async loadAgenda({ back = 7, days = 21 } = {}) {
      this.agendaLoading = true
      this.agendaError = ''
      try {
        const d = await api.get(`/api/media/agenda?back=${back}&days=${days}`)
        this.agenda = d.items || []
        this.agendaErrors = d.errors || {}
        this.agendaLoaded = true
      } catch (e) {
        this.agendaError = e?.body || e?.message || 'No se pudo cargar la agenda.'
      } finally { this.agendaLoading = false }
    },

    async loadQueue() {
      this.queueLoading = true
      try {
        const d = await api.get('/api/media/queue')
        this.queue = d.items || []
        this.queueErrors = d.errors || {}
        this.queueError = ''
      } catch (e) {
        this.queueError = e?.body || e?.message || 'No se pudo leer la cola.'
      } finally { this.queueLoading = false }
    },

    async cancelQueue(item, { blocklist = false } = {}) {
      const ui = useUiStore()
      try {
        await api.post('/api/media/queue/remove', { app: item.app, id: item.id, blocklist })
        this.queue = this.queue.filter(q => !(q.app === item.app && q.id === item.id))
        ui.toast(blocklist ? 'Descarga cancelada y release descartada' : 'Descarga cancelada', 'ok')
      } catch (e) {
        ui.toast(`No se pudo cancelar: ${e?.body || e?.message || ''}`, 'error')
      }
      this.loadQueue()
    },

    async loadHistory() {
      this.historyLoading = true
      this.historyError = ''
      try {
        const d = await api.get('/api/media/history?limit=80')
        this.watched = d.watched || []
        this.grabs = d.grabs || []
        this.historyLoaded = true
      } catch (e) {
        this.historyError = e?.body || e?.message || 'No se pudo cargar el historial.'
      } finally { this.historyLoading = false }
    },

    async loadForYou() {
      if (this.forYouLoading) return
      this.forYouLoading = true
      this.forYouError = ''
      try {
        const d = await api.get('/api/for_you/media')
        this.forYou = d.items || []
        this.forYouLoaded = true
      } catch (e) {
        this.forYouError = e?.body || e?.message || 'No se pudieron cargar las recomendaciones.'
      } finally { this.forYouLoading = false }
    },

    async load() {
      this.loading = true
      this.loadError = ''
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
      } catch (e) {
        // Antes NO había catch: el rechazo se perdía y la lista se quedaba en [], así que la
        // vista anunciaba «Tu biblioteca está vacía» cuando lo que pasaba era que Sonarr/Radarr
        // (o el propio backend) no respondían. "Falló" y "no había" no pueden ser lo mismo.
        this.loadError = e?.message || 'No se pudo contactar con el servidor.'
      } finally { this.loading = false }
    },

    async loadContinue() {
      try { this.continueItems = (await api.get('/api/media/continue')).items || [] } catch { /* no crítico */ }
    },

    /* Marcar visto / no visto A MANO. Mi Anime lo tiene desde siempre (`toggleWatched`) y aquí
       faltaba: si ves una película en otro sitio, o el reproductor se cierra antes del final, la
       biblioteca se queda mintiendo y no había forma de corregirla.

       No necesita endpoint nuevo: `/api/media/progress` ya decide «visto» con `ended`, y al
       desmarcar se manda posición 0 — reanudar en el segundo final no serviría de nada. */
    async setWatched(key, visto, duration = 0, meta = {}) {
      const ui = useUiStore()
      const antes = this.progressByKey[key]
      // Optimista: la rejilla y los filtros reaccionan al instante, y se revierte si falla.
      this.progressByKey = { ...this.progressByKey, [key]: { pos: 0, duration, watched: visto } }
      try {
        // `meta` (título, portada, nº de episodio) es lo que necesita el historial compartido:
        // marcar a mano tiene que quedar registrado igual que verlo, o la Retrospectiva contaría
        // una cosa y la biblioteca otra.
        await api.post('/api/media/progress', { key, position: 0, duration, ended: visto, ...meta })
        this.loadContinue()
        this.historyLoaded = false
        this.loadHistory()
      } catch (_) {
        const copia = { ...this.progressByKey }
        if (antes) copia[key] = antes; else delete copia[key]
        this.progressByKey = copia
        ui.toast('No se pudo actualizar', 'error')
      }
    },

    /* Abre la ficha desde una lista que sólo tiene el id (agenda, historial, cola). Si la obra ya
       no está en la biblioteca no se hace nada: navegar a una ficha vacía es peor que no navegar. */
    openById(kind, id) {
      const it = (kind === 'movie' ? this.movies : this.series).find(x => x.id === Number(id))
      if (it) this.openDetail(it)
      return !!it
    },

    /* Alta desde una tarjeta de TMDB (Descubrir y «Para ti» hacen lo MISMO). Vivía suelto dentro
       de `MediaDiscover.vue`; al aparecer el segundo consumidor sube al store en vez de copiarse:
       el paso por `/resolve` (lookup exacto `tmdb:<id>`) es justo lo que se olvida al duplicar. */
    async addFromTmdb(it, kind) {
      const ui = useUiStore()
      if (it.already) { ui.toast('Ya está en tu biblioteca', 'info'); return false }
      this.adding = it.tmdb_id
      try {
        const t = it.title_original || it.title
        const r = await api.get(`/api/media/resolve?kind=${kind}&tmdb_id=${it.tmdb_id}` +
                                `&title=${encodeURIComponent(t)}&year=${it.year || ''}`)
        if (!r.match) {
          ui.toast(`«${t}» no está en el catálogo de ${kind === 'movie' ? 'Radarr' : 'Sonarr'}`, 'error')
          return false
        }
        if (r.match.already) { ui.toast('Ya está en tu biblioteca', 'info'); it.already = true; return false }
        const ok = await this.add(r.match.ext_id, kind)
        if (ok) it.already = true
        return ok
      } catch (e) {
        ui.toast(`No se pudo añadir: ${e?.body || e?.message || ''}`, 'error')
        return false
      } finally { this.adding = '' }
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

    // Igual que la ficha de anime: es una PÁGINA, así que registra entrada de historial (antes no
    // lo hacía y "atrás" desde una serie te sacaba de la sección entera).
    // `vtGo` para que el póster de la tarjeta vuele hasta el hero, como en anime y manga. Todo lo
    // que cambia estado va DENTRO del callback: `startViewTransition` no lo ejecuta en el acto
    // (ver lib/vt.js), así que leer `this.detail` justo después leería el valor viejo.
    openDetail(item) { vtGo(() => { this.detail = item; useUiStore().pushNav() }) },
    exitDetail() { useUiStore().back(() => this.closeDetail()) },

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
      if (!q) { this._searchReqId++; this.results = null; this.searching = false; return }
      // Anti-carrera: buscando mientras se escribe, la respuesta de "bre" puede llegar DESPUÉS
      // que la de "breaking" y pisarla. Sólo escribe la petición más reciente.
      const rid = ++this._searchReqId
      this.searching = true; this.searchErr = ''
      try {
        const kind = this.filter === 'movies' ? 'movie' : 'series'
        const r = (await api.get(`/api/media/search?q=${encodeURIComponent(q)}&kind=${kind}`)).results || []
        if (rid !== this._searchReqId) return
        this.results = r
      } catch (e) {
        if (rid !== this._searchReqId) return
        // Un fallo NO se pinta como "sin resultados": confundirlos hace creer que la serie no
        // existe cuando lo que pasa es que Sonarr está caído.
        this.searchErr = e?.body || e?.message || 'no se pudo buscar'
        this.results = null
      } finally { if (rid === this._searchReqId) this.searching = false }
    },

    /* Buscar mientras se escribe; Enter/el botón disparan ya, sin esperar al debounce. */
    onSearchInput(term) {
      clearTimeout(this._searchTimer)
      this._searchTimer = setTimeout(() => this.runSearch(term), 350)
    },
    submitSearch(term) { clearTimeout(this._searchTimer); return this.runSearch(term) },

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
      // El episodio ya normalizado trae el minuto VIVO; usar `ep.pos` de nuevo aquí lo tiraría.
      const cur = this._epForPlayer(item, ep)
      anime.playNative(
        { id: null, title: item.title, cover: item.poster },
        cur,
        cur.pos,
        {
          progressKey: cur.progressKey,
          playlist: playlist.length ? playlist : null,
          onProgress: p => this.notePlayback(p),
        },
      )
    },

    /* Posición de reanudación REAL de una clave: lo que dejó el reproductor hace un momento
     * manda sobre lo que trajo Sonarr. Sin esto, salir de un episodio y volver a entrar
     * reanudaba en el minuto viejo: la lista de episodios se recarga cada mucho, así que el
     * `pos` de la API sigue siendo el de la sesión anterior hasta un F5. Lo usan la ficha, el
     * riel de "Seguir viendo" y el panel del reproductor — un solo sitio para los tres. */
    livePos(key, fallback = 0) {
      const p = this.progressByKey[key]
      return p ? p.pos : (fallback || 0)
    },

    _epForPlayer(item, e) {
      const progressKey = `series:${item.id}:${e.id}`
      return {
        num: e.num, season: e.season, in_local: !!e.has_file, local_path: e.path,
        // `still` es una URL remota (TVDB vía Sonarr): pasa por el proxy o el WebView la bloquea.
        title: e.title, thumb: e.still ? imgProxy(e.still) : '',
        pos: this.livePos(progressKey, e.pos),
        label: `T${String(e.season).padStart(2, '0')}E${String(e.num).padStart(2, '0')}`,
        progressKey,
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
      if (cw) {
        cw.pos = watched ? 0 : Math.floor(pos); cw.duration = duration || cw.duration
        cw.at = Math.floor(Date.now() / 1000)
        // Reordena el riel al instante: "Seguir viendo" va por lo más reciente y esto ES lo
        // más reciente. Sin reordenar, lo que acabas de ver seguía apareciendo el cuarto.
        this.continueItems = [cw, ...this.continueItems.filter(c => c !== cw)]
      } else if (this._railMiss !== key) {
        // Episodio que aún no estaba en el riel (primera vez que lo ves): se pide la lista UNA
        // vez, no en cada latido de 5 s, para que aparezca sin recargar la interfaz.
        this._railMiss = key
        this.loadContinue()
      }
      // Al terminar un episodio, el siguiente lo decide el backend: ahí sí toca releer.
      if (watched) { this._railMiss = null; this.loadContinue() }
    },

    async playContinue(cw) {
      const ui = useUiStore()
      // El riel mezcla series y películas: una película se reanuda por su propio camino, que ya
      // sabe leer el minuto guardado (`livePos`). Sin esto, buscaba episodios de una serie que
      // no existe y el clic no hacía nada.
      if (cw.kind === 'movie') {
        const peli = this.movies.find(m => m.id === cw.movie_id) ||
                     { id: cw.movie_id, title: cw.title, poster: cw.poster }
        return this.playMovie(peli)
      }
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

    /* "Borrar eps" de anime: libera disco SIN perder la serie ni el progreso.
     * El backend borra el fichero Y quita el torrent — sin lo segundo el hardlink que siembra
     * qBittorrent deja los bytes puestos y Sonarr re-importa el episodio (era el bug). */
    async freeSpace(item, { season = null } = {}) {
      const ui = useUiStore()
      const isMovie = (item.kind || 'series') === 'movie'
      try {
        const url = isMovie
          ? `/api/media/movie/${item.id}/file`
          : `/api/media/series/${item.id}/files${season != null ? `?season=${season}` : ''}`
        const d = await api.del(url)
        if (!d.deleted) { ui.toast('No había archivos que borrar', 'info'); return d }
        const msg = `✓ ${d.deleted} archivo(s) borrados · ${formatBytes(d.freed)} liberados`
        // Un fallo parcial NO puede pasar por un éxito. Y si el torrent sigue vivo el espacio
        // no está realmente libre (los datos siguen sembrándose): se dice, no se calla.
        const pegas = [
          d.failed ? `${d.failed} no se pudieron borrar` : '',
          d.torrents_failed ? `${d.torrents_failed} torrent(s) siguen sembrando` : '',
          d.torrents_error ? 'no se pudo consultar el torrent: el espacio puede seguir ocupado' : '',
        ].filter(Boolean)
        ui.toast(pegas.length ? `${msg} — ${pegas.join('; ')}` : msg, pegas.length ? 'warn' : 'ok')
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
        // Mismo criterio que en series: el minuto vivo manda sobre el que devuelve Radarr.
        const progressKey = `movie:${item.id}`
        anime.playNative(
          { id: null, title: item.title, cover: item.poster },
          { num: 1, in_local: true, local_path: f.path, title: item.title },
          this.livePos(progressKey, f.pos),
          { progressKey, onProgress: p => this.notePlayback(p) },
        )
      } catch (e) {
        // El backend distingue "aún no descargada" de "Radarr falló": se respeta.
        ui.toast(e?.status === 404 ? 'Todavía no está descargada' : 'No se pudo reproducir', 'error')
      }
    },
  },
})
