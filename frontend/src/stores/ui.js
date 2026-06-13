import { defineStore } from 'pinia'
import { useAnimeStore } from './anime'

const SESSION_KEY = 'animanga:session:v2'
let _navStack = []
let _navInit = false

function saveHistory() {
  const s = {
    view: _navStack.length > 0 ? _navStack[_navStack.length - 1].view : 'library',
    sub: _navStack.length > 0 ? _navStack[_navStack.length - 1].sub : '',
    animeDetail: _navStack.length > 0 ? _navStack[_navStack.length - 1].animeDetail : null,
  }
  try { localStorage.setItem(SESSION_KEY, JSON.stringify(s)) } catch {}
}

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
    goto(view) {
      if (VALID.has(view)) this.currentView = view
      this.sidebarMobileOpen = false
      this.pushNav(view)
      this.persist()
    },
    pushNav(view, sub = '', animeDetail = null) {
      if (!_navInit) {
        _navInit = true
        history.replaceState({ pos: 0 }, '')
        window.addEventListener('popstate', (e) => {
          // Prefer a registered restore (e.g. close an open detail) over a raw view switch.
          if (_navStack.length > 0) {
            const prev = _navStack.pop()
            if (prev.restore) { prev.restore(); saveHistory(); return }
          }
          // Fallback: land on the view (+ anime sub) we're navigating back to.
          // Guard against stale/invalid views so we never strand on a placeholder.
          const st = e.state || {}
          this.currentView = VALID.has(st.view) ? st.view : 'library'
          if (this.currentView === 'anime' && st.sub) {
            try { useAnimeStore().sub = st.sub } catch {}
          }
          saveHistory()
        })
      }
      const v = VALID.has(view) ? view : this.currentView
      _navStack.push({ view: v, sub, animeDetail, restore: null })
      history.pushState({ pos: _navStack.length, view: v, sub }, '')
      saveHistory()
    },
    _setNavRestore(fn) {
      if (_navStack.length > 0) _navStack[_navStack.length - 1].restore = fn
    },
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
      this.persist()
    },
    persist() {
      saveHistory()
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
