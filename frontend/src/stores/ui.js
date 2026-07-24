import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { isNative, send as nativeSend } from '@/lib/nativeBridge'
import { vtGo } from '@/lib/vt'
import { useAnimeStore } from './anime'
import { useMangaStore } from './manga'

const SESSION_KEY = 'animanga:session:v2'
let _navInit = false
let _applying = false   // true while applying a popstate, so we don't push new history

function loadSession() {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY) || '{}') } catch { return {} }
}

/* `history.pushState` serializa con *structured clone*, y eso LANZA con los proxies reactivos de
 * Vue: `snapshot()` devuelve `modeHome` (estado del store) y `manga.source_meta` (objeto anidado
 * del store), y ambos son proxies. Resultado: `pushState` fallaba SIEMPRE, el `catch {}` se lo
 * comía, y el historial nunca tuvo entradas → atrás/adelante (ratón, Alt+←, botones) no hacían
 * nada, desde siempre.
 *
 * El round-trip por JSON los aplana a objetos corrientes. Es exactamente lo que ya hacía
 * `persist()` — por eso el restaurar-al-recargar sí funcionaba y el historial no: mismo dato,
 * dos serializadores, y sólo uno se moría callado. Ver [[feedback_measurement_traps]]. */
export function plainState(obj) {
  try { return JSON.parse(JSON.stringify(obj)) } catch { return {} }
}

// Anime items map to currentView 'anime' + an anime sub-view (like the original sidebar).
// Manga se agrupa por intención (como el anime): 'library' = tu contenido
// (Descargados/Locales + Importar), 'explore' = buscar online (MangaDex/Fuentes
// en pestañas). 'activity' es transversal (manga+anime) → grupo General.
// La app se divide en dos MODOS que reconfiguran el sidebar entero (mismo binario, dos
// experiencias): `anime` (anime/manga/manwhas/novelas — la identidad original) y `cine`
// (series y películas occidentales). Cada grupo declara a qué modo pertenece; `both` sale en
// los dos (infra transversal: Descargas es un único qBittorrent, Actividad cruza todo). El
// conmutador vive en la cabecera del sidebar; nada del contenido cambia, solo QUÉ se muestra.
export const VIEWS = [
  {
    group: 'Manga', mode: 'anime',
    items: [
      { id: 'library',  label: 'Biblioteca', icon: 'library' },
      { id: 'discover', label: 'Descubrir',  icon: 'search' },
      { id: 'explore',  label: 'Explorar',   icon: 'globe' },
    ],
  },
  {
    group: 'Anime', mode: 'anime',
    items: [
      { id: 'anime', sub: 'library',   label: 'Mi Anime',     icon: 'film' },
      { id: 'anime', sub: 'search',    label: 'Buscar Anime', icon: 'search' },
      { id: 'anime', sub: 'explore',   label: 'Explorar',     icon: 'globe' },
      { id: 'anime', sub: 'seasonal',  label: 'Temporada',    icon: 'spark' },
    ],
  },
  {
    group: 'Series y Películas', mode: 'cine',
    items: [
      { id: 'media', sub: 'library',   label: 'Mi Biblioteca', icon: 'film' },
      { id: 'media', sub: 'search',    label: 'Buscar',        icon: 'search' },
      { id: 'media', sub: 'discover',  label: 'Descubrir',     icon: 'globe' },
    ],
  },
  {
    group: 'General', mode: 'both',
    items: [
      // Descargas es UNA sola: todos los torrents pasan por el mismo qBittorrent (anime,
      // series y películas se separan por categoría, no por cliente), así que tener una vista
      // por sección mostraba trozos de la misma lista. Vive en General por eso.
      { id: 'anime', sub: 'downloads', label: 'Descargas', icon: 'download' },
      { id: 'activity', label: 'Actividad', icon: 'spark' },
    ],
  },
]

export const MODES = [
  { id: 'anime', label: 'アニメ' },
  { id: 'cine',  label: 'Cine' },
]
// A qué modo "pertenece" una vista de nivel superior, para que un deep-link o atrás/adelante
// deje el conmutador coherente con lo que se ve. Las transversales (activity/settings/workshop)
// no fuerzan modo: se ven bajo cualquiera de los dos.
const VIEW_MODE = { library: 'anime', discover: 'anime', explore: 'anime', anime: 'anime', media: 'cine' }
function _modeOf(view) { return VIEW_MODE[view] || null }
// Vista por defecto de cada modo (a dónde aterrizas al conmutar la primera vez).
const MODE_DEFAULT = { anime: 'library', cine: 'media' }

