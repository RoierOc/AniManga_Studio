import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { isNative, send as nativeSend } from '@/lib/nativeBridge'
import { useAnimeStore } from './anime'
import { useMangaStore } from './manga'

const SESSION_KEY = 'animanga:session:v2'
let _navInit = false
let _applying = false   // true while applying a popstate, so we don't push new history

function loadSession() {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY) || '{}') } catch { return {} }
}

// Anime items map to currentView 'anime' + an anime sub-view (like the original sidebar).
// Manga se agrupa por intención (como el anime): 'library' = tu contenido
// (Descargados/Locales + Importar), 'explore' = buscar online (MangaDex/Fuentes
// en pestañas). 'activity' es transversal (manga+anime) → grupo General.
export const VIEWS = [
  {
    group: 'Manga',
    items: [
      { id: 'library', label: 'Biblioteca', icon: 'library' },
      { id: 'explore', label: 'Explorar',   icon: 'globe' },
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
  {
    group: 'General',
    items: [
      { id: 'activity', label: 'Actividad', icon: 'spark' },
    ],
  },
]

// Vistas alcanzables SIN ítem propio en el sidebar: 'settings' (engranaje) y
// 'workshop' (botón Importar dentro de Biblioteca).
const EXTRA_VIEWS = ['settings', 'workshop']
// Compatibilidad: sesiones/deep-links anteriores a la fusión de secciones.
const LEGACY_VIEWS = { mangadex: 'explore', sources: 'explore', local: 'library' }
const VALID = new Set([...VIEWS.flatMap(g => g.items.map(i => i.id)), ...EXTRA_VIEWS])
function _normView(v) { v = LEGACY_VIEWS[v] || v; return VALID.has(v) ? v : 'library' }

// El wrapper de api guarda el cuerpo del error como texto crudo en `e.body`;
// extrae el {error, retry_after} JSON del backend de la biblioteca oculta.
function _hiddenErr(e) {
  let error = e?.message || 'error', retry_after = 0
  try { const j = JSON.parse(e?.body || '{}'); if (j.error) error = j.error; if (j.retry_after) retry_after = j.retry_after } catch {}
  return { error, retry_after }
}

export const useUiStore = defineStore('ui', {
  state: () => {
    const s = loadSession()
    return {
      currentView: _normView(s.view),
      sidebarCollapsed: s.sidebarCollapsed ?? false,
      sidebarMobileOpen: false,
      showShortcuts: false,
      activityOpen: false,         // Activity drawer (slide-over) open
      activityTab: 'active',       // 'active' | 'history' — tab of the full Activity view
      // Biblioteca oculta: reflejo (solo en memoria) del modo del backend. NUNCA se
      // persiste ni se restaura de localStorage — se sincroniza desde el backend
      // (`refreshHiddenStatus`) al arrancar, y el backend siempre vuelve a normal
      // al reiniciar. `hiddenConfigured` = ya hay un código guardado.
      hiddenModeActive: false,
      hiddenConfigured: false,
      // Pantalla completa unificada (lector, F11). En la shell nativa va por la
      // ventana (verbo IPC 'fullscreen', borderless real); en navegador, API DOM.
      fullscreen: false,
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

    initNav() {
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
        this.currentView = _normView(st.view)
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
      this.initNav()
      if (_applying) return
      try { history.pushState(this.snapshot(), '') } catch {}
      this.persist()
      window.scrollTo({ top: 0 })
    },
    // Replace the current entry in place (state changed but it's not a new "page").
    replaceNav() {
      this.initNav()
      if (_applying) return
      try { history.replaceState(this.snapshot(), '') } catch {}
      this.persist()
    },
    // Go back — used by close/Volver buttons so Forward can reopen what was closed.
    back() { try { window.history.back() } catch {} },

    // ── Pantalla completa unificada ───────────────────────────────────────────
    // En la shell nativa la ventana entra en fullscreen borderless real (Rust);
    // en navegador usamos la API DOM. Un solo estado para lector/F11 → nunca se
    // queda "atascado" (al cerrar el capítulo se llama setFullscreen(false)).
    setFullscreen(on) {
      on = !!on
      this.fullscreen = on
      if (isNative()) { nativeSend('fullscreen', { on }); return }
      try {
        if (on) document.documentElement.requestFullscreen?.()
        else if (document.fullscreenElement) document.exitFullscreen?.()
      } catch {}
    },
    toggleFullscreen() { this.setFullscreen(!this.fullscreen) },
    // El usuario puede salir del fullscreen del navegador con Esc/F11 del SO →
    // App.vue engancha 'fullscreenchange' y sincroniza este flag.
    _syncFullscreen(on) { this.fullscreen = !!on },

    goto(view) {
      const v = LEGACY_VIEWS[view] || view
      if (VALID.has(v)) this.currentView = v
      this.sidebarMobileOpen = false
      this.activityOpen = false   // navigating away closes the activity drawer
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

    // ── Biblioteca oculta ─────────────────────────────────────────────────────
    // Sincroniza el estado del modo con el backend (fuente de verdad dentro del
    // proceso). Se llama al arrancar la app y tras abrir Ajustes.
    async refreshHiddenStatus() {
      try {
        const s = await api.get('/api/config/hidden/status')
        this.hiddenModeActive = !!s.active
        this.hiddenConfigured = !!s.configured
      } catch { /* backend caído: deja los valores actuales */ }
    },

    // Establece o cambia el código secreto. `current` sólo hace falta si ya había
    // uno configurado. Devuelve {ok} o {error} para que la vista pinte el mensaje.
    async setHiddenCode(code, current = '') {
      try {
        await api.post('/api/config/hidden/set-code', { code, current_code: current })
        this.hiddenConfigured = true
        return { ok: true }
      } catch (e) {
        return { ok: false, ..._hiddenErr(e) }
      }
    },

    // Alterna el modo oculto verificando el código. En éxito recarga la página:
    // así TODAS las vistas se re-piden contra la biblioteca correcta sin cablear
    // recargas por store, y el estado se re-sincroniza desde el backend al volver.
    async toggleHiddenMode(code) {
      try {
        const r = await api.post('/api/config/hidden/toggle', { code })
        this.hiddenModeActive = !!r.active
        // Recarga dura para repintar biblioteca/almacenamiento con la raíz activa.
        try { location.reload() } catch {}
        return { ok: true, active: !!r.active }
      } catch (e) {
        return { ok: false, ..._hiddenErr(e) }
      }
    },

    // action (optional): { label, fn } → renders a button (e.g. "Deshacer").
    toast(message, type = 'info', ms = 3600, action = null) {
      const id = ++this._toastSeq
      this.toasts.push({ id, message, type, action })
      if (ms) setTimeout(() => this.dismissToast(id), ms)
      return id
    },
    runToastAction(id) {
      const t = this.toasts.find(x => x.id === id)
      this.dismissToast(id)
      if (t?.action?.fn) t.action.fn()
    },
    dismissToast(id) {
      this.toasts = this.toasts.filter(t => t.id !== id)
    },
  },
})
