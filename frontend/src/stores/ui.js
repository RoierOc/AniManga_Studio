import { defineStore } from 'pinia'
import { useAnimeStore } from './anime'
import { useMangaStore } from './manga'

const SESSION_KEY = 'animanga:session:v2'
let _navInit = false
let _applying = false   // true while applying a popstate, so we don't push new history

function loadSession() {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY) || '{}') } catch { return {} }
}

// Anime items map to currentView 'anime' + an anime sub-view (like the original sidebar).
export const VIEWS = [
  {
    group: 'Manga',
    items: [
      { id: 'library',  label: 'Biblioteca', icon: 'library' },
      { id: 'mangadex', label: 'MangaDex',   icon: 'search' },
      { id: 'followed', label: 'Seguidos',   icon: 'heart' },
      { id: 'sources',  label: 'Fuentes',    icon: 'globe' },
      { id: 'local',    label: 'Local',      icon: 'folder' },
    ],
  },
  {
    group: 'Anime',
    items: [
      { id: 'anime', sub: 'library',   label: 'Mi Anime',     icon: 'film' },
      { id: 'anime', sub: 'search',    label: 'Buscar Anime', icon: 'search' },
      { id: 'anime', sub: 'seasonal',  label: 'Temporada',    icon: 'spark' },
      { id: 'anime', sub: 'downloads', label: 'Descargas',    icon: 'download' },
    ],
  },
]

const VALID = new Set(VIEWS.flatMap(g => g.items.map(i => i.id)))

export const useUiStore = defineStore('ui', {
  state: () => {
    const s = loadSession()
    return {
      currentView: VALID.has(s.view) ? s.view : 'library',
      sidebarCollapsed: s.sidebarCollapsed ?? false,
      sidebarMobileOpen: false,
      showShortcuts: false,
      toasts: [],
      _toastSeq: 0,
    }
  },
  actions: {
    // Serializable snapshot of where the user is — drives both browser history
    // (back/forward) and the F5 session restore.
    snapshot() {
      let sub = '', animeDetail = null, manga = null
      try { const a = useAnimeStore(); sub = a.sub; animeDetail = a.detailId || null } catch {}
      try {
        const m = useMangaStore()
        if (m.current) manga = {
          id: m.current.id, name: m.current.name, cover: m.current.cover,
          source_meta: m.current.source_meta, mdId: m.mdId, mdOnly: !!m.current.mdOnly,
        }
      } catch {}
      return { view: this.currentView, sub, animeDetail, manga, sidebarCollapsed: this.sidebarCollapsed }
    },

    _initNav() {
      if (_navInit) return
      _navInit = true
      try { history.replaceState(this.snapshot(), '') } catch {}
      window.addEventListener('popstate', (e) => this._apply(e.state || {}))
    },

    // Apply a history snapshot (a back/forward landing) to the app. Reopens or closes
    // the anime detail and manga modal to match — so back/forward returns you exactly
    // where you were. Never pushes new history (guarded by _applying).
    _apply(st) {
      _applying = true
      try {
        this.currentView = VALID.has(st.view) ? st.view : 'library'
        try {
          const a = useAnimeStore()
          if (st.sub) a.sub = st.sub
          a.detailId = st.animeDetail || null
          if (a.torrentAnime) a.torrentAnime = null   // transient search view — close on any nav
          if (a.detailId) { const d = a.detail; if (d?.al_id) { a.loadTags(d); a.loadRecs(d) } }
        } catch {}
        try {
          const m = useMangaStore()
          if (st.manga) { if (!m.current || m.current.id !== st.manga.id) m.open(st.manga, { fromHistory: true }) }
          else if (m.current) m.current = null
        } catch {}
      } finally {
        _applying = false
        this.persist()
      }
    },

    // Push the current location as a new browser-history entry.
    pushNav() {
      this._initNav()
      if (_applying) return
      try { history.pushState(this.snapshot(), '') } catch {}
      this.persist()
    },
    // Replace the current entry in place (state changed but it's not a new "page").
    replaceNav() {
      this._initNav()
      if (_applying) return
      try { history.replaceState(this.snapshot(), '') } catch {}
      this.persist()
    },
    // Go back — used by close/Volver buttons so Forward can reopen what was closed.
    back() { try { window.history.back() } catch {} },

    goto(view) {
      if (VALID.has(view)) this.currentView = view
      this.sidebarMobileOpen = false
      // switching to a top-level view closes any open detail/modal
      try { const a = useAnimeStore(); a.detailId = null; a.torrentAnime = null } catch {}
      try { const m = useMangaStore(); m.current = null } catch {}
      this.pushNav()
    },

    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
      this.replaceNav()
    },

    // Persist the real current location (survives F5, like a normal web app).
    persist() {
      try { localStorage.setItem(SESSION_KEY, JSON.stringify(this.snapshot())) } catch {}
    },

    toast(message, type = 'info', ms = 3600) {
      const id = ++this._toastSeq
      this.toasts.push({ id, message, type })
      if (ms) setTimeout(() => this.dismissToast(id), ms)
      return id
    },
    dismissToast(id) {
      this.toasts = this.toasts.filter(t => t.id !== id)
    },
  },
})
