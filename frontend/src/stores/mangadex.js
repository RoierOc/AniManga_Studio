import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from './ui'
import { useMangaStore } from './manga'

const ALL_RATINGS = ['safe', 'suggestive', 'erotica']

export const useMangadexStore = defineStore('mangadex', {
  state: () => ({
    tab: 'followed',            // followed | latest | rating | search
    query: '',
    results: [],                // search results
    _reqId: 0,                  // anti-carrera al buscar mientras se escribe
    _qTimer: 0,
    popular: [],                // popular/latest/rating list
    loading: false,
    error: '',                  // '' = sin fallo. Un toast de 3 s no puede ser el ÚNICO aviso:
                                // se va y deja una rejilla vacía que miente («Sin resultados»)
    page: 1,
    total: 0,

    ratings: ['safe', 'suggestive'],
    allTags: [],
    selectedTags: [],
    showTagFilter: false,

    scores: {},                 // mal_id -> anilist score

    detail: null,               // full manga meta
    chapters: [],
    detailLoading: false,
    detailLang: '',             // language filter in detail
    reading: {},                // chapter id -> true while "Leer" is resolving pages

    followed: [],               // mangadex /library
    followedLoaded: false,
    localIds: [],               // ids present in local_library
    authed: false,

    // AniList Top
    alTop: [],
    alLoading: false,
    alError: '',
    alPage: 1,
    alHasMore: false,
    alSort: 'SCORE_DESC',
    alGenres: [],
    alFilter: '',               // selected genre/tag name
    alFilterType: 'genre',      // genre | tag
  }),

  getters: {
    list: (s) => s.tab === 'search' ? s.results : s.popular,
    detailLangs: (s) => [...new Set(s.chapters.map(c => c.language))].sort(),
    detailChapters: (s) => s.detailLang ? s.chapters.filter(c => c.language === s.detailLang) : s.chapters,
  },

  actions: {
    async checkAuth() {
      try { this.authed = !!(await api.get('/api/mangadex/check'))?.authenticated } catch (_) {}
      this.loadLocalLibrary()
    },
    async login() {
      const ui = useUiStore()
      ui.toast('Conectando con MangaDex…', 'info')
      try {
        const d = await api.post('/api/mangadex/login', {})
        if (d.error) { ui.toast('Login falló — revisa credenciales', 'error'); return }
        this.authed = true; ui.toast('Conectado a MangaDex ✓', 'ok'); this.loadFollowed()
      } catch (_) { ui.toast('No se pudo iniciar sesión', 'error') }
    },
    async loadTags() {
      if (this.allTags.length) return
      try { this.allTags = await api.get('/api/mangadex/tags') || [] } catch (_) {}
    },
    toggleRating(r) {
      this.ratings = this.ratings.includes(r) ? this.ratings.filter(x => x !== r) : [...this.ratings, r]
      this.refresh()
    },
    toggleTag(id) {
      this.selectedTags = this.selectedTags.includes(id) ? this.selectedTags.filter(x => x !== id) : [...this.selectedTags, id]
      this.refresh()
    },
    _ratingParams() {
      return (this.ratings.length ? this.ratings : ALL_RATINGS).map(r => `rating[]=${r}`).join('&')
    },
    _tagParams() {
      return this.selectedTags.map(t => `tags[]=${t}`).join('&')
    },

    refresh() {
      if (this.tab === 'search') this.search()
      else this.loadPopular(this.tab, 1)
    },
    setTab(tab) {
      this.tab = tab
      if (tab === 'search') { if (this.query.trim()) this.search() }
      else this.loadPopular(tab, 1)
    },

    async loadPopular(type, page = 1) {
      this.loading = true
      this.error = ''
      this.page = page
      try {
        const qs = [`type=${type}`, `page=${page}`, this._ratingParams(), this._tagParams()].filter(Boolean).join('&')
        const d = await api.get(`/api/mangadex/popular?${qs}`)
        const results = d.results || []
        this.popular = page === 1 ? results : [...this.popular, ...results]
        this.total = d.total || 0
        this._fetchScores(results)
      } catch (e) {
        this.error = e?.body || e?.message || 'MangaDex no responde'
        useUiStore().toast('Error cargando MangaDex', 'error')
      } finally { this.loading = false }
    },
    async search() {
      const q = this.query.trim()
      if (q.length < 2 && !this.selectedTags.length) { this._reqId++; this.results = []; this.loading = false; return }
      this.tab = 'search'
      // Anti-carrera: buscando mientras se escribe, una respuesta vieja puede llegar la última
      // y pisar la buena. Sólo escribe la petición más reciente.
      const rid = ++this._reqId
      this.loading = true
      this.error = ''
      try {
        const qs = [`q=${encodeURIComponent(q)}`, this._ratingParams(), this._tagParams()].filter(Boolean).join('&')
        const r = await api.get(`/api/mangadex/search?${qs}`) || []
        if (rid !== this._reqId) return
        this.results = r
        this._fetchScores(this.results)
      } catch (e) {
        if (rid !== this._reqId) return
        this.error = e?.body || e?.message || 'La búsqueda falló'
        useUiStore().toast('Error buscando', 'error')
      }
      finally { if (rid === this._reqId) this.loading = false }
    },

    /* Buscar mientras se escribe; Enter dispara ya, sin esperar al debounce. */
    onQueryInput() {
      clearTimeout(this._qTimer)
      this._qTimer = setTimeout(() => this.search(), 350)
    },
    submitQuery() { clearTimeout(this._qTimer); this.search() },

    async _fetchScores(list) {
      const ids = list.map(m => m.mal_id).filter(id => id && !(id in this.scores))
      if (!ids.length) return
      try {
        const d = await api.post('/api/anilist/scores', { mal_ids: ids })
        Object.assign(this.scores, d || {})
      } catch (_) {}
    },
    /* `/api/anilist/scores` devuelve un OBJETO por id (`{score, genres, popularity, al_id}`), pero
     * la búsqueda (línea de abajo) guarda el número pelado. La tarjeta espera un número: pasarle el
     * objeto pintaba `★ NaN` en todas las tarjetas (y el color por nota nunca se activaba). */
    score(m) {
      const s = m.mal_id ? this.scores[m.mal_id] : null
      const n = typeof s === 'object' && s !== null ? s.score : s
      return typeof n === 'number' && !Number.isNaN(n) ? n : null
    },

    async openDetail(m) {
      this.detail = m
      this.chapters = []
      this.detailLang = ''
      this.detailLoading = true
      try {
        const [full, chs] = await Promise.all([
          api.get(`/api/mangadex/manga/${m.id}`).catch(() => null),
          api.get(`/api/mangadex/chapters/${m.id}`).catch(() => []),
        ])
        if (full) this.detail = { ...m, ...full }
        this.chapters = chs || []
        // Show all languages by default (empty = no filter)
        this.detailLang = ''
      } catch (_) {}
      finally { this.detailLoading = false }
    },
    closeDetail() { this.detail = null },

    async downloadChapter(ch) {
      try {
        await api.post('/api/download/download_chapter', {
          title: this.detail.title, chapter: ch.chapter, chapterId: ch.id, mangaId: this.detail.id,
        })
        useUiStore().toast(`Descargando cap. ${ch.chapter}`, 'info')
      } catch (_) { useUiStore().toast('No se pudo iniciar la descarga', 'error') }
    },

    async readChapter(ch) {
      const ui = useUiStore()
      this.reading[ch.id] = true
      try {
        const pg = await api.get(`/api/mangadex/chapter/${ch.id}/pages`)
        const pageUrls = pg.pages || []
        if (!pageUrls.length) { ui.toast('Capítulo sin páginas', 'error'); return }
        useMangaStore().openOnlineReader(this.detail.title, ch.chapter, pageUrls, 'MangaDex', { kind: 'mangadex', chapterRef: ch.id }, this.detail.cover || '')
      } catch (_) { ui.toast('No se pudo abrir el capítulo', 'error') }
      finally { delete this.reading[ch.id] }
    },

    async addLocal(m) {
      const ui = useUiStore()
      try {
        await api.post('/api/mangadex/local_library/add', {
          manga: {
            id: m.id,
            title: m.title,
            cover: m.cover,
            mal_id: m.mal_id,
            al_id: m.al_id,
          }
        })
        if (!this.localIds.includes(m.id)) this.localIds.push(m.id)
        ui.toast('Añadido a tu biblioteca', 'ok')
      } catch (_) { ui.toast('No se pudo añadir', 'error') }
    },

    async loadLocalLibrary() {
      try { this.localIds = (await api.get('/api/mangadex/local_library') || []).map(m => String(m.id || m.mangaId || '')) } catch (_) {}
    },

    async loadFollowed() {
      this.loading = true
      this.error = ''
      try { this.followed = await api.get('/api/mangadex/library') || []; this._fetchScores(this.followed) }
      catch (e) { this.followed = []; this.error = e?.body || e?.message || 'No se pudo leer tu biblioteca de MangaDex' }
      finally { this.followedLoaded = true; this.loading = false }
    },
    async follow(m) {
      try { await api.post(`/api/mangadex/follow/${m.id}`, {}); useUiStore().toast('Siguiendo', 'ok') } catch (_) {}
    },

    /* ── AniList Top ────────────────────────────────────────────────────── */
    async loadAnilistGenres() {
      if (this.alGenres.length) return
      try { this.alGenres = await api.get('/api/anilist/genres') || [] } catch (_) {}
    },
    setAlFilter(g) {
      if (g && this.alFilter === g.name) { this.alFilter = ''; this.alFilterType = 'genre' }
      else if (g) { this.alFilter = g.name; this.alFilterType = g.type }
      else { this.alFilter = '' }
      this.loadAnilistTop(1)
    },
    async loadAnilistTop(page = 1) {
      this.alLoading = true
      this.alError = ''
      this.alPage = page
      try {
        const qs = [`sort=${this.alSort}`, `page=${page}`]
        if (this.alFilter) qs.push(`${this.alFilterType}=${encodeURIComponent(this.alFilter)}`)
        const d = await api.get(`/api/anilist/top?${qs.join('&')}`)
        const results = d.results || []
        this.alTop = page === 1 ? results : [...this.alTop, ...results]
        this.alHasMore = !!d.hasNextPage
        // these carry mal_id → reuse the score cache directly
        for (const m of results) if (m.mal_id && m.score) this.scores[m.mal_id] = m.score
      } catch (e) {
        this.alError = e?.body || e?.message || 'AniList no responde'
        useUiStore().toast('Error cargando AniList', 'error')
      } finally { this.alLoading = false }
    },
    // open an AniList result by searching MangaDex for its title
    openAnilistResult(m) {
      this.query = m.title_romaji || m.title
      this.setTab('search')
    },
    async unfollow(m) {
      try { await api.post(`/api/mangadex/unfollow/${m.id}`, {}); this.followed = this.followed.filter(x => x.id !== m.id) } catch (_) {}
    },
  },
})