// Vistas alcanzables SIN ítem propio en el sidebar: 'settings' (engranaje) y
// 'workshop' (botón Importar dentro de Biblioteca).
// 'kitchen' = cocina del sistema de diseño (se abre desde Ajustes; no ocupa sitio en el sidebar).
const EXTRA_VIEWS = ['settings', 'workshop', 'kitchen']
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
    // El modo lo dicta la vista restaurada (para que un deep-link a series abra ya en Cine); si la
    // vista es transversal, cae a lo último guardado, y si no, a anime (la identidad original).
    const view = _normView(s.view)
    const mode = _modeOf(view) || (s.mode === 'cine' ? 'cine' : 'anime')
    return {
      mode,
      // Última vista visitada por modo → al conmutar vuelves donde estabas, no al inicio.
      modeHome: { anime: MODE_DEFAULT.anime, cine: MODE_DEFAULT.cine, ...(s.modeHome || {}) },
      currentView: view,
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
      // Diálogo de confirmación propio (sustituye a confirm() del SO, que rompía la estética
      // en el momento más delicado). null | { title, body, confirmLabel, danger, _resolve }.
      confirmDlg: null,
    }
  },
  actions: {
    // Serializable snapshot of where the user is — drives both browser history
    // (back/forward) and the F5 session restore.
    snapshot() {
      let sub = '', animeDetail = null, manga = null
      try { const a = useAnimeStore(); sub = a.sub; animeDetail = a.detailId || null } catch {}
      // La sub-vista de Series y Películas también viaja en el historial; si no, atrás/adelante
      // devolvería la sección con la pestaña equivocada. Se lee de localStorage y NO importando
      // el store: `stores/media.js` ya importa este módulo, y el import cruzado sería un ciclo.
      const mediaSub = localStorage.getItem('media-sub') || 'library'
      try {
        const m = useMangaStore()
        if (m.current) manga = {
          id: m.current.id, name: m.current.name, cover: m.current.cover,
          source_meta: m.current.source_meta, mdId: m.mdId, mdOnly: !!m.current.mdOnly,
        }
      } catch {}
      return { view: this.currentView, sub, mediaSub, animeDetail, manga, mode: this.mode, modeHome: this.modeHome, sidebarCollapsed: this.sidebarCollapsed }
    },

    // ── Modo (アニメ ⇄ Cine) ───────────────────────────────────────────────────
    // Pinta la clase de tema en <html>: `mode-cine` remapea el acento a indigo sin tocar
    // ningún componente (todos derivan de --accent/--azure). Idempotente.
    _applyModeTheme() {
      try { document.documentElement.classList.toggle('mode-cine', this.mode === 'cine') } catch {}
    },
    // Conmuta de modo y navega a donde estabas por última vez en ese modo (o su vista por
    // defecto). No toca ninguna vista ni store de contenido: solo reconfigura la navegación.
    switchMode(m) {
      if (m !== 'anime' && m !== 'cine') return
      if (m === this.mode) return
      this.mode = m
      this._applyModeTheme()
      this.goto(this.modeHome[m] || MODE_DEFAULT[m])
    },
    // Recuerda la última vista de un modo (la llama el sidebar al pulsar un ítem de ese modo).
    setModeHome(mode, view) {
      if (mode === 'anime' || mode === 'cine') { this.modeHome = { ...this.modeHome, [mode]: view }; this.persist() }
    },

    initNav() {
      if (_navInit) return
      _navInit = true
      this._applyModeTheme()
      this._histState('replaceState')
      window.addEventListener('popstate', (e) => this._apply(e.state || {}))
    },

    // Apply a history snapshot (a back/forward landing) to the app. Reopens or closes
    // the anime detail and manga modal to match — so back/forward returns you exactly
    // where you were. Never pushes new history (guarded by _applying).
    _apply(st) {
      _applying = true
      try {
        this.currentView = _normView(st.view)
        // El modo sigue a la vista restaurada; si es transversal, respeta el que traía el snapshot.
        this.mode = _modeOf(this.currentView) || (st.mode === 'cine' ? 'cine' : 'anime')
        if (st.modeHome) this.modeHome = { ...this.modeHome, ...st.modeHome }
        this._applyModeTheme()
        try {
          const a = useAnimeStore()
          if (st.sub) a.sub = st.sub
          a.detailId = st.animeDetail || null
          if (a.torrentAnime) a.torrentAnime = null   // transient search view — close on any nav
          if (a.detailId) { const d = a.detail; if (d?.al_id) { a.loadTags(d); a.loadRecs(d) } }
        } catch {}
        if (st.mediaSub) localStorage.setItem('media-sub', st.mediaSub)
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
    /* Escribe el estado en el historial. Un `catch {}` mudo aquí ya costó que atrás/adelante no
       funcionase NUNCA sin que nada lo dijera: ahora un fallo se avisa por consola (regla del
       repo: "falló" y "no había" no pueden ser lo mismo). */
    _histState(method) {
      try {
        history[method](plainState(this.snapshot()), '')
      } catch (e) {
        console.warn(`[nav] history.${method} falló — atrás/adelante no funcionará`, e)
      }
    },
    pushNav() {
      this.initNav()
      if (_applying) return
      this._histState('pushState')
      this.persist()
      window.scrollTo({ top: 0 })
    },
    // Replace the current entry in place (state changed but it's not a new "page").
    replaceNav() {
      this.initNav()
      if (_applying) return
      this._histState('replaceState')
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
      // Envolver el cambio de sección en una View Transition (crossfade + morph de
      // elementos compartidos). vtGo cae a mutación directa si no hay soporte.
      vtGo(() => {
        if (VALID.has(v)) this.currentView = v
        this.sidebarMobileOpen = false
        this.activityOpen = false   // navigating away closes the activity drawer
        // switching to a top-level view closes any open detail/modal
        try { const a = useAnimeStore(); a.detailId = null; a.torrentAnime = null } catch {}
        try { const m = useMangaStore(); m.current = null } catch {}
        this.pushNav()
      })
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

    // Confirmación con la estética de la app. Devuelve Promise<boolean>.
    // body admite \n (se pintan como párrafos). danger=true → botón coral.
    confirm({ title = '¿Seguro?', body = '', confirmLabel = 'Confirmar', cancelLabel = 'Cancelar', danger = false } = {}) {
      return new Promise((resolve) => {
        this.confirmDlg = { title, body, confirmLabel, cancelLabel, danger, _resolve: resolve }
      })
    },
    resolveConfirm(ok) {
      const d = this.confirmDlg
      this.confirmDlg = null
      d?._resolve?.(!!ok)
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
