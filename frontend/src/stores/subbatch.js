import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'

/* Traducción/búsqueda de subtítulos POR LOTES (anime + series/pelis).
 *
 * El backend hace todo el trabajo (escanea idiomas, orquesta en serie premade→IA). Este store solo
 * gobierna el modal: recibe los `items` (episodios con cómo resolver su vídeo + títulos) de quien
 * lo abre, escanea, deja elegir alcance/política y lanza el lote, sondeando el progreso.
 */
let poller = null

export const useSubBatchStore = defineStore('subbatch', {
  state: () => ({
    open: false,
    title: '',
    items: [],            // items originales (con path/info_hash/anime_id/local_path/titles/season)
    scanning: false,
    episodes: [],         // resultado de /scan: [{episode, es_status, source_langs, recommended, …}]
    summary: null,
    selected: new Set(),  // episodios (num) elegidos para procesar
    policy: 'buscar_o_traducir',
    // Reinyectar: rehacer episodios que YA tienen español (el anterior salió mal). Sin esto el
    // worker los salta con «ya tiene español» y no hay forma de repetirlos desde la UI.
    force: false,
    // Fuente elegida A MANO por episodio: { [ep]: {kind:'premade'|'track'|'external', …} }.
    // Con elección explícita el worker no aplica la política ni cae a otra fuente si falla.
    choices: {},
    picking: 0,           // episodio cuyo selector se está abriendo (o 0)
    // Ejecución
    batchId: '',
    batch: null,          // agregado de GET /<id>
    running: false,
  }),

  getters: {
    // Un episodio es elegible si tiene archivo y aún no está en español (o se fuerza).
    processable: (s) => s.episodes.filter(e => e.file_present && e.recommended === 'process'),
    // Rehacer sólo tiene sentido sobre NUESTRO sidecar: el ES incrustado es el oficial del grupo.
    redoable: (s) => s.episodes.filter(e => e.file_present && e.es_status === 'sidecar'),
    itemByEp: (s) => (ep) => s.items.find(i => i.episode === ep),
    // Estado en vivo por episodio durante la ejecución.
    liveOf: (s) => (ep) => s.batch?.items?.find(i => i.episode === ep) || null,
    // Al REABRIR un lote en marcha no hay escaneo, así que la lista sale del propio lote. El
    // escaneo trae más (idioma detectado, fuentes), pero para mirar el progreso basta con esto.
    filas: (s) => (s.episodes.length ? s.episodes
      : (s.batch?.items || []).map(i => ({ ...i, file_present: true }))),
    // Progreso REAL: episodios terminados + la fracción del que está en curso.
    pctLote: (s) => {
      const b = s.batch
      if (!b || !b.total) return 0
      const frac = Math.min(100, b.current_progress || 0) / 100
      return Math.min(100, ((b.done || 0) + frac) / b.total * 100)
    },
  },

  actions: {
    openFor({ title, items }) {
      this.open = true
      this.title = title || ''
      this.items = items || []
      this.episodes = []
      this.summary = null
      this.selected = new Set()
      this.policy = 'buscar_o_traducir'
      this.force = false
      this.choices = {}
      this.batchId = ''
      this.batch = null
      this.running = false
      this.runScan()
    },

    close() {
      this.open = false
      if (poller) { clearInterval(poller); poller = null }
    },

    /** Vuelve a ABRIR un lote que ya está corriendo, sin re-escanear ni arrancar otro.
     *
     * `openFor` no vale para esto: resetea `batchId`/`batch` y lanza un escaneo nuevo, así que
     * cerrar el modal con un lote en marcha dejaba la única vista detallada del progreso
     * inaccesible durante los 40 minutos que dura. Se entra desde el Centro de Actividad. */
    reopen(batchId, title) {
      if (!batchId) return
      this.open = true
      this.title = title || this.title
      this.batchId = batchId
      this.running = true
      this._poll()
    },

    async runScan() {
      if (!this.items.length) return
      this.scanning = true
      try {
        const d = await api.post('/api/subtitle/batch/scan', { items: this.items })
        this.episodes = d.episodes || []
        this.summary = d.summary || null
        // Preselección sensata: todo lo que la política por defecto procesaría.
        this.selected = new Set(this.processable.map(e => e.episode))
      } catch (_) {
        useUiStore().toast('No se pudo escanear los subtítulos', 'error')
        this.episodes = []
      } finally { this.scanning = false }
    },

    toggle(ep) {
      const s = new Set(this.selected)
      s.has(ep) ? s.delete(ep) : s.add(ep)
      this.selected = s
    },
    selectAll() { this.selected = new Set(this.episodes.filter(e => e.file_present).map(e => e.episode)) },
    selectNone() { this.selected = new Set() },
    selectProcess() { this.selected = new Set(this.processable.map(e => e.episode)) },
    // Rehacer los ya hechos: sin `force` el worker los saltaría, así que van juntos.
    selectRedo() { this.force = true; this.selected = new Set(this.redoable.map(e => e.episode)) },
    setPolicy(p) { this.policy = p },
    setForce(v) { this.force = !!v },

    /* Elegir A MANO la fuente de UN episodio, con el mismo selector del clic derecho → Traducir.
     * Se reutiliza `translateSubs` en modo `onPick`: en vez de lanzar la traducción, devuelve lo
     * elegido y aquí se apunta para el lote. Es una llamada de red por episodio (busca premade en
     * OpenSubtitles y compañía), por eso es bajo demanda y no parte del escaneo. */
    async pickSource(ep) {
      const item = this.itemByEp(ep)
      if (!item) return
      const anime = useAnimeStore()
      this.picking = ep
      try {
        await anime.translateSubs(
          { id: item.anime_id || '', title: this.title },
          {
            num: ep, season: item.season || 1, titles: item.titles || [],
            ep_type: item.ep_type || 'episode', info_hash: item.info_hash || '',
            local_path: item.local_path || item.path || '',
          },
          {
            onPick: (choice) => {
              this.choices = { ...this.choices, [ep]: choice }
              this.selected = new Set(this.selected).add(ep)   // elegir fuente = querer procesarlo
              anime.subTrackModal = null
            },
          },
        )
      } finally { this.picking = 0 }
    },

    clearChoice(ep) {
      const c = { ...this.choices }
      delete c[ep]
      this.choices = c
    },

    async start() {
      const ui = useUiStore()
      const chosen = this.items
        .filter(i => this.selected.has(i.episode))
        .map(i => (this.choices[i.episode] ? { ...i, choice: this.choices[i.episode] } : i))
      if (!chosen.length) { ui.toast('Selecciona al menos un episodio', 'warn'); return }
      try {
        const d = await api.post('/api/subtitle/batch/start',
          { items: chosen, policy: this.policy, title: this.title, force: this.force })
        if (d.error) { ui.toast(d.error, 'error'); return }
        this.batchId = d.batch_id
        this.running = true
        this._poll()
      } catch (e) {
        if (e?.status === 409) ui.toast('Ya hay un lote en curso', 'warn')
        else ui.toast('No se pudo iniciar el lote', 'error')
      }
    },

    _poll() {
      if (poller) clearInterval(poller)
      poller = setInterval(async () => {
        try {
          const b = await api.get(`/api/subtitle/batch/${this.batchId}`)
          this.batch = b
          if (b.status !== 'running') {
            clearInterval(poller); poller = null
            this.running = false
            const ui = useUiStore()
            const done = b.items.filter(i => i.status === 'done').length
            const prem = b.items.filter(i => i.method === 'premade').length
            const ia = b.items.filter(i => i.method === 'ia').length
            const err = b.items.filter(i => i.status === 'error').length
            if (b.status === 'interrupted') ui.toast(`El servidor se reinició durante el lote · ${done} episodios sí quedaron listos`, 'warn', 9000)
            else if (b.status === 'cancelled') ui.toast('Lote cancelado', 'info')
            else ui.toast(`Lote terminado: ${done} listos (${prem} premade, ${ia} IA)${err ? `, ${err} sin fuente` : ''}`, 'ok', 8000)
            // Una elección ya aplicada no debe repetirse sola en el siguiente lanzamiento; las de
            // los que fallaron se conservan para poder reintentar sin volver a elegir.
            for (const i of b.items) if (i.status === 'done') this.clearChoice(i.episode)
            // Re-escanea para reflejar los nuevos ES sin cerrar el modal.
            this.runScan()
          }
        } catch (_) { clearInterval(poller); poller = null; this.running = false }
      }, 1500)
    },

    async cancel(hard = false) {
      if (!this.batchId) return
      try { await api.post(`/api/subtitle/batch/${this.batchId}/cancel`, { hard }) } catch (_) { /* */ }
    },
  },
})
