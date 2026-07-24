import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'

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
    // Ejecución
    batchId: '',
    batch: null,          // agregado de GET /<id>
    running: false,
  }),

  getters: {
    // Un episodio es elegible si tiene archivo y aún no está en español (o se fuerza).
    processable: (s) => s.episodes.filter(e => e.file_present && e.recommended === 'process'),
    itemByEp: (s) => (ep) => s.items.find(i => i.episode === ep),
    // Estado en vivo por episodio durante la ejecución.
    liveOf: (s) => (ep) => s.batch?.items?.find(i => i.episode === ep) || null,
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
      this.batchId = ''
      this.batch = null
      this.running = false
      this.runScan()
    },

    close() {
      this.open = false
      if (poller) { clearInterval(poller); poller = null }
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
    setPolicy(p) { this.policy = p },

    async start() {
      const ui = useUiStore()
      const chosen = this.items.filter(i => this.selected.has(i.episode))
      if (!chosen.length) { ui.toast('Selecciona al menos un episodio', 'warn'); return }
      try {
        const d = await api.post('/api/subtitle/batch/start', { items: chosen, policy: this.policy, title: this.title })
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
            if (b.status === 'cancelled') ui.toast('Lote cancelado', 'info')
            else ui.toast(`Lote terminado: ${done} listos (${prem} premade, ${ia} IA)${err ? `, ${err} sin fuente` : ''}`, 'ok', 8000)
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
