import { api } from '@/lib/api'

const seasonalFlights = new WeakMap()
const exploreFlights = new WeakMap()
const airingFlights = new WeakMap()
const genreFlights = new WeakMap()

function latestRequest(flights, store, key, run, onStart = () => {}, onFinish = () => {}) {
  const active = flights.get(store)
  if (active?.key === key) return active.promise

  const entry = { key, promise: null }
  flights.set(store, entry)
  onStart()
  entry.promise = (async () => {
    try { return await run(() => flights.get(store) === entry) }
    finally {
      if (flights.get(store) === entry) {
        onFinish()
        flights.delete(store)
      }
    }
  })()
  return entry.promise
}

export const animeDiscoveryActions = {
  async loadAiring() {
    return latestRequest(airingFlights, this, 'airing', async current => {
      this.airingError = ''
      try {
        const data = await api.get('/api/anime/airing')
        if (current()) this.airing = data || {}
      } catch (e) {
        if (current()) this.airingError = e?.body || e?.message || 'No se pudo consultar AniList.'
      }
    }, () => { this.airingLoading = true }, () => { this.airingLoading = false })
  },

  async loadForYou() {
    if (this.forYou.length || this._forYouLoading) return
    this._forYouLoading = true
    try {
      const r = await api.get('/api/for_you/anime')
      if (Array.isArray(r)) { this.forYou = r; this.forYouReason = '' }
      else { this.forYou = r?.items || []; this.forYouReason = r?.reason || '' }
    } catch {
      this.forYou = []
      this.forYouReason = ''
    } finally { this._forYouLoading = false }
  },

  needsAnimeMetadataBackfill() {
    return this.library.some(anime =>
      !anime.banner || !anime.genres?.length || !anime.total_episodes || !anime.cover_xl || !anime.synopsis)
  },

  async loadSeasonal() {
    const params = new URLSearchParams({ sort: this.seasonSort })
    if (this.season) params.set('season', this.season)
    if (this.year) params.set('year', String(this.year))
    const key = params.toString()

    return latestRequest(seasonalFlights, this, key, async current => {
      this.seasonalError = ''
      try {
        const data = await api.get(`/api/anime/seasonal?${params}`)
        if (!current()) return
        this.seasonal = data.results || []
        if (!this.season) this.season = data.season || ''
        if (!this.year) this.year = data.year || 0
      } catch (e) {
        if (!current()) return
        this.seasonal = []
        this.seasonalError = e?.body || e?.message || 'Error desconocido'
      }
    }, () => { this.seasonalLoading = true }, () => { this.seasonalLoading = false })
  },

  seasonNav(dir) {
    const seasons = ['WINTER', 'SPRING', 'SUMMER', 'FALL']
    const index = seasons.indexOf(this.season)
    if (index === -1) return this.loadSeasonal()
    let next = index + dir
    if (next < 0) { next = 3; this.year-- }
    else if (next > 3) { next = 0; this.year++ }
    this.season = seasons[next]
    return this.loadSeasonal()
  },

  isInLibrary(anime) {
    if (!anime) return false
    return this.library.some(item =>
      (anime.al_id && item.al_id === anime.al_id) ||
      (anime.mal_id && item.mal_id === anime.mal_id) ||
      (anime.title && item.title === anime.title))
  },

  _exploreSortKey() {
    return { score: 'SCORE_DESC', popularity: 'POPULARITY_DESC', trending: 'TRENDING_DESC' }[this.exploreSort] || 'SCORE_DESC'
  },

  async loadExplore(append = false) {
    if (!append) this.explorePage = 1
    const params = new URLSearchParams({ sort: this._exploreSortKey(), page: String(this.explorePage) })
    if (this.exploreGenre) {
      const genre = this.exploreGenreList.find(item => item.name === this.exploreGenre)
      params.set(genre?.type === 'tag' ? 'tag' : 'genre', this.exploreGenre)
    }
    if (this.exploreYear) params.set('year', String(this.exploreYear))
    if (this.exploreFormat) params.set('format', this.exploreFormat)
    const key = `${append ? 'append' : 'replace'}:${params}`

    return latestRequest(exploreFlights, this, key, async current => {
      this.exploreError = ''
      try {
        const data = await api.get(`/api/anilist/anime_top?${params}`)
        if (!current()) return
        const rows = data.results || []
        this.explore = append ? [...this.explore, ...rows] : rows
        this.exploreHasNext = !!data.hasNextPage
      } catch (e) {
        if (!current()) return
        if (!append) this.explore = []
        this.exploreError = e?.body || e?.message || 'Error desconocido'
      }
    }, () => { this.exploreLoading = true }, () => { this.exploreLoading = false })
  },

  async loadExploreMore() {
    if (this.exploreLoading || !this.exploreHasNext) return
    this.explorePage += 1
    return this.loadExplore(true)
  },

  async loadExploreGenres() {
    if (this.exploreGenreList.length) return
    return latestRequest(genreFlights, this, 'genres', async current => {
      try {
        const genres = await api.get('/api/anilist/genres')
        if (current()) this.exploreGenreList = genres || []
      } catch {
        if (current()) this.exploreGenreList = []
      }
    })
  },
}
