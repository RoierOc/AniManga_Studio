import { defineStore } from 'pinia'

const SESSION_KEY = 'animanga:session:v2'

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
      this.persist()
    },
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
      this.persist()
    },
    persist() {
      localStorage.setItem(SESSION_KEY, JSON.stringify({
        view: this.currentView,
        sidebarCollapsed: this.sidebarCollapsed,
      }))
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
