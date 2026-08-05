import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { isNative, send as nativeSend } from '@/lib/nativeBridge'
import { vtGo } from '@/lib/vt'
import { useAnimeStore } from './anime'
import { useMangaStore } from './manga'
import { useMediaStore } from './media'
import { useNovelsStore } from './novels'

const SESSION_KEY = 'animanga:session:v2'
let _navInit = false
let _applying = false   // true while applying a popstate, so we don't push new history
let _scrollTimer = 0    // debounce del sellado de la posición de scroll en el historial
/* Ventanas en las que el scroll de la página NO representa dónde estaba el usuario, así que
 * sellarlo destruiría la memoria en vez de guardarla. Son dos, y las dos se medían mal:
 *   · al CAMBIAR de vista, la saliente se desmonta, la página encoge y el navegador te baja el
 *     scroll a 0 — el debounce de 150 ms grababa ese 0 sobre la posición buena de la entrada que
 *     estabas abandonando;
 *   · al ATERRIZAR de un atrás/adelante, el propio `scrollTo` del restaurador dispara eventos.
 * Sin esto, volver al Inicio a media página funcionaba… hasta que un aterrizaje llegaba tarde y
 * entonces la posición se perdía PARA SIEMPRE, no sólo esa vez. */
let _stampFrozen = false
let _freezeTimer = 0
function _freezeStamp(ms = 500) {
  _stampFrozen = true
  clearTimeout(_freezeTimer)
  _freezeTimer = setTimeout(() => { _stampFrozen = false }, ms)
}

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
  /* La Portada es la ÚNICA entrada sin grupo y visible en los dos modos: es la puerta de la app,
     no una sección más. Va primera porque es donde se aterriza. */
  {
    group: '', mode: 'both',
    items: [
      { id: 'home', label: 'Inicio', icon: 'home' },
    ],
  },
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
      { id: 'anime', sub: 'seasonal',  label: 'Temporada',    icon: 'sun' },
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
      { id: 'activity', label: 'Actividad', icon: 'refresh' },
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
// `home` NO va aquí a propósito: es transversal, así que no fuerza modo y se ve bajo los dos.
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
// Sin sesión previa (o con una vista que ya no existe) se aterriza en la PORTADA, no en la
// rejilla de manga: es la vista que cruza los cinco dominios y ofrece lo que ibas a retomar.
// Una sesión guardada conserva su vista, así que esto no mueve a nadie de sitio a media faena.
function _normView(v) { v = LEGACY_VIEWS[v] || v; return VALID.has(v) ? v : 'home' }

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
    /* Al ABRIR la app se aterriza SIEMPRE en la Portada, se estuviera donde se estuviera al
     * cerrar. Es una decisión del usuario (31-jul-2026): la Portada es la puerta, y quiere
     * cruzarla cada vez, no reanudar la sesión anterior a media faena.
     *
     * Lo demás de la sesión SÍ se conserva (modo, pestañas de segundo nivel, sidebar): sólo se
     * ignora la vista. Y atrás/adelante siguen funcionando dentro de la sesión, porque ahí manda
     * el historial (`_apply`), no esto. */
    const view = 'home'
    const mode = _modeOf(view) || (s.mode === 'cine' ? 'cine' : 'anime')
    return {
      mode,
      // Última vista visitada por modo → al conmutar vuelves donde estabas, no al inicio.
      modeHome: { anime: MODE_DEFAULT.anime, cine: MODE_DEFAULT.cine, ...(s.modeHome || {}) },
      currentView: view,
      /* Pestañas de segundo nivel. Vivían como `ref` + localStorage DENTRO de cada vista
         (LibraryHub, ExploreView, SettingsView, MediaStudio), y por eso atrás/adelante no podía
         devolverte a la que estabas: el historial no las veía, y aunque `_apply` reescribiera el
         localStorage el `ref` ya montado no se enteraba. Aquí son estado reactivo del store, así
         que viajan en el snapshot y se restauran de verdad. */
      tabs: {
        lib: 'downloaded',      // Biblioteca: Descargados | Locales
        exp: 'mangadex',        // Explorar: MangaDex | Fuentes
        set: 'general',         // Ajustes
        media: 'library',       // Series y Películas
        ...(s.tabs || {}),
      },
      sidebarCollapsed: s.sidebarCollapsed ?? false,
      sidebarMobileOpen: false,
      showShortcuts: false,
      /* Retrospectiva a pantalla completa («tu mes» / «tu año»). Es un overlay, no una vista:
         no ocupa sitio en el sidebar ni entra en el historial de navegación — se abre, se mira
         y se cierra. */
      showRetro: false,
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
      _navSeq: 0,   // sube en cada aterrizaje de atrás/adelante — ver back(fallback)
    }
  },
  actions: {
    // Serializable snapshot of where the user is — drives both browser history
    // (back/forward) and the F5 session restore.
    snapshot() {
      let sub = '', animeDetail = null, preview = null, manga = null, reader = null, mediaDetail = null
      try {
        const a = useAnimeStore()
        sub = a.sub
        animeDetail = a.detailId || null
        // Una ficha de anime que NO está en tu biblioteca (recomendación, temporada) se abre en
        // `previewAnime`, no en `detailId`. Empujaba entrada de historial pero no viajaba en el
        // snapshot: la entrada salía idéntica a la anterior, así que "atrás" parecía no hacer
        // nada y el segundo "atrás" te sacaba dos vistas de golpe.
        if (a.previewAnime) {
          const p = a.previewAnime
          preview = { al_id: p.al_id, id: p.id, title: p.title, cover: p.cover, format: p.format }
        }
      } catch {}
      try {
        const m = useMangaStore()
        if (m.current) manga = {
          id: m.current.id, name: m.current.name, cover: m.current.cover,
          source_meta: m.current.source_meta, mdId: m.mdId, mdOnly: !!m.current.mdOnly,
        }
        // Leer un capítulo es la página MÁS profunda de la app y no registraba nada: "atrás"
        // desde el lector te sacaba del lector Y del manga de una vez, aterrizando en la
        // biblioteca. Ahora es una entrada propia.
        if (m.reader) reader = { title: m.reader.title, chapter: m.reader.chapter, source: m.reader.source, kind: m.reader.kind }
      } catch {}
      // El item entero (lo aplana `plainState`): la ficha lo pinta directamente y buscarlo por id
      // en la biblioteca no siempre funciona (puede venir de una búsqueda, que no está cargada).
      try { const md = useMediaStore().detail; if (md) mediaDetail = md } catch {}
      let novel = null, novelReader = null
      try {
        const n = useNovelsStore()
        if (n.detail) novel = { ...n.detail }
        if (n.reader) novelReader = { ...n.reader, path: n.novel?.path || n.detail?.path || '' }
      } catch {}
      return { view: this.currentView, sub, animeDetail, preview, manga, reader, mediaDetail, novel, novelReader,
               tabs: this.tabs, mode: this.mode, modeHome: this.modeHome, sidebarCollapsed: this.sidebarCollapsed }
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
      // Restauramos el scroll a mano (ver _restoreScroll): el automático del navegador dispara
      // ANTES de que la vista restaurada haya pedido sus datos, así que siempre erraba.
      try { history.scrollRestoration = 'manual' } catch {}
      this._histState('replaceState')
      window.addEventListener('popstate', (e) => this._apply(e.state || {}))
      // Sella la posición MIENTRAS scrolleas, no solo al navegar. Sin esto, "adelante" siempre
      // aterrizaba arriba: la entrada que dejas atrás al retroceder nunca llegaba a guardar
      // dónde estabas, porque sólo se sellaba en el push (o sea, sólo hacia delante).
      window.addEventListener('scroll', () => {
        if (_applying) return
        clearTimeout(_scrollTimer)
        _scrollTimer = setTimeout(() => this._stampScroll(), 150)
      }, { passive: true })
    },

    // Guarda la posición actual EN la entrada de historial actual (sin crear una nueva).
    _stampScroll() {
      if (_stampFrozen) return
      try { history.replaceState({ ...(history.state || {}), scroll: window.scrollY }, '') } catch {}
    },

    /* Devuelve la página a `y` tras un atrás/adelante. El contenido de la vista llega por fetch,
     * así que en el primer frame la página aún no tiene altura y un scrollTo() se queda corto:
     * reintenta hasta llegar (o rendirse).
     * 12 frames (~200 ms) cubre las cargas normales; si una vista tarda más, aterrizas
     * arriba — observar el resize del documento no compensa la complejidad. */
    _restoreScroll(y) {
      // Volver a una entrada sellada ARRIBA es una posición como cualquier otra, no un "no hacer
      // nada": salir del Inicio a media página y darle a "adelante" te dejaba la ficha nueva
      // abierta a 438 px de scroll, con el hero cortado. Sin bucle de reintento porque el 0 no
      // depende de que el contenido haya cargado.
      if (!y) { window.scrollTo({ top: 0, behavior: 'instant' }); return }
      _freezeStamp(2000)                                // cubre el bucle entero de reintentos
      let tries = 90                                    // ~1,5 s: cubre la carga de una biblioteca
      const tick = () => {
        // `behavior:'instant'` explícito: restaurar una posición NUNCA debe animarse (se ve como
        // un salto raro) y, si alguien vuelve a poner scroll suave más arriba, una animación
        // pelearía con este bucle — que relanzaría el scrollTo a media animación sin llegar nunca.
        window.scrollTo({ top: y, behavior: 'instant' })
        if (--tries > 0 && Math.abs(window.scrollY - y) > 2) { requestAnimationFrame(tick); return }
        // Se suelta 200 ms después: el último scrollTo aún tiene su evento en cola, y sellarlo
        // sería justo el sello malo que la congelación existe para evitar.
        _freezeStamp(200)
      }
      requestAnimationFrame(tick)
    },

    // Apply a history snapshot (a back/forward landing) to the app. Reopens or closes
    // the anime detail and manga modal to match — so back/forward returns you exactly
    // where you were. Never pushes new history (guarded by _applying).
    _apply(st) {
      _applying = true
      this._navSeq++
      try {
        this.currentView = _normView(st.view)
        // El modo sigue a la vista restaurada; si es transversal, respeta el que traía el snapshot.
        this.mode = _modeOf(this.currentView) || (st.mode === 'cine' ? 'cine' : 'anime')
        if (st.modeHome) this.modeHome = { ...this.modeHome, ...st.modeHome }
        if (st.tabs) this.tabs = { ...this.tabs, ...st.tabs }
        this._applyModeTheme()
        try {
          const a = useAnimeStore()
          if (st.sub) a.sub = st.sub
          a.detailId = st.animeDetail || null
          if (a.torrentAnime) a.torrentAnime = null   // transient search view — close on any nav
          if (a.detailId) { const d = a.detail; if (d?.al_id) { a.loadTags(d); a.loadRecs(d) } }
          if (st.preview) { if (a.previewAnime?.al_id !== st.preview.al_id) a._openPreview(st.preview) }
          else if (a.previewAnime) a.previewAnime = null
        } catch {}
        try {
          const md = useMediaStore()
          if (st.mediaDetail) { if (md.detail?.id !== st.mediaDetail.id || md.detail?.kind !== st.mediaDetail.kind) md.detail = st.mediaDetail }
          else if (md.detail) md.detail = null
        } catch {}
        try {
          const m = useMangaStore()
          if (st.manga) { if (!m.current || m.current.id !== st.manga.id) m.open(st.manga, { fromHistory: true }) }
          else if (m.current) m.current = null
          // El lector: se cierra siempre que la entrada de destino no lo lleve, y se reabre si sí.
          // Reabrir sólo vale para un capítulo local — el online lee de URLs de un solo uso, así
          // que ahí "adelante" te deja en la ficha del manga, que es de donde saliste.
          const r = st.reader
          if (r && r.kind === 'manga' && r.source !== 'online') {
            if (String(m.reader?.chapter) !== String(r.chapter) || m.reader?.title !== r.title) m.read(r.chapter, r.source, r.title)
          } else if (m.reader) m.closeReader()
        } catch {}
        try {
          const n = useNovelsStore()
          const nr = st.novelReader
          if (nr) { if (n.reader?.novelId !== nr.novelId || n.reader?.chapterIndex !== nr.chapterIndex)
                      n.openReader({ id: nr.novelId, title: nr.title, novel: { pluginId: nr.pluginId, path: nr.path } }, nr.chapterIndex) }
          else if (n.reader) n.closeReader()
          if (st.novel) { if (n.detail?.novelId !== st.novel.novelId) n.openDetail({ id: st.novel.novelId, title: st.novel.title, cover: st.novel.cover, novel: st.novel }) }
          else if (n.detail) n.closeDetail()
        } catch {}
      } finally {
        _applying = false
        this.persist()
        this._restoreScroll(st.scroll || 0)
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
      // Sella la posición del scroll en la entrada que ABANDONAMOS. `history.state` todavía
      // describe la vista saliente (aún no hemos hecho push) y la página aún no se ha movido,
      // así que este es el único instante en que ambos datos son los correctos. Sin esto,
      // volver de un detalle a una biblioteca con 200 títulos te dejaba siempre arriba del todo.
      clearTimeout(_scrollTimer)
      this._stampScroll()
      this._histState('pushState')
      this.persist()
      window.scrollTo({ top: 0, behavior: 'instant' })
    },
    // Replace the current entry in place (state changed but it's not a new "page").
    replaceNav() {
      this.initNav()
      if (_applying) return
      this._histState('replaceState')
      this.persist()
    },
    /* Atrás — lo usan los botones Cerrar/Volver, para que "adelante" reabra lo que cerraste.
       `fallback` cubre el callejón sin salida: si recargas (F5) con un capítulo abierto, esa es la
       PRIMERA entrada del historial y `history.back()` no puede hacer nada; sin red de seguridad
       el botón de cerrar no cerraría. `_navSeq` distingue "no se movió" de "sí se movió", que no
       pueden ser lo mismo — cerrar a ciegas cerraría también el capítulo al que acabas de volver. */
    back(fallback = null) {
      const seq = this._navSeq
      try { window.history.back() } catch {}
      if (fallback) setTimeout(() => { if (this._navSeq === seq) fallback() }, 200)
    },

    /* Cambia una pestaña de segundo nivel Y la registra en el historial. Es una navegación como
       cualquier otra: si "atrás" no la deshace, el botón se salta la pestaña y te saca de la
       sección entera, que es justo lo que hacía. */
    setTab(key, val) {
      if (this.tabs[key] === val) return
      this.tabs = { ...this.tabs, [key]: val }
      this.pushNav()
    },

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

    /* `push: false` cambia de sección SIN dejar entrada propia en el historial. Lo necesita quien
       navega y acto seguido abre algo que ya empuja su propia entrada (Inicio → ficha de anime o
       de serie): con las dos, UN clic dejaba DOS entradas y el primer «atrás» aterrizaba en una
       sección intermedia por la que el usuario no había pasado. */
    goto(view, { push = true } = {}) {
      const v = LEGACY_VIEWS[view] || view
      /* Sella AQUÍ la posición de la vista que se abandona, antes de tocar nada. Un instante
         después la vista saliente se desmonta, la página encoge y el scroll cae solo a 0: el
         `_stampScroll` de `pushNav` llegaba a veces con la página ya encogida y guardaba ese 0.
         Era una carrera, así que fallaba una de cada tres o cuatro veces — de ahí que el Inicio
         "a veces" recordara dónde estabas. */
      clearTimeout(_scrollTimer)
      this._stampScroll()
      _freezeStamp()
      // Envolver el cambio de sección en una View Transition (crossfade + morph de
      // elementos compartidos). vtGo cae a mutación directa si no hay soporte.
      vtGo(() => {
        if (VALID.has(v)) this.currentView = v
        this.sidebarMobileOpen = false
        this.activityOpen = false   // navigating away closes the activity drawer
        // switching to a top-level view closes any open detail/modal
        try { const a = useAnimeStore(); a.detailId = null; a.torrentAnime = null } catch {}
        try { const m = useMangaStore(); m.current = null } catch {}
        if (push) this.pushNav()
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
