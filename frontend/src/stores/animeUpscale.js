import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from './ui'

/* Hornear anime con Anime4K — la mitad de cliente de `api/anime_upscale.py`.
 *
 * Va en su propio store y no dentro de `anime.js` (2000 líneas) por la regla de documentación técnica: lo
 * nuevo, en su módulo.
 *
 * El sondeo se enciende SOLO mientras hay algo en marcha. Un `setInterval` permanente para una
 * cosa que se usa cuatro veces al mes es una petición cada 3 s durante toda la sesión.
 */

let sonda = null

export const useAnimeUpscaleStore = defineStore('animeUpscale', {
  state: () => ({
    disponible: true,
    motivo: '',
    actual: null,        // { id, nombre, estado, porcentaje, quedan, video, salida }
    cola: [],
    hechos: [],
    cadena: 'Mode A+A UL + Thin',
  }),

  getters: {
    // ¿Este episodio concreto está en la cola o en el horno? Lo pinta la tarjeta.
    trabajoDe: (s) => (path) => {
      if (!path) return null
      const todos = [s.actual, ...s.cola].filter(Boolean)
      return todos.find((t) => t.video === path || t.salida === path) || null
    },
    hayAlgo: (s) => !!s.actual || s.cola.length > 0,
  },

  actions: {
    async refrescar() {
      try {
        const d = await api.get('/api/anime/upscale/status')
        this.$patch(d)
        if (this.hayAlgo) this.sondear()
        else this.parar()
      } catch (_) { this.parar() }
    },

    sondear() {
      if (sonda) return
      sonda = setInterval(async () => {
        try {
          const d = await api.get('/api/anime/upscale/status')
          const antes = this.actual?.id
          this.$patch(d)
          // Al terminar uno, avisar y refrescar la lista: el episodio ahora apunta al horneado.
          if (antes && this.actual?.id !== antes) {
            const t = this.hechos.find((h) => h.id === antes)
            const ui = useUiStore()
            if (t?.estado === 'hecho') ui.toast(`Escalado listo: ${t.nombre}`, 'success')
            else if (t?.estado === 'error') ui.toast(`Falló el escalado: ${t.error || ''}`, 'error')
          }
          if (!this.hayAlgo) this.parar()
        } catch (_) { this.parar() }
      }, 3000)
    },

    parar() {
      if (sonda) { clearInterval(sonda); sonda = null }
    },

    async hornear(path) {
      const ui = useUiStore()
      try {
        await api.post('/api/anime/upscale/start', { path })
        ui.toast('En cola para escalar con Anime4K', 'success')
        await this.refrescar()
      } catch (e) { ui.toast(e?.message || 'No se pudo encolar', 'error') }
    },

    async cancelar(id) {
      try { await api.post('/api/anime/upscale/cancel', { id }) } catch (_) { /* ya no estaba */ }
      await this.refrescar()
    },

    async descartar(path) {
      const ui = useUiStore()
      try {
        await api.post('/api/anime/upscale/discard', { path })
        ui.toast('Versión escalada borrada; vuelve el original', 'success')
      } catch (e) { ui.toast(e?.message || 'No se pudo borrar', 'error') }
    },
  },
})
